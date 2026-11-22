# v2 paraphrase wiring: a split's template cases plus the kept paraphrases of that split become
# one derived, hashed suite document that `run_suite.py --suite-path` runs unchanged. A
# paraphrase case is a deep copy of its source case (same key, same paraphrase_group), so the
# existing intent-stability column spans template and paraphrase phrasings of one intent.
# Spec: docs/specs/paraphrase-suite-wiring.md.
#
# No-text rule: no ValueError raised here quotes a question or a paraphrase; messages name
# ids, hashes and counts only (the paraphrase sets are sealed).

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from src.eval.suites.paraphrases import AXES, PERSONAS, SCHEMA, canonical_bytes
from src.eval.suites.v2_suite import content_hash

SPLITS = AXES  # ("dev", "validation"): H-F is never paraphrased.
_AXIS_KEYS = ("intent_axis", "template_axis", "persona_axis")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")


def _tag_values(tags: Sequence[str], prefix: str) -> list[str]:
    return [t[len(prefix):] for t in tags if t.startswith(prefix)]


def _check_suites(suites: Sequence[Mapping[str, Any]]) -> dict[str, tuple[Mapping[str, Any], str]]:
    """Validates the suites; returns case id -> (case record, its suite's split)."""
    names: set[Any] = set()
    by_id: dict[str, tuple[Mapping[str, Any], str]] = {}
    for i, suite in enumerate(suites):
        name = suite.get("name")
        s_split = suite.get("split")
        if s_split not in SPLITS:
            raise ValueError(f"suite {i} ({name}): split is not one of {SPLITS}")
        if name in names:
            raise ValueError(f"two suites share the name {name}")
        names.add(name)
        cases = suite.get("cases")
        if not isinstance(cases, list) or suite.get("content_hash") != content_hash(cases):
            raise ValueError(f"suite {name}: content_hash does not match its cases")
        for case in cases:
            cid = case.get("id")
            if _tag_values(case.get("tags") or [], "split:") != [s_split]:
                raise ValueError(f"suite {name}: case {cid} has a split tag other than "
                                 f"split:{s_split}")
            if cid in by_id:
                raise ValueError(f"case id {cid} appears twice across the suites")
            by_id[cid] = (case, s_split)
    return by_id


def _check_sets(paraphrase_sets: Sequence[Mapping[str, Any]]) -> None:
    hashes: set[str] = set()
    seen_ids: dict[str, str] = {}
    for i, pset in enumerate(paraphrase_sets):
        if pset.get("schema") != SCHEMA:
            raise ValueError(f"paraphrase set {i}: schema is not {SCHEMA}")
        digest = pset.get("content_hash")
        if not isinstance(digest, str) or _HEX64_RE.fullmatch(digest) is None:
            raise ValueError(f"paraphrase set {i}: content_hash is missing or not lowercase hex")
        body = {k: v for k, v in pset.items() if k != "content_hash"}
        if hashlib.sha256(canonical_bytes(body)).hexdigest() != digest:
            raise ValueError(f"paraphrase set {digest[:12]}: content_hash does not match its "
                             "content")
        if digest in hashes:
            raise ValueError(f"two paraphrase sets share the content_hash {digest[:12]}")
        hashes.add(digest)
        for p in pset.get("paraphrases") or []:
            pid = p.get("paraphrase_id")
            if pid in seen_ids and seen_ids[pid] != digest:
                raise ValueError(f"paraphrase_id {pid} appears in two paraphrase sets")
            seen_ids[pid] = digest


def _check_record(p: Mapping[str, Any], by_id: Mapping[str, tuple[Mapping[str, Any], str]],
                  digest: str) -> Mapping[str, Any]:
    """Validates a selected record; returns its source case record."""
    pid, cid, persona = p.get("paraphrase_id"), p.get("case_id"), p.get("persona")
    where = f"paraphrase set {digest[:12]}, record {pid}"
    if persona not in PERSONAS:
        raise ValueError(f"{where}: persona is not one of the {len(PERSONAS)} personas")
    if not isinstance(cid, str) or pid != f"{cid}:{persona}":
        raise ValueError(f"{where}: paraphrase_id is not case_id:persona")
    axes = [p.get(k) for k in _AXIS_KEYS]
    if any(a not in SPLITS for a in axes):
        raise ValueError(f"{where}: an axis is not one of {SPLITS}")
    want = "validation" if "validation" in axes else "dev"
    if p.get("split") != want:
        raise ValueError(f"{where}: split disagrees with its axes (expected {want})")
    if cid not in by_id:
        raise ValueError(f"{where}: case {cid} is in none of the suites")
    source, source_split = by_id[cid]
    intents = _tag_values(source.get("tags") or [], "intent:")
    intent_id = p.get("intent_id")
    if intents != [intent_id] or source.get("paraphrase_group") != intent_id:
        raise ValueError(f"{where}: intent_id differs from case {cid}'s intent tag or "
                         "paraphrase_group")
    if want == "dev" and source_split == "validation":
        raise ValueError(f"{where}: a dev paraphrase of the validation case {cid}")
    return source


def _paraphrase_case(source: Mapping[str, Any], p: Mapping[str, Any], digest: str,
                     name: str) -> dict[str, Any]:
    case = copy.deepcopy(dict(source))
    tags: list[str] = []
    for tag in source.get("tags") or []:
        if tag.startswith("split:"):
            tag = f"split:{p['split']}"
        elif tag.startswith("family:"):
            tag = "family:paraphrase"
        tags.append(tag)
    tags += [f"persona:{p['persona']}", f"paraphrase_of:{p['case_id']}",
             f"paraphrase_set:{digest}"]
    case.update(id=p["paraphrase_id"], question=p["paraphrase"], suite=name, tags=tags,
                paraphrase={"set": digest, "paraphrase_id": p["paraphrase_id"],
                            "case_id": p["case_id"], "persona": p["persona"],
                            "source_sha256": p["source_sha256"]})
    return case


def build_paraphrase_suite(
    split: str,
    suites: Sequence[Mapping[str, Any]],
    paraphrase_sets: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Builds a split's template cases plus its kept paraphrases as one suite document.

    Args:
        split: ``"dev"`` or ``"validation"``.
        suites: v2 suite documents as ``write_v2_suites`` writes them.
        paraphrase_sets: Paraphrase sets as ``load_paraphrase_set`` returns them.

    Returns:
        The suite document (name, version, split, n_cases, content_hash, inputs, counts,
        cases sorted by id). Independent of the order of ``suites`` and ``paraphrase_sets``.

    Raises:
        ValueError: On any input listed in the spec; the message names ids, hashes and
            counts, never a question or a paraphrase.
    """
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    if not suites:
        raise ValueError("no suites given")
    if not paraphrase_sets:
        raise ValueError("no paraphrase sets given")
    by_id = _check_suites(suites)
    if not any(s["split"] == split for s in suites):
        raise ValueError(f"no suite has split {split}")
    _check_sets(paraphrase_sets)

    name = f"xbrl_v2_{split}_paraphrased"
    cases: list[dict[str, Any]] = []
    for suite in suites:
        if suite["split"] == split:
            for record in suite["cases"]:
                cases.append(dict(copy.deepcopy(dict(record)), suite=name))
    n_template = len(cases)

    set_rows = []
    for pset in paraphrase_sets:
        digest = pset["content_hash"]
        n_selected = 0
        for p in pset.get("paraphrases") or []:
            if p.get("split") != split:
                continue
            source = _check_record(p, by_id, digest)
            cases.append(_paraphrase_case(source, p, digest, name))
            n_selected += 1
        set_rows.append({"content_hash": digest, "prompt_version": pset.get("prompt_version"),
                         "n_selected": n_selected})

    cases.sort(key=lambda c: c["id"])
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("the build has a duplicate case id")
    return {
        "name": name,
        "version": "1",
        "split": split,
        "n_cases": len(cases),
        "content_hash": content_hash(cases),
        "inputs": {
            "suites": sorted(({"name": s["name"], "split": s["split"],
                               "content_hash": s["content_hash"]} for s in suites),
                             key=lambda r: r["name"]),
            "paraphrase_sets": sorted(set_rows, key=lambda r: r["content_hash"]),
        },
        "counts": {"template": n_template, "paraphrase": len(cases) - n_template},
        "cases": cases,
    }


def write_paraphrase_suite(doc: Mapping[str, Any], path: str | Path) -> Path:
    """Writes a paraphrased suite document after checking its hash and count.

    Args:
        doc: A document from ``build_paraphrase_suite``.
        path: The output file.

    Returns:
        ``Path(path)``.

    Raises:
        ValueError: When the content hash or the case count does not match the cases.
    """
    cases = doc.get("cases")
    if not isinstance(cases, list) or doc.get("content_hash") != content_hash(cases):
        raise ValueError("the suite's content_hash does not match its cases")
    if doc.get("n_cases") != len(cases):
        raise ValueError(f"n_cases is {doc.get('n_cases')}, the suite has {len(cases)} cases")
    text = json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    out = Path(path)
    out.write_bytes(text.encode("utf-8"))
    return out
