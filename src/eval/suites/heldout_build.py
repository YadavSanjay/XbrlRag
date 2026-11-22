# The held-out mode of the v2 suite build: H-F, the sealed held-out-filer suite, built blind
# from the quarantined database. A lexicon overlay for the held-out filers read from outside
# the repository, held-out sector tags for the named groups, the intents that involve a
# held-out filer, cases tagged split:heldout_filers, witness blocks left out, and output that
# reports counts only. Spec: docs/specs/hf-suite-build.md.

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from src.eval.keys.base import SecSource
from src.eval.keys.core import AnswerKey
from src.eval.keys.key_definitions import key_definition
from src.eval.keys.statement_witness import _covers, check_resolution_use
from src.eval.suites.intents import (
    Intent,
    Lexicon,
    Phrasebook,
    build_phrased_cases,
    intent_id,
    load_lexicon,
)
from src.eval.suites.v2_suite import (
    COMPOUND_SHAPES,
    GENERATED_SHAPES,
    FactStoreSource,
    IntentDroppedError,
    KeyedIntent,
    _case_record,
    _compound_record,
    _enumerate,
    _enumerate_compound,
    _eval_case,
    _intent_object,
    _intents_manifest,
    _Keyer,
    _MemoSecSource,
    _normalize_inventory,
    _normalize_universes,
    _validated_compound_quotas,
    _validated_quotas,
    _write,
    content_hash,
    order_candidates,
)
from src.eval.suites.v2_witness import KeyWitness
from src.retriever.xbrl.entity_sectors import build_groups

SPLIT = "heldout_filers"
SUITE = "heldout_hf"
COMPOUND_SUITE = "heldout_hf_compound"
DRAW_SCHEME = "xbrlrag-heldout-v2"
_OVERLAY_KEYS = frozenset({"companies", "period_ends"})
# Original annual reports; amendments and every other form are ignored (requirement 3).
_ORIGINAL_ANNUAL = frozenset({"10-K", "20-F", "40-F"})
_FILE_NAMES = {"heldout_filers": f"{SUITE}.json", "manifest": f"{SUITE}_manifest.json",
               "intents": f"{SUITE}_intents.json"}
_COMPOUND_FILE_NAMES = {"heldout_filers": f"{COMPOUND_SUITE}.json",
                        "manifest": f"{COMPOUND_SUITE}_manifest.json",
                        "intents": f"{COMPOUND_SUITE}_intents.json"}


# --------------------------------------------------------------------------- paths


def require_outside(path: str | Path, repo_root: str | Path) -> Path:
    """Returns ``path`` resolved when it lies outside the repository (requirement 1).

    Args:
        path: The path to check.
        repo_root: The repository's root.

    Returns:
        The resolved path.

    Raises:
        ValueError: The path is the root or lies under it.
    """
    resolved = Path(path).resolve()
    root = Path(repo_root).resolve()
    parts = [os.path.normcase(p) for p in resolved.parts]
    root_parts = [os.path.normcase(p) for p in root.parts]
    if parts[:len(root_parts)] == root_parts:
        raise ValueError(f"{resolved} lies inside the repository {root}; held-out inputs and "
                         "outputs must live outside it")
    return resolved


# --------------------------------------------------------------------------- overlay


def _where(exc: ValueError) -> str:
    """The top-level key a lexicon error is about, never its ticker or surface string."""
    text = str(exc)
    for key in sorted(_OVERLAY_KEYS):
        if text.startswith(key):
            return key
    return "file"


def merge_lexicon_overlay(lexicon: Lexicon, path: str | Path) -> Lexicon:
    """Adds the held-out filers of an overlay file to a lexicon (requirement 2).

    Args:
        lexicon: The committed lexicon (not changed).
        path: The overlay YAML file (``companies`` and ``period_ends`` only).

    Returns:
        A new lexicon with the overlay's companies and period ends added.

    Raises:
        ValueError: The overlay is malformed, has another key, or names a base filer.
    """
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError:
        raise ValueError("the lexicon overlay is not valid YAML") from None
    if doc is not None and not isinstance(doc, dict):
        raise ValueError("the lexicon overlay must be a mapping")
    extra = set(doc or {}) - _OVERLAY_KEYS
    if extra:
        raise ValueError(f"the lexicon overlay may hold only companies and period_ends; it "
                         f"has {len(extra)} other top-level key(s)")
    try:
        overlay = load_lexicon(path)
    except ValueError as exc:
        # The loader's message quotes the offending ticker or surface string: never repeat it.
        raise ValueError(f"the lexicon overlay is malformed under {_where(exc)}") from None
    base_named = set(lexicon.companies) | set(lexicon.period_ends)
    named = set(overlay.companies) | set(overlay.period_ends)
    clash = named & base_named
    if clash:
        raise ValueError(f"the lexicon overlay names {len(clash)} filer(s) the committed "
                         "lexicon already names")
    orphans = set(overlay.period_ends) - set(overlay.companies)
    if orphans:
        raise ValueError(f"the lexicon overlay's period_ends name {len(orphans)} filer(s) its "
                         "companies do not")
    companies = {t: dict(v) for t, v in lexicon.companies.items()}
    companies.update({t: dict(v) for t, v in overlay.companies.items()})
    period_ends = {t: dict(v) for t, v in lexicon.period_ends.items()}
    period_ends.update({t: dict(v) for t, v in overlay.period_ends.items()})
    return Lexicon(companies=companies, metrics=lexicon.metrics,
                   dimensions=lexicon.dimensions, universes=lexicon.universes,
                   period_ends=period_ends)


def drawn_filers(draw: Mapping[str, Any]) -> list[dict[str, str]]:
    """The drawn filers of both strata of a held-out draw file (requirement 3).

    Args:
        draw: The parsed draw file.

    Returns:
        ``{"cik", "symbol", "name"}`` per drawn filer, sorted by CIK.

    Raises:
        ValueError: The scheme is not ``xbrlrag-heldout-v2`` or a stratum is missing.
    """
    if draw.get("scheme") != DRAW_SCHEME:
        raise ValueError(f"the draw file's scheme must be {DRAW_SCHEME!r}")
    strata = draw.get("strata")
    out = []
    for name in ("S", "D"):
        stratum = strata.get(name) if isinstance(strata, Mapping) else None
        drawn = stratum.get("drawn") if isinstance(stratum, Mapping) else None
        if not isinstance(drawn, list):
            raise ValueError(f"the draw file lacks strata.{name}.drawn")
        out += [{"cik": str(e["cik"]).strip().zfill(10), "symbol": str(e["symbol"]),
                 "name": str(e["name"])} for e in drawn]
    return sorted(out, key=lambda f: f["cik"])


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value).strip()


def lexicon_overlay(drawn: Sequence[Mapping[str, str]], entities: Iterable[Sequence[Any]],
                    filings: Iterable[Sequence[Any]]) -> dict[str, Any]:
    """Builds the overlay document of the drawn filers (requirement 3).

    Args:
        drawn: ``drawn_filers`` output.
        entities: ``(cik, company_name, trading_symbol)`` rows.
        filings: ``(cik, accession, form_type, fiscal_year, filed, period_end)`` rows.

    Returns:
        ``{"companies": {...}, "period_ends": {...}}``.

    Raises:
        ValueError: Two drawn filers share a ticker.
    """
    by_cik = {str(cik).strip().zfill(10): (name, symbol) for cik, name, symbol in entities}
    # cik -> fiscal year -> (filed, accession, period end) of the earliest original report.
    first: dict[str, dict[int, tuple[str, str, str]]] = {}
    for cik, accession, form, year, filed, period_end in filings:
        if _text(form) not in _ORIGINAL_ANNUAL or year is None:
            continue
        candidate = (_iso(filed), _text(accession), _iso(period_end))
        years = first.setdefault(str(cik).strip().zfill(10), {})
        if int(year) not in years or candidate[:2] < years[int(year)][:2]:
            years[int(year)] = candidate

    companies: dict[str, dict[str, str]] = {}
    period_ends: dict[str, dict[int, str]] = {}
    for filer in drawn:
        cik = str(filer["cik"]).strip().zfill(10)
        if cik not in by_cik:
            continue
        name, symbol = by_cik[cik]
        ticker = _text(symbol).upper()
        if not ticker:
            continue
        if ticker in companies:
            raise ValueError("two drawn filers share one trading symbol")
        short = _text(filer["name"])
        companies[ticker] = {"short": short, "legal": _text(name) or short}
        ends = first.get(cik)
        if ends:
            period_ends[ticker] = {y: ends[y][2] for y in sorted(ends)}
    return {"companies": {t: companies[t] for t in sorted(companies)},
            "period_ends": {t: period_ends[t] for t in sorted(period_ends)}}


def write_lexicon_overlay(overlay: Mapping[str, Any], path: str | Path) -> Path:
    """Writes an overlay document as YAML that ``merge_lexicon_overlay`` reads back.

    Args:
        overlay: ``lexicon_overlay`` output.
        path: The file to write.

    Returns:
        The path written.
    """
    doc = {"companies": {str(t): {"short": str(v["short"]), "legal": str(v["legal"])}
                         for t, v in overlay["companies"].items()},
           "period_ends": {str(t): {int(y): str(e) for y, e in ends.items()}
                           for t, ends in overlay["period_ends"].items()}}
    text = yaml.safe_dump(doc, sort_keys=True, allow_unicode=True, default_flow_style=False)
    target = Path(path)
    target.write_bytes(text.encode("utf-8"))
    return target


# --------------------------------------------------------------------------- groups


def heldout_groups(groups_doc: Mapping[str, Any], dev_tags: Mapping[str, Any],
                   heldout_tags: Mapping[str, Any]) -> dict[str, list[str]]:
    """The named groups over the committed and the held-out sector tags (requirement 4).

    Args:
        groups_doc: ``load_universe_groups`` output.
        dev_tags: ``load_sector_tags`` of the committed sectors file.
        heldout_tags: ``load_sector_tags`` of the held-out tags file.

    Returns:
        group id -> sorted tickers.

    Raises:
        ValueError: A filer in both tag sets, a bad definition, or stale committed groups.
    """
    dev, held = dict(dev_tags["filers"]), dict(heldout_tags["filers"])
    if set(dev) & set(held):
        raise ValueError(f"{len(set(dev) & set(held))} CIK(s) are in both the committed and "
                         "the held-out sector tags")
    dev_tickers = {f["ticker"] for f in dev.values()}
    held_tickers = {f["ticker"] for f in held.values()}
    if dev_tickers & held_tickers:
        raise ValueError(f"{len(dev_tickers & held_tickers)} ticker(s) are in both the "
                         "committed and the held-out sector tags")
    merged = {"as_of": dev_tags.get("as_of"), "source": dev_tags.get("source"),
              "filers": {**dev, **held}}
    definitions = {gid: dict(rule) for gid, rule in groups_doc["definitions"].items()}
    groups = build_groups(merged, definitions)
    for gid, members in groups.items():
        dev_part = sorted(t for t in members if t not in held_tickers)
        if dev_part != sorted(groups_doc["groups"].get(gid, [])):
            raise ValueError(f"group {gid!r}: the committed members differ from what its "
                             "definition selects over the committed tags (stale groups)")
    return {gid: sorted(groups[gid]) for gid in definitions}


# --------------------------------------------------------------------------- build


def involves_heldout(intent: Intent, universes: Mapping[str, Sequence[str]],
                     heldout: Collection[str]) -> bool:
    """Whether an intent involves a held-out filer (requirement 5).

    Args:
        intent: The candidate intent.
        universes: universe id -> member tickers.
        heldout: The held-out tickers.

    Returns:
        True when a held-out ticker is the intent's filer or a member of its universe.

    Raises:
        ValueError: The intent's universe is not in ``universes``.
    """
    held = {str(t).strip().upper() for t in heldout}
    names = {str(t).strip().upper() for t in intent.tickers}
    if intent.universe is not None:
        if intent.universe not in universes:
            raise ValueError("an intent names a universe the build does not define")
        names |= {str(t).strip().upper() for t in universes[intent.universe]}
    return bool(names & held)


def _heldout_set(heldout: Sequence[str], inv: Mapping[str, Any]) -> frozenset[str]:
    """The held-out tickers, each checked to be in the inventory (requirement 6.1)."""
    held = frozenset(str(t).strip().upper() for t in heldout)
    if not held:
        raise ValueError("a held-out build needs at least one held-out filer")
    missing = held - set(inv)
    if missing:
        raise ValueError(f"{len(missing)} held-out filer(s) are not in the inventory")
    return held


def _resolution_use(witness: KeyWitness) -> dict[str, Any]:
    """``check_resolution_use`` over the resolutions covering a kept witness, plus ``unused``.

    The committed resolutions were reviewed against the development build; H-F keys a subset,
    so a resolution covering nothing it kept is counted, not refused (requirement 6.4).
    """
    kept = [witness.witnesses[k] for k in sorted(witness.witnesses, key=repr)]
    used = [r for r in witness.resolutions if any(_covers(r, w) for w in kept)]
    out = check_resolution_use(used, kept, witness._tables_all)  # the build's FSDS tables
    out["unused"] = len(witness.resolutions) - len(used)
    return out


def _relabel(record: dict[str, Any], suite: str) -> dict[str, Any]:
    """A development record turned into its held-out form: suite name and split tag."""
    if record["tags"][0] != "split:dev":
        raise AssertionError("a share-0 phrasing put a case outside the dev split")
    record["suite"] = suite
    record["tags"] = [f"split:{SPLIT}", *record["tags"][1:]]
    return record


def _assemble(records: list[dict[str, Any]], kept: Sequence[KeyedIntent], *, suite_name: str,
              intents_manifest: dict[str, Any], per_intent: int, salt: str, n_heldout: int,
              witness: KeyWitness | None) -> dict[str, Any]:
    """The suite, manifest and intent list of a held-out build (requirement 6.6)."""
    cases = sorted(records, key=lambda r: r["id"])
    digest = content_hash(cases)
    suite = {"name": suite_name, "version": "1", "split": SPLIT, "n_cases": len(cases),
             "content_hash": digest, "cases": cases}
    shapes = Counter(r["origin"].removeprefix("v2:") for r in cases)
    statuses = Counter(r["key"]["status"] for r in cases)
    intents = [_intent_object(k["intent"]) for k in kept]
    manifest: dict[str, Any] = {
        "split": SPLIT,
        "content_hashes": {SPLIT: digest},
        "cases": {SPLIT: {"total": len(cases), "by_shape": dict(sorted(shapes.items())),
                          "by_key_status": dict(sorted(statuses.items()))}},
        "intents": intents_manifest,
        "disagreements": [d for k in kept for d in k["disagreements"]],
        "phrasing": {"per_intent": per_intent, "share": 0, "salt": salt},
        "intents_hash": content_hash(intents),
        "heldout": {"filers": n_heldout},
    }
    if witness is not None:
        manifest["key_independence"] = witness.manifest()
        manifest["key_definitions"] = witness.definitions_manifest()
        manifest["key_resolutions"] = _resolution_use(witness)
        manifest["fsds"] = witness.fsds_manifest()
    return {"heldout_filers": suite, "manifest": manifest, "intents": intents}


_Readings = tuple[KeyedIntent, dict[int, AnswerKey] | None, dict[int, list[dict[str, Any]]] | None]


def _key_main(keyer: _Keyer, witness: KeyWitness | None, intent: Intent) -> _Readings:
    """Keys one main candidate; with a witness, a block drops it as ``witness_blocked``.

    The keying runs in the witness's provisional scope, so a block never reaches the
    witness's report (requirement 6.3), whatever other reason the keying met.
    """
    if witness is None:
        one = keyer.key(intent)
        return one, keyer.cover_readings(one), keyer.defined_readings(one)
    with witness.provisional() as blocks:
        try:
            one = keyer.key(intent)
        except IntentDroppedError:
            if blocks:
                raise IntentDroppedError("witness_blocked") from None
            raise
        covers, defined = keyer.cover_readings(one), keyer.defined_readings(one)
    if blocks:
        raise IntentDroppedError("witness_blocked")
    return one, covers, defined


def build_heldout_suites(inventory: Mapping[str, Any], universes: Mapping[str, Sequence[str]],
                         sec: SecSource, fact_store: FactStoreSource, phrasebook: Phrasebook,
                         lexicon: Lexicon, *, heldout: Sequence[str], quotas: Mapping[str, int],
                         per_intent: int, salt: str,
                         witness: KeyWitness | None = None) -> dict[str, Any]:
    """Builds the held-out main suite ``heldout_hf`` and its manifest (requirement 6).

    Args:
        inventory: The corpus inventory.
        universes: universe id -> member tickers.
        sec: Where the companyfacts payloads come from.
        fact_store: Where the cross-check values come from.
        phrasebook: Question templates.
        lexicon: Surface forms (the committed lexicon merged with the overlay).
        heldout: The held-out tickers.
        quotas: shape -> number of keyed intents wanted.
        per_intent: Phrasings per intent.
        salt: The split salt (recorded; the share is 0).
        witness: The statement witness, or None.

    Returns:
        ``{"heldout_filers": suite, "manifest": manifest, "intents": intents}``.

    Raises:
        ValueError: An empty ``heldout``, a held-out filer outside the inventory, or a bad
            quota.
    """
    wanted = _validated_quotas(quotas)
    inv = _normalize_inventory(inventory)
    unis = _normalize_universes(universes)
    held = _heldout_set(heldout, inv)
    keyer = _Keyer(inv, unis, _MemoSecSource(sec), fact_store, witness)
    every = _enumerate(inv, unis)
    candidates = {s: [i for i in every[s] if involves_heldout(i, unis, held)]
                  for s in GENERATED_SHAPES}

    keyed: dict[str, _Readings] = {}
    selected: dict[str, int] = {}
    dropped_by_shape: dict[str, Counter[str]] = {}
    for shape in GENERATED_SHAPES:
        count = 0
        for intent in order_candidates(candidates[shape]):
            if count >= wanted[shape]:
                break
            try:
                keyed[intent_id(intent)] = _key_main(keyer, witness, intent)
            except IntentDroppedError as exc:
                dropped_by_shape.setdefault(shape, Counter())[str(exc.args[0])] += 1
                continue
            count += 1
        selected[shape] = count

    ordered_ids = sorted(keyed)
    phrased = build_phrased_cases([keyed[i][0]["intent"] for i in ordered_ids], phrasebook,
                                  lexicon, per_intent=per_intent, share=0, salt=salt)
    records = []
    for case in phrased:
        iid = intent_id(case.intent)
        one, covers, defined = keyed[iid]
        eval_case = _eval_case(case, one, inv, iid, covers, defined)
        metrics = one["intent"].metrics
        definition = key_definition(metrics[0]) if witness is not None and metrics else None
        records.append(_relabel(_case_record(eval_case, one, covers, defined, definition),
                                SUITE))
    return _assemble(
        records, [keyed[i][0] for i in ordered_ids], suite_name=SUITE,
        intents_manifest=_intents_manifest(wanted, selected, candidates, dropped_by_shape),
        per_intent=per_intent, salt=salt, n_heldout=len(held), witness=witness)


def build_heldout_compound_suites(inventory: Mapping[str, Any],
                                  universes: Mapping[str, Sequence[str]], sec: SecSource,
                                  fact_store: FactStoreSource, phrasebook: Phrasebook,
                                  lexicon: Lexicon, *, heldout: Sequence[str],
                                  quotas: Mapping[str, int], per_intent: int, salt: str,
                                  witness: KeyWitness | None = None) -> dict[str, Any]:
    """Builds the held-out compound suite ``heldout_hf_compound`` (requirement 7).

    Args:
        inventory: The corpus inventory.
        universes: universe id -> member tickers.
        sec: Where the companyfacts payloads come from.
        fact_store: Where the cross-check values come from.
        phrasebook: The compound question templates.
        lexicon: Surface forms (the committed lexicon merged with the overlay).
        heldout: The held-out tickers.
        quotas: compound shape -> number of keyed intents wanted.
        per_intent: Phrasings per intent.
        salt: The split salt (recorded; the share is 0).
        witness: The statement witness, or None.

    Returns:
        ``{"heldout_filers": suite, "manifest": manifest, "intents": intents}``.

    Raises:
        ValueError: An empty ``heldout``, a held-out filer outside the inventory, or a bad
            quota.
    """
    wanted = _validated_compound_quotas(quotas)
    inv = _normalize_inventory(inventory)
    unis = _normalize_universes(universes)
    held = _heldout_set(heldout, inv)
    keyer = _Keyer(inv, unis, _MemoSecSource(sec), fact_store, witness)
    every = _enumerate_compound(inv, unis)
    candidates = {s: [i for i in every[s] if involves_heldout(i, unis, held)]
                  for s in COMPOUND_SHAPES}

    keyed: dict[str, tuple[KeyedIntent, dict[str, Any]]] = {}
    selected: dict[str, int] = {}
    dropped_by_shape: dict[str, Counter[str]] = {}
    for shape in COMPOUND_SHAPES:
        count = 0
        for intent in order_candidates(candidates[shape]):
            if count >= wanted[shape]:
                break
            try:
                # key_compound already drops a witness block as witness_blocked.
                keyed[intent_id(intent)] = keyer.key_compound(intent)
            except IntentDroppedError as exc:
                dropped_by_shape.setdefault(shape, Counter())[str(exc.args[0])] += 1
                continue
            count += 1
        selected[shape] = count

    ordered_ids = sorted(keyed)
    phrased = build_phrased_cases([keyed[i][0]["intent"] for i in ordered_ids], phrasebook,
                                  lexicon, per_intent=per_intent, share=0, salt=salt)
    records = []
    for case in phrased:
        one, dependent = keyed[intent_id(case.intent)]
        records.append(_relabel(_compound_record(case, one, dependent, lexicon),
                                COMPOUND_SUITE))
    return _assemble(
        records, [keyed[i][0] for i in ordered_ids], suite_name=COMPOUND_SUITE,
        intents_manifest=_intents_manifest(wanted, selected, candidates, dropped_by_shape),
        per_intent=per_intent, salt=salt, n_heldout=len(held), witness=witness)


# --------------------------------------------------------------------------- output


def write_heldout_suites(build: Mapping[str, Any], out_dir: str | Path) -> dict[str, Path]:
    """Writes ``heldout_hf.json``, its manifest and its intent list (requirement 8).

    Args:
        build: ``build_heldout_suites`` output.
        out_dir: The directory to write into.

    Returns:
        ``{"heldout_filers": path, "manifest": path, "intents": path}``.
    """
    return _write(build, out_dir, _FILE_NAMES)


def write_heldout_compound_suites(build: Mapping[str, Any],
                                  out_dir: str | Path) -> dict[str, Path]:
    """Writes ``heldout_hf_compound.json``, its manifest and intent list (requirement 8).

    Args:
        build: ``build_heldout_compound_suites`` output.
        out_dir: The directory to write into.

    Returns:
        ``{"heldout_filers": path, "manifest": path, "intents": path}``.
    """
    return _write(build, out_dir, _COMPOUND_FILE_NAMES)


def _counts(mapping: Mapping[str, Any]) -> str:
    """``name n, name n`` over a mapping of fixed names to counts, or ``none``."""
    return ", ".join(f"{k} {int(v)}" for k, v in mapping.items()) or "none"


def heldout_summary(build: Mapping[str, Any]) -> list[str]:
    """The counts-only lines the build script prints for a held-out build (requirement 9).

    Args:
        build: A held-out main or compound build.

    Returns:
        The lines: counts and the content hash only.
    """
    suite = build["heldout_filers"]
    manifest = build["manifest"]
    intents = manifest["intents"]
    lines = [f"{suite['name']}: held-out filers {int(manifest['heldout']['filers'])}"]
    for shape, quota in intents["quota"].items():
        dropped = intents["dropped_by_shape"].get(shape, {})
        lines.append(f"  {shape}: quota {int(quota)}, selected {int(intents['selected'][shape])},"
                     f" candidates {int(intents['candidates'][shape])}, dropped "
                     f"{_counts(dropped)}")
    counts = manifest["cases"][SPLIT]
    lines.append(f"{SPLIT}: {int(counts['total'])} cases; by key status "
                 f"{_counts(counts['by_key_status'])}; {suite['content_hash']}")
    lines.append(f"disagreements: {len(manifest['disagreements'])}")
    if "key_independence" in manifest:
        independence = manifest["key_independence"]
        lines.append(f"statement witness: {int(independence['n'])} keys; by status "
                     f"{_counts(independence['by_status'])}; resolved "
                     f"{int(independence['resolved'])}; held {len(independence['held'])}")
    if "key_definitions" in manifest:
        lines.append(f"defined keys: {int(manifest['key_definitions']['n'])}")
    if "key_resolutions" in manifest:
        res = manifest["key_resolutions"]
        lines.append(f"key resolutions: used {int(res['n'])} ({int(res['original'])} original, "
                     f"{int(res['reviewed'])} reviewed), unused {int(res['unused'])}; by bucket "
                     f"{_counts(res['by_bucket'])}")
    if "fsds" in manifest:
        coverage = manifest["fsds"]["coverage"]
        lines.append(f"fsds: quarters {int(coverage['n'])}, missing "
                     f"{len(coverage['missing'])}; not published "
                     f"{int(manifest['fsds']['not_published']['n'])}")
    return lines
