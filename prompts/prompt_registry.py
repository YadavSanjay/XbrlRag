"""Prompt registry keyed by (stage, provider, series, version).

Design: docs/design/XbrlRag_LLM_Harness.md.

Templates live under ``prompts/templates/xbrl/<location>/<stage>/<version>/{system,user}.txt``
where ``<location>`` is either ``_base`` (the provider-neutral single source of
truth) or a ``<provider>/<series>`` override (e.g. ``anthropic/claude-4``).
``registry.yaml`` names, per stage, the default (provider, series, version) and
the resolution order — try each ``{provider}/{series}`` pattern, fall back to
``_base``; first location that has both templates for the requested version wins.

This lets us (a) hold multiple prompt versions side by side, (b) pin an earlier
version while pointing the model at a newer series for regression, and (c) author
provider-specific overrides without duplicating the shared prompts. The content
hash (over system+user text) remains the integrity stamp on each ``llm_call``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml

from prompts.prompts import XBRL_TEMPLATES_DIR, PromptTemplate

_XBRL_MANIFEST = Path(XBRL_TEMPLATES_DIR) / "registry.yaml"
# The eval graders' prompts (e.g. the eval_judge stage), kept apart from the query flow's.
EVAL_TEMPLATES_DIR = Path(__file__).parent / "templates" / "eval"
_EVAL_MANIFEST = EVAL_TEMPLATES_DIR / "registry.yaml"


@dataclass(frozen=True)
class ResolvedPrompt:
    """A concrete, provider-tagged prompt pair resolved from the registry."""

    stage: str
    provider: str
    series: str | None
    version: str
    ref: str  # resolved location, e.g. "_base/intent_router/v1"
    system: PromptTemplate
    user: PromptTemplate

    @property
    def system_text(self) -> str:
        return self.system.template

    @property
    def user_text(self) -> str:
        return self.user.template

    @property
    def content_hash(self) -> str:
        """Short SHA256 over system+user text — the integrity stamp (order-fixed)."""
        h = hashlib.sha256()
        h.update(self.system.template.encode("utf-8"))
        h.update(b"\0")
        h.update(self.user.template.encode("utf-8"))
        return h.hexdigest()[:12]

    def signature(self) -> dict:
        """``{templates, version, prompt_ref}`` stamped onto the ``llm_call`` event.

        ``templates`` carries the resolved refs (so provider/series/version is
        visible in the trace) and ``version`` is the content hash (so identical
        content groups together regardless of where it resolved from)."""
        return {
            "templates": [f"{self.ref}/system", f"{self.ref}/user"],
            "version": self.content_hash,
            "prompt_ref": self.ref,
        }


class PromptRegistry:
    """Resolves (stage, provider, series, version) -> :class:`ResolvedPrompt`."""

    def __init__(self, manifest_path: Path | str, templates_root: Path | str):
        self.templates_root = Path(templates_root)
        with open(manifest_path, encoding="utf-8") as f:
            self._manifest = yaml.safe_load(f) or {}
        self._stages: dict = self._manifest.get("stages", {})
        self._cache: dict = {}

    def stages(self) -> list[str]:
        return list(self._stages.keys())

    def resolve(
        self,
        stage: str,
        *,
        provider: str | None = None,
        series: str | None = None,
        version: str | None = None,
    ) -> ResolvedPrompt:
        if stage not in self._stages:
            raise KeyError(f"Unknown prompt stage {stage!r}; known: {list(self._stages)}")

        cfg = self._stages[stage]
        default = cfg.get("default", {}) or {}
        provider = provider or default.get("provider", "openai")
        series = series if series is not None else default.get("series")
        version = version or default.get("version", "v1")
        resolution = cfg.get("resolution", ["{provider}/{series}", "_base"])

        key = (stage, provider, series, version)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        for pattern in resolution:
            loc = pattern.format(provider=provider, series=series)
            sdir = self.templates_root / loc / stage / version
            sys_f = sdir / "system.txt"
            usr_f = sdir / "user.txt"
            if sys_f.exists() and usr_f.exists():
                ref = f"{loc}/{stage}/{version}"
                resolved = ResolvedPrompt(
                    stage=stage, provider=provider, series=series, version=version, ref=ref,
                    system=PromptTemplate(sys_f.read_text(encoding="utf-8"), name=f"{ref}/system"),
                    user=PromptTemplate(usr_f.read_text(encoding="utf-8"), name=f"{ref}/user"),
                )
                self._cache[key] = resolved
                return resolved

        raise FileNotFoundError(
            f"No prompt templates for stage={stage} provider={provider} "
            f"series={series} version={version} (searched {resolution} under {self.templates_root})"
        )

    def list_variants(self, stage: str) -> list[dict]:
        """Enumerate on-disk (provider, series, version) variants for *stage*.

        Used by the regression harness to build the A/B matrix from what exists.
        """
        variants: list[dict] = []
        for stage_dir in self.templates_root.glob(f"**/{stage}"):
            if not stage_dir.is_dir():
                continue
            loc = stage_dir.parent.relative_to(self.templates_root).as_posix()
            provider, series = self._split_loc(loc)
            for vdir in sorted(stage_dir.glob("v*")):
                if (vdir / "system.txt").exists() and (vdir / "user.txt").exists():
                    variants.append({
                        "provider": provider, "series": series,
                        "version": vdir.name, "location": loc,
                    })
        return variants

    @staticmethod
    def _split_loc(loc: str) -> tuple[str, str | None]:
        if loc == "_base":
            return ("_base", None)
        parts = loc.split("/")
        return (parts[0], parts[1] if len(parts) > 1 else None)


_default_registry: PromptRegistry | None = None


def get_xbrl_registry() -> PromptRegistry:
    """Process-wide default XBRL prompt registry (lazily constructed)."""
    global _default_registry
    if _default_registry is None:
        _default_registry = PromptRegistry(_XBRL_MANIFEST, XBRL_TEMPLATES_DIR)
    return _default_registry


_eval_registry: PromptRegistry | None = None


def get_eval_registry() -> PromptRegistry:
    """The process-wide registry of the eval graders' prompts (lazily constructed).

    Returns:
        The registry rooted at ``prompts/templates/eval/``.
    """
    global _eval_registry
    if _eval_registry is None:
        _eval_registry = PromptRegistry(_EVAL_MANIFEST, EVAL_TEMPLATES_DIR)
    return _eval_registry
