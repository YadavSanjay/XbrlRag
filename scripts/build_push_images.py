#!/usr/bin/env python3
"""Build, gate, and push XbrlRag's container images (the APP side of deploy).

Builds arag / arag-ui (and, on demand, arag-ingest) from THIS repo's Dockerfiles for a
target platform (default linux/arm64 — the Graviton estate default), SHA-tags them, runs
an import-smoke acceptance gate INSIDE the built `arag` serving image, and pushes to ECR.
The infra repo (aws-genai-iac) owns the downstream deploy — scripts/deploy_arag_bundle.py
uploads the app-tier compose bundle + renders deploy.env, and scripts/deploy_arag_ingest_image.py
publishes the ingest image pointer; the EC2 bootstrap pulls these images. The two repos meet
at the ECR registry: infra provisions the genai-dev/arag* repos, this pushes to them. Keeping
build+gate here (next to the Dockerfiles + the smoke test) is the clean boundary.

ECR image refs are:  <registry>/<namespace>/<repo>:<arch>-<sha>
  e.g.  123456789012.dkr.ecr.us-east-1.amazonaws.com/genai-dev/arag:arm64-abc1234
NOTE the <namespace> (default `genai-dev`): the repos are genai-dev/arag*, NOT bare arag*.
The three repo names are `arag` (serving: BFF + MCP), `arag-ui` (React + nginx), and
`arag-ingest` (the on-demand ingest runner). Each image has a DIFFERENT build context —
arag / arag-ingest build from the repo root, arag-ui builds from ui/ (its Dockerfile COPYs
package.json / nginx.conf relative to ui/). See docs/guides/xbrl-flow/10-packaging-images.md.

DEFAULT build set = the app-tier compose pair (arag + arag-ui) that deploy_arag_bundle.py
serves. arag-ingest is on a SEPARATE cadence (published by deploy_arag_ingest_image.py, not
the bundle), so it is opt-in: add `--component arag-ingest` to build/push it too.

The gate (CL-Q analogue of the qlp golden-PV gate) is the same import-smoke the docker CI
runs: it imports src.api.main + src.mcp_server.server INSIDE the built `arag` image and
asserts no heavy ML lib (torch/transformers/…) loaded — the slim serving image's defining
invariant. It is self-contained and deterministic: NO network, DB, corpus, models, or mounted
data (the serving image ships none). It is ON by default; pass --no-gate to skip it, but a
push without the gate is un-accepted.

Run:
    python scripts/build_push_images.py --dry-run                       # print the plan, touch nothing
    python scripts/build_push_images.py --no-push                       # build + gate locally, no push
    AWS_PROFILE=genai-tf python scripts/build_push_images.py --tag arm64-abc1234   # build -> gate -> push
    python scripts/build_push_images.py --component arag-ingest         # also build/push the ingest image
    python scripts/build_push_images.py --registry 123....amazonaws.com --tag arm64-abc1234   # pin the registry

On an x86 host the arm64 build + gate runs under QEMU emulation (slow); Docker Desktop bundles
the binfmt handlers. If ECR rejects buildx's OCI attestation manifest, the images are single-arch
here (--load) so no attestation is attached; a push failure on that front is not from this script.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# component -> (ECR repo name, Dockerfile, build context). The context DIFFERS per image:
# arag / arag-ingest COPY src/·configs/·scripts/ from the repo root ('.'); arag-ui's
# Dockerfile COPYs package.json / nginx.conf relative to ui/, so it MUST build from 'ui'.
COMPONENTS = {
    "arag": ("arag", "infra/docker/Dockerfile", "."),
    "arag-ui": ("arag-ui", "ui/Dockerfile", "ui"),
    "arag-ingest": ("arag-ingest", "infra/docker/Dockerfile.ingest", "."),
}
# Default = the app-tier compose pair (what deploy_arag_bundle.py serves). arag-ingest is on
# its own cadence (deploy_arag_ingest_image.py), so it is opt-in via --component arag-ingest.
DEFAULT_COMPONENTS = ["arag", "arag-ui"]

# The in-image acceptance: import the two serving entrypoints and assert the slim image stayed
# torch-free (mirrors the smoke test in .github/workflows/docker.yml). Self-contained: no
# network / DB / corpus / models / mounted data. Runs inside the built `arag` image.
GATE_PY = (
    "import importlib, sys; "
    "[importlib.import_module(m) for m in ('src.api.main', 'src.mcp_server.server')]; "
    "heavy = [h for h in ('torch','transformers','sentence_transformers','faiss','FlagEmbedding') "
    "if h in sys.modules]; "
    "assert not heavy, f'slim serving image unexpectedly loaded {heavy}'; "
    "print('gate OK: api+mcp import, no heavy ML libs')"
)
GATE_CMD = ["python", "-c", GATE_PY]


def _run(cmd: list[str], dry: bool, **kw: object) -> int:
    print("  $ " + " ".join(cmd))
    if dry:
        return 0
    return subprocess.run(cmd, **kw).returncode  # type: ignore[arg-type]


def _git_sha() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return out.stdout.strip() or "dev"


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--platform", default="linux/arm64", help="target platform (default %(default)s)")
    p.add_argument(
        "--namespace",
        default="genai-dev",
        help="ECR repo namespace (default %(default)s) — the repos are <namespace>/arag*",
    )
    p.add_argument(
        "--registry",
        default=None,
        help="ECR registry host <acct>.dkr.ecr.<region>.amazonaws.com; else resolved from STS on push.",
    )
    p.add_argument(
        "--tag", default=None, help="image tag (default <arch>-<git-sha>, e.g. arm64-abc1234)."
    )
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--profile", default=None, help="AWS profile for the STS registry-resolve + ECR login.")
    p.add_argument(
        "--component",
        action="append",
        choices=list(COMPONENTS),
        default=[],
        metavar="NAME",
        help="build only this component (repeatable; default: arag + arag-ui; add arag-ingest to include it).",
    )
    p.add_argument(
        "--no-gate",
        action="store_true",
        help="skip the in-image import-smoke gate; a push without it is un-accepted.",
    )
    p.add_argument("--no-push", action="store_true", help="build + gate only; do not push to ECR.")
    p.add_argument("--dry-run", action="store_true", help="print the plan; run nothing.")
    args = p.parse_args()

    components = args.component or list(DEFAULT_COMPONENTS)
    sha = _git_sha()
    arch = args.platform.rsplit("/", 1)[-1]  # linux/arm64 -> arm64
    tag = args.tag or f"{arch}-{sha}"
    dry = args.dry_run

    # Resolve the registry only when pushing: explicit --registry wins; else STS (or a dry-run placeholder).
    registry = args.registry
    if not args.no_push and not registry:
        if dry:
            registry = f"<acct>.dkr.ecr.{args.region}.amazonaws.com"
        else:
            import boto3

            session = boto3.Session(profile_name=args.profile, region_name=args.region)
            registry = f"{session.client('sts').get_caller_identity()['Account']}.dkr.ecr.{args.region}.amazonaws.com"

    def ref(component: str) -> str:
        repo = f"{args.namespace}/{COMPONENTS[component][0]}"
        return f"{registry}/{repo}:{tag}" if registry else f"{repo}:{tag}"

    print(f"platform={args.platform}  tag={tag}  registry={registry or '(local; --no-push)'}")
    print(f"components={components}  gate={'off' if args.no_gate else 'on'}  push={'off' if args.no_push else 'on'}")
    for c in components:
        _, dockerfile, context = COMPONENTS[c]
        print(f"  {c:12s} {dockerfile:30s} (ctx {context:3s}) -> {ref(c)}")

    # 1. Build each (single platform, --load so the gate can run it + push works from the local store).
    for c in components:
        _, dockerfile, context = COMPONENTS[c]
        cmd = [
            "docker", "buildx", "build", "--platform", args.platform, "--load",
            "-f", str(REPO_ROOT / dockerfile), "-t", ref(c),
            str(REPO_ROOT / context),  # arag/arag-ingest -> repo root; arag-ui -> ui/
        ]
        print(f"\n== build {c} ==")
        if (rc := _run(cmd, dry)) != 0:
            print(f"ERROR: build {c} failed (rc={rc})", file=sys.stderr)
            return rc

    # 2. Gate: run the import-smoke acceptance INSIDE the built `arag` serving image (no mounts).
    if not args.no_gate and "arag" in components:
        print("\n== import-smoke gate (inside arag serving image) ==")
        gate = [
            "docker", "run", "--rm", "--platform", args.platform, ref("arag"), *GATE_CMD,
        ]
        if (rc := _run(gate, dry)) != 0:
            print(
                f"GATE FAILED (rc={rc}) — NOT pushing. The serving image either failed to import "
                "src.api.main / src.mcp_server.server or pulled in a heavy ML lib (torch/…): the slim "
                "image must stay torch-free (it embeds over RAGINFER_URL). Fix requirements-serve.txt.",
                file=sys.stderr,
            )
            return rc
        print("  gate PASS")
    elif not args.no_gate:
        print("\n  (import-smoke gate skipped — 'arag' is not in the build set)")

    # 3. Push (ECR login, then docker push each).
    if args.no_push:
        print("\n--no-push: built + gated; nothing pushed.")
        return 0
    if not registry:
        print("ERROR: pushing needs a registry (--registry, or AWS creds for the STS resolve).", file=sys.stderr)
        return 2

    print("\n== ecr login + push ==")
    if dry:
        print(f"  $ aws ecr get-login-password --region {args.region} \\")
        print(f"      | docker login --username AWS --password-stdin {registry}")
    else:
        import base64

        import boto3

        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        token = session.client("ecr").get_authorization_token()["authorizationData"][0]["authorizationToken"]
        user, pwd = base64.b64decode(token).decode().split(":", 1)
        login = subprocess.run(
            ["docker", "login", "--username", user, "--password-stdin", registry], input=pwd, text=True
        )
        if login.returncode:
            print("ERROR: docker login to ECR failed", file=sys.stderr)
            return login.returncode
    for c in components:
        if (rc := _run(["docker", "push", ref(c)], dry)) != 0:
            print(f"ERROR: push {c} failed (rc={rc})", file=sys.stderr)
            return rc

    print(
        f"\nDone. Pushed {len(components)} image(s) at tag {tag}. Next: the infra-side "
        f"deploy_arag_bundle.py --tag {tag}" + (" (+ deploy_arag_ingest_image.py for the ingest pointer)")
        if not dry
        else "\nDry run complete; nothing built or pushed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
