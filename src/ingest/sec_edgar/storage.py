"""Filing storage abstraction for the ingest side (CL #3).

The XBRL ingest path (download -> canonicalize -> Arelle -> chunk/embed) is the
only part of the system that touches filing bytes on disk; query-time serving is
filesystem-free. This module puts one seam between that ingest I/O and the
underlying store so the same code runs against the local filesystem (deploy_mode
``local``) or S3 (deploy_mode ``aws``), selected by :func:`FilingStore.from_env`.

Keys are POSIX-style paths relative to the store root, mirroring today's on-disk
layout ``{TICKER}/{YEAR}/{FORM}/...`` (e.g. ``NVDA/2026/10-K/manifest.yaml``).
Local mode maps a key to ``root/key``; S3 mode maps it to
``s3://$XBRLRAG_S3_BUCKET/$XBRLRAG_S3_PREFIX/key``.

Two things are *not* a transparent swap and get explicit helpers:

* **Discovery** — populators walk the tree for ``manifest.yaml`` via
  ``Path.rglob``; S3 has no rglob, so :meth:`iter_manifest_keys` abstracts it.
* **Arelle** — Arelle is an external Java process that needs real local files;
  :meth:`localize_dir` yields a local directory (the real one in local mode, a
  temp download that is cleaned up in S3 mode).

See ``docs/design/XbrlRag_AWS_Deployment.md``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from src import PROJECT_ROOT
from src.config.deploy import storage_backend
from src.logging_config import get_logger

logger = get_logger(__name__)

MANIFEST_NAME = "manifest.yaml"

# The local-dev default when SEC_EDGAR_ROOT is unset: the repo's own data/ tree,
# derived from PROJECT_ROOT (the package location), never a host-specific literal.
DEFAULT_LOCAL_ROOT = f"{PROJECT_ROOT}/data/storage/sec_edgar"

# A "role" names one of the two filing trees the ingest pipeline uses. Local mode
# maps each to its own root (env-overridable); S3 mode co-locates both under one
# bucket as two prefixes: s3://$XBRLRAG_S3_BUCKET/$XBRLRAG_S3_PREFIX/<role>/... .
_ROLE_LOCAL_ENV = {
    "sec_edgar": "SEC_EDGAR_ROOT",
    "normalized": "XBRLRAG_NORMALIZED_ROOT",
}
_ROLE_LOCAL_DEFAULT = {
    "sec_edgar": DEFAULT_LOCAL_ROOT,
    "normalized": f"{PROJECT_ROOT}/data/storage/xbrl/normalized",
}


def default_local_root(role: str = "sec_edgar") -> str:
    """The built-in local-dev default for *role*, ignoring env.

    The single source for these literals. Modules that need a documented default
    for a CLI help string or class constant must call this rather than re-spelling
    the path — six copies is how one of them drifts.
    """
    return _ROLE_LOCAL_DEFAULT.get(role, DEFAULT_LOCAL_ROOT)


def local_root(role: str = "sec_edgar") -> str:
    """The local filesystem root for *role*, honouring its env var.

    Callers must resolve roots through this instead of reaching for the historical
    ``D:/…`` constants. Passing a hardcoded default explicitly into
    ``FilingStore.from_env(root=...)`` **overrides** the env var, and on Linux the
    Windows string is a *relative* path — so it resolves against the process cwd
    (``/app/D:/<repo>/data/storage/sec_edgar``), discovery finds zero
    filings, and the stage reports success having done nothing. That failure is
    silent by construction, which is why every entry point uses this helper.
    """
    env_var = _ROLE_LOCAL_ENV.get(role)
    from_env = os.environ.get(env_var) if env_var else None
    return from_env or _ROLE_LOCAL_DEFAULT.get(role, DEFAULT_LOCAL_ROOT)


def ensure_writable_root(root: str | Path, role: str = "normalized") -> None:
    """Fail early and legibly if a local *output* root is missing or read-only.

    Container mounts are the usual cause: the host directory is created with
    ``sudo`` (root-owned) while the image runs as uid 10001, so the first write
    dies several frames deep inside ``pathlib.mkdir`` with a bare
    ``FileNotFoundError`` naming a leaf directory — which reads like a missing
    input, not a permissions problem. Check up front and say which path, which
    uid, and what to run.
    """
    path = Path(root)
    uid = getattr(os, "getuid", lambda: "?")()
    hint = (
        f"sudo mkdir -p {path} && sudo chown -R {uid}:{uid} {path}"
        if uid != "?"
        else f"create {path} and make it writable"
    )
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PermissionError(
            f"{role} root {path} is not creatable by uid {uid}: {exc}. "
            f"If this is a container mount, on the host run: {hint}"
        ) from exc
    if not os.access(path, os.W_OK):
        raise PermissionError(
            f"{role} root {path} exists but is not writable by uid {uid}. "
            f"If this is a container mount, on the host run: {hint}"
        )


def _norm_key(key) -> str:
    """Normalize any path-ish value to a clean POSIX relative key."""
    return str(key).replace("\\", "/").strip("/")


class FilingStore(ABC):
    """Backend-agnostic byte store for downloaded filings, keyed by relative path."""

    # -- factory ----------------------------------------------------------
    @classmethod
    def from_env(
        cls, root: str | Path | None = None, role: str = "sec_edgar"
    ) -> FilingStore:
        """Build the store the current deploy mode selects.

        ``storage_backend()`` (``XBRLRAG_STORAGE_BACKEND`` override, else derived
        from ``XBRLRAG_DEPLOY_MODE``) decides local vs S3. ``role`` selects which
        filing tree — ``sec_edgar`` (raw downloads) or ``normalized`` (canonical
        HTML). ``root`` only applies to the local backend (overrides the role's
        default root).
        """
        if storage_backend() == "s3":
            return S3FilingStore.from_env(role=role)
        return LocalFilingStore.from_env(root, role=role)

    # -- interface --------------------------------------------------------
    @abstractmethod
    def write_bytes(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def read_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def iter_manifest_keys(self, prefix: str = "") -> Iterator[str]:
        """Yield store-relative keys of every ``manifest.yaml`` under ``prefix``."""

    @abstractmethod
    def localize_dir(self, prefix: str):
        """Context manager yielding a real local directory for ``prefix``.

        Local mode yields the real directory; S3 mode downloads it to a temp dir
        and removes it on exit. Use this to hand a filing dir to Arelle.
        """

    def write_text(self, key: str, text: str, encoding: str = "utf-8") -> None:
        self.write_bytes(key, text.encode(encoding))

    def read_text(self, key: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(key).decode(encoding)

    def local_path(self, key: str) -> Path | None:
        """Real local path for ``key`` if the backend has one, else ``None``.

        Local backend returns the concrete path (so unchanged local code can keep
        using ``Path`` semantics); S3 returns ``None`` — callers that need a real
        file in S3 mode must go through :meth:`localize_dir`.
        """
        return None


class LocalFilingStore(FilingStore):
    """Filesystem backend — a thin, behavior-preserving wrapper over ``pathlib``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    @classmethod
    def from_env(
        cls, root: str | Path | None = None, role: str = "sec_edgar"
    ) -> LocalFilingStore:
        if root is None:
            env_var = _ROLE_LOCAL_ENV.get(role, "SEC_EDGAR_ROOT")
            default = _ROLE_LOCAL_DEFAULT.get(role, DEFAULT_LOCAL_ROOT)
            root = os.environ.get(env_var, default)
        return cls(root)

    def path(self, key: str) -> Path:
        return self.root / _norm_key(key)

    def local_path(self, key: str) -> Path | None:
        return self.path(key)

    def write_bytes(self, key: str, data: bytes) -> None:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def write_text(self, key: str, text: str, encoding: str = "utf-8") -> None:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding=encoding)

    def read_bytes(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def read_text(self, key: str, encoding: str = "utf-8") -> str:
        return self.path(key).read_text(encoding=encoding)

    def exists(self, key: str) -> bool:
        return self.path(key).exists()

    def iter_manifest_keys(self, prefix: str = "") -> Iterator[str]:
        base = self.path(prefix) if prefix else self.root
        if not base.exists():
            return
        for p in base.rglob(MANIFEST_NAME):
            yield _norm_key(p.relative_to(self.root))

    @contextmanager
    def localize_dir(self, prefix: str):
        # Already local — hand back the real directory, nothing to clean up.
        yield self.path(prefix)


class S3FilingStore(FilingStore):
    """S3 backend. boto3 is imported lazily so the module stays importable (and
    the local path stays boto-free) when boto3 isn't installed."""

    def __init__(self, bucket: str, prefix: str = "", client=None) -> None:
        self.bucket = bucket
        self.prefix = _norm_key(prefix) if prefix else ""
        if client is None:
            import boto3  # lazy: only needed for the real S3 path

            client = boto3.client("s3")
        self._client = client

    @classmethod
    def from_env(cls, role: str = "sec_edgar") -> S3FilingStore:
        try:
            bucket = os.environ["XBRLRAG_S3_BUCKET"]
        except KeyError as exc:  # pragma: no cover - config error surfaced early
            raise RuntimeError(
                "deploy_mode=aws / storage_backend=s3 requires XBRLRAG_S3_BUCKET to be set"
            ) from exc
        # One bucket, two prefixes: <base>/<role>/... co-locates the raw
        # (sec_edgar) and canonical (normalized) trees of the same filing.
        base = os.environ.get("XBRLRAG_S3_PREFIX", "")
        prefix = f"{base}/{role}".strip("/") if base else role
        logger.info("S3FilingStore bucket=%s prefix=%s role=%s", bucket, prefix, role)
        return cls(bucket, prefix)

    def _obj_key(self, key: str) -> str:
        k = _norm_key(key)
        return f"{self.prefix}/{k}" if self.prefix else k

    def _strip_prefix(self, obj_key: str) -> str:
        k = _norm_key(obj_key)
        if self.prefix and k.startswith(self.prefix + "/"):
            return k[len(self.prefix) + 1 :]
        return k

    def write_bytes(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self.bucket, Key=self._obj_key(key), Body=data)

    def read_bytes(self, key: str) -> bytes:
        resp = self._client.get_object(Bucket=self.bucket, Key=self._obj_key(key))
        return resp["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._obj_key(key))
            return True
        except Exception as exc:  # noqa: BLE001 - classify 404 vs real error
            resp = getattr(exc, "response", None)
            code = None
            if isinstance(resp, dict):
                code = str(resp.get("Error", {}).get("Code", "")) or str(
                    resp.get("ResponseMetadata", {}).get("HTTPStatusCode", "")
                )
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def iter_manifest_keys(self, prefix: str = "") -> Iterator[str]:
        list_prefix = self._obj_key(prefix) if prefix else self.prefix
        token: str | None = None
        while True:
            kwargs = {"Bucket": self.bucket}
            if list_prefix:
                kwargs["Prefix"] = (
                    list_prefix if list_prefix.endswith("/") else list_prefix + "/"
                )
            if token:
                kwargs["ContinuationToken"] = token
            resp = self._client.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []) or []:
                obj_key = obj["Key"]
                if _norm_key(obj_key).endswith("/" + MANIFEST_NAME) or _norm_key(
                    obj_key
                ).endswith(MANIFEST_NAME):
                    yield self._strip_prefix(obj_key)
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")

    @contextmanager
    def localize_dir(self, prefix: str):
        tmp_root = Path(tempfile.mkdtemp(prefix="xbrlrag_filing_"))
        try:
            list_prefix = self._obj_key(prefix)
            token: str | None = None
            downloaded = 0
            while True:
                kwargs = {"Bucket": self.bucket, "Prefix": list_prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                resp = self._client.list_objects_v2(**kwargs)
                for obj in resp.get("Contents", []) or []:
                    obj_key = obj["Key"]
                    rel = self._strip_prefix(obj_key)
                    dest = tmp_root / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    body = self._client.get_object(Bucket=self.bucket, Key=obj_key)[
                        "Body"
                    ].read()
                    dest.write_bytes(body)
                    downloaded += 1
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
            logger.info(
                "localized %d object(s) for prefix=%s -> %s",
                downloaded,
                prefix,
                tmp_root,
            )
            yield tmp_root / _norm_key(prefix)
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)
