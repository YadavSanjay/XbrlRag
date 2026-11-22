# v2 phrasing source 2: export phrased questions into batch files for a one-off, hand-run
# paraphrase generation in an LLM web UI, and import the saved replies (format and meaning
# checks, duplicate removal, the three-axis split, a frozen and hashed paraphrase set).
# No LLM call is made here. Spec: docs/specs/eval-paraphrases.md.
#
# No-text rule: no ValueError raised here quotes a question, a paraphrase or a body line;
# messages name line numbers, file basenames, case ids and counts only.

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from src import PROJECT_ROOT

PERSONAS = ("sell_side_shorthand", "terse_pm", "conversational_retail", "typo_prone",
            "buried_ask")
AXES = ("dev", "validation")
SCHEMA = "paraphrase-set/1"
REASONS = ("company_missing", "period_missing", "metric_missing", "universe_missing",
           "semantic_drift")
# Built-in drift phrases (spec, Meaning check); `cy` / `cyNNNN` tokens are checked apart.
DRIFT_PHRASES = ("calendar", "quarter", "quarterly", "q1", "q2", "q3", "q4", "ttm", "ltm",
                 "trailing", "ytd", "year to date", "last year", "this year", "last fiscal year",
                 "latest", "most recent")
# Model families the maintainer's decision excludes as paraphrase generators.
_EXCLUDED_MODEL_WORDS = ("gpt", "openai", "claude", "anthropic", "deepseek")

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'"})
_POSSESSIVE_RE = re.compile(r"'s(?![^\W_])")
_TOKEN_RE = re.compile(r"[^\W_]+")
_CY_RE = re.compile(r"cy\d*")
_VERSION_RE = re.compile(r"v[0-9]+")
_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_HEADER_RE = re.compile(r"^#\s*(model|date|prompt)\s*:\s*(\S.*?)\s*$")
_HEADER_KEYS = {"model": "model", "date": "date", "prompt": "prompt_version"}
_RECORD_KEYS = frozenset({"case_id", "persona", "paraphrase"})
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _iso_date(value: object) -> bool:
    if not isinstance(value, str) or _ISO_DATE_RE.fullmatch(value) is None:
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _forms(field: str, case_id: str, value: Iterable[object]) -> tuple[str, ...]:
    """Validates one sequence of surface forms; the message names the field, never a form."""
    if isinstance(value, str):
        raise ValueError(f"{case_id}: {field} must be a sequence of str, not a str")
    forms: list[str] = []
    for form in value:
        if not isinstance(form, str) or not normalise(form):
            raise ValueError(f"{case_id}: every {field} entry must be a str with a non-empty "
                             "normalised form")
        forms.append(form)
    return tuple(forms)


class ParaphraseSource:
    """One phrased question to paraphrase, with what its meaning check needs."""

    def __init__(
        self,
        case_id: str,
        question: str,
        *,
        intent_id: str,
        intent_axis: str,
        template_axis: str,
        company_forms: Sequence[str] = (),
        years: Sequence[int] = (),
        period_ends: Mapping[int, str] | None = None,
        metric_forms: Sequence[Sequence[str]] = (),
        universe_forms: Sequence[str] = (),
        forbidden_forms: Sequence[str] = (),
    ) -> None:
        """Validates and stores the fields as attributes of the same names.

        Args:
            case_id: The phrased case's id.
            question: The phrased question.
            intent_id: The case's intent id.
            intent_axis: ``dev`` or ``validation``.
            template_axis: ``dev`` or ``validation``.
            company_forms: The company's surface forms; empty for no company.
            years: The fiscal years the question names; stored sorted and unique.
            period_ends: ``{year: "YYYY-MM-DD"}`` fiscal period ends, or None.
            metric_forms: One sequence of surface forms per metric.
            universe_forms: The universe's phrases, if any.
            forbidden_forms: Phrases whose appearance changes the meaning.

        Raises:
            ValueError: On any rule under the spec's *Inputs*.
        """
        if not _non_empty(case_id):
            raise ValueError("case_id must be a non-empty str")
        if not _non_empty(question):
            raise ValueError(f"{case_id}: question must be a non-empty str")
        if not _non_empty(intent_id):
            raise ValueError(f"{case_id}: intent_id must be a non-empty str")
        for name, value in (("intent_axis", intent_axis), ("template_axis", template_axis)):
            if value not in AXES:
                raise ValueError(f"{case_id}: {name} must be 'dev' or 'validation'")
        for y in years:
            if not _is_int(y):
                raise ValueError(f"{case_id}: every year must be an int")
        ends: dict[int, str] = {}
        for year, end in (period_ends or {}).items():
            if not _is_int(year):
                raise ValueError(f"{case_id}: every period_ends key must be an int year")
            if not _iso_date(end):
                raise ValueError(f"{case_id}: period_ends[{year}] must be an ISO YYYY-MM-DD date")
            ends[year] = end
        if isinstance(metric_forms, str):
            raise ValueError(f"{case_id}: metric_forms must be a sequence of sequences")
        metrics = tuple(_forms("metric_forms", case_id, m) for m in metric_forms)
        if not metrics or any(not m for m in metrics):
            raise ValueError(f"{case_id}: metric_forms needs at least one metric, each with at "
                             "least one form")

        self.case_id = case_id
        self.question = question
        self.intent_id = intent_id
        self.intent_axis = intent_axis
        self.template_axis = template_axis
        self.company_forms = _forms("company_forms", case_id, company_forms)
        self.years: tuple[int, ...] = tuple(sorted(set(years)))
        self.period_ends = ends
        self.metric_forms: tuple[tuple[str, ...], ...] = metrics
        self.universe_forms = _forms("universe_forms", case_id, universe_forms)
        self.forbidden_forms = _forms("forbidden_forms", case_id, forbidden_forms)

    def _key(self) -> tuple[object, ...]:
        return (self.case_id, self.question, self.intent_id, self.intent_axis,
                self.template_axis, self.company_forms, self.years,
                tuple(sorted(self.period_ends.items())), self.metric_forms,
                self.universe_forms, self.forbidden_forms)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ParaphraseSource):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def __repr__(self) -> str:
        # No question text: a repr can end up in a log or a traceback.
        return (f"ParaphraseSource({self.case_id!r}, intent_id={self.intent_id!r}, "
                f"intent_axis={self.intent_axis!r}, template_axis={self.template_axis!r})")


def normalise(text: str) -> str:
    """Returns the normalised token string used for matching and near-duplicates.

    Args:
        text: Any text.

    Returns:
        Lower-cased, possessive-free tokens joined by single spaces.
    """
    t = unicodedata.normalize("NFKC", text).casefold()
    t = t.translate(_APOSTROPHES)
    t = _POSSESSIVE_RE.sub("", t).replace("'", "")
    return " ".join(_TOKEN_RE.findall(t))


def _contains(normalised_text: str, form: str) -> bool:
    """Whether a text, already normalised, contains ``form`` as whole tokens."""
    n = normalise(form)
    return bool(n) and f" {n} " in f" {normalised_text} "


def _ranked(case_id: str) -> list[str]:
    return sorted(PERSONAS,
                  key=lambda p: hashlib.sha1(f"{case_id}|{p}".encode()).hexdigest())


def assign_personas(case_id: str, k: int) -> tuple[str, ...]:
    """Returns the ``k`` personas requested for a case, in canonical order.

    Args:
        case_id: The case id.
        k: How many personas, 1 to 5.

    Returns:
        The personas with the smallest SHA-1 of ``f"{case_id}|{persona}"``.

    Raises:
        ValueError: When ``k`` is outside 1..5.
    """
    if not _is_int(k) or not 1 <= k <= len(PERSONAS):
        raise ValueError(f"k must be an int in 1..{len(PERSONAS)}, got {k!r}")
    chosen = set(_ranked(case_id)[:k])
    return tuple(p for p in PERSONAS if p in chosen)


def instructions_path(version: str) -> Path:
    """Returns the path of a versioned paraphrase instruction block.

    Args:
        version: ``v`` followed by digits.

    Returns:
        ``PROJECT_ROOT/prompts/templates/eval_paraphrase/<version>/instructions.txt``.

    Raises:
        ValueError: When ``version`` is malformed.
    """
    if not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
        raise ValueError("version must be 'v' followed by digits")
    return Path(PROJECT_ROOT) / "prompts" / "templates" / "eval_paraphrase" / version \
        / "instructions.txt"


def _unique_ids(sources: Sequence[ParaphraseSource]) -> None:
    seen: set[str] = set()
    for s in sources:
        if s.case_id in seen:
            raise ValueError(f"two sources share case_id {s.case_id}")
        seen.add(s.case_id)


def export_batches(
    sources: Sequence[ParaphraseSource],
    *,
    instructions: str,
    prompt_version: str,
    batch_size: int = 50,
    personas_per_case: int = 2,
) -> list[str]:
    """Returns the batch texts the maintainer pastes into the web UI.

    Args:
        sources: The questions to paraphrase.
        instructions: The instruction block text.
        prompt_version: The instruction block's version.
        batch_size: Questions per batch.
        personas_per_case: Personas requested per question.

    Returns:
        One text per batch.

    Raises:
        ValueError: On a bad size, a duplicate case id or a version mismatch.
    """
    if not _is_int(batch_size) or batch_size < 1:
        raise ValueError(f"batch_size must be an int >= 1, got {batch_size!r}")
    if not _is_int(personas_per_case) or not 1 <= personas_per_case <= len(PERSONAS):
        raise ValueError(f"personas_per_case must be in 1..{len(PERSONAS)}, "
                         f"got {personas_per_case!r}")
    _unique_ids(sources)
    first = instructions.split("\n", 1)[0].removesuffix("\r")
    if first != f"# paraphrase-instructions {prompt_version}":
        raise ValueError("the instructions' first line must be exactly "
                         f"'# paraphrase-instructions {prompt_version}'")
    if not sources:
        return []
    ordered = sorted(sources, key=lambda s: s.case_id)
    n = math.ceil(len(ordered) / batch_size)
    head = instructions.rstrip("\n") + "\n\n"
    batches = []
    for i in range(n):
        chunk = ordered[i * batch_size:(i + 1) * batch_size]
        text = head + (f"### BATCH {i + 1} OF {n} | PROMPT {prompt_version} | "
                       f"{len(chunk)} QUESTIONS\n")
        for s in chunk:
            row = {"case_id": s.case_id,
                   "personas": list(assign_personas(s.case_id, personas_per_case)),
                   "question": s.question}
            text += json.dumps(row, ensure_ascii=False) + "\n"
        batches.append(text)
    return batches


def _excluded_model(model: str) -> bool:
    low = model.lower()
    return any(w in low for w in _EXCLUDED_MODEL_WORDS) or re.match(r"o[0-9]", low) is not None


def _parse_header(lines: list[str]) -> tuple[dict[str, str], int]:
    """Parses the header run; returns its values and the number of header lines."""
    header: dict[str, str] = {}
    n = 0
    while n < len(lines) and lines[n].strip().startswith("#"):
        m = _HEADER_RE.match(lines[n].strip())
        if m is None:
            raise ValueError(f"line {n + 1}: a header line must be '# model|date|prompt: value'")
        key = _HEADER_KEYS[m.group(1)]
        if key in header:
            raise ValueError(f"line {n + 1}: header key '{m.group(1)}' repeated")
        header[key] = m.group(2)
        n += 1
    missing = [k for k, v in _HEADER_KEYS.items() if v not in header]
    if missing:
        raise ValueError(f"header lacks {', '.join(missing)}")
    if not _iso_date(header["date"]):
        raise ValueError("header date must be a real YYYY-MM-DD date")
    if _excluded_model(header["model"]):
        raise ValueError("header model is from an excluded family (OpenAI, Anthropic, "
                         "DeepSeek); paraphrases must come from a fourth family")
    return header, n


def _parse_record(line_no: int, line: str) -> dict[str, Any]:
    try:
        obj = json.loads(line)
    except ValueError:
        raise ValueError(f"line {line_no}: not a JSON object") from None
    if not isinstance(obj, dict):
        raise ValueError(f"line {line_no}: not a JSON object")
    if set(obj) != _RECORD_KEYS:
        raise ValueError(f"line {line_no}: keys must be exactly case_id, persona, paraphrase")
    for key in ("case_id", "persona", "paraphrase"):
        if not _non_empty(obj[key]):
            raise ValueError(f"line {line_no}: {key} must be a non-empty string")
    if obj["persona"].strip() not in PERSONAS:
        raise ValueError(f"line {line_no}: unknown persona")
    return {"line": line_no, "case_id": obj["case_id"].strip(),
            "persona": obj["persona"].strip(), "paraphrase": obj["paraphrase"].strip()}


def parse_reply(text: str) -> dict:
    """Parses one saved reply file: its header and its JSON-lines records.

    Args:
        text: The file's text.

    Returns:
        ``{"model", "date", "prompt_version", "records"}``.

    Raises:
        ValueError: On any format rule; the message never quotes body text.
    """
    if text.startswith("﻿"):
        text = text[1:]
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]
    header, n = _parse_header(lines)
    records = []
    for i in range(n, len(lines)):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("```"):
            continue
        records.append(_parse_record(i + 1, lines[i]))
    if not records:
        raise ValueError("the reply has no record")
    return {"model": header["model"], "date": header["date"],
            "prompt_version": header["prompt_version"], "records": records}


def _period_forms(source: ParaphraseSource, year: int) -> list[str]:
    yyyy, yy = f"{year:04d}", f"{year % 100:02d}"
    forms = [f"fy {yyyy}", f"fy{yyyy}", f"fy {yy}", f"fy{yy}", f"fiscal {yyyy}", f"fiscal {yy}",
             f"fiscal year {yyyy}", f"fiscal year {yy}", f"{yyyy} fiscal year"]
    end = source.period_ends.get(year)
    if end is not None:
        d = dt.date.fromisoformat(end)
        month = _MONTHS[d.month - 1]
        # The full month, its three-letter abbreviation, and "sept" (paraphrase-shorthand).
        for name in (month, month[:3], *(("sept",) if d.month == 9 else ())):
            forms.append(f"{name} {d.day} {d.year:04d}")
    if source.company_forms:
        forms.append(yyyy)
    return forms


def _drift_phrases(source: ParaphraseSource, normalised_text: str) -> list[str]:
    cy = [t for t in normalised_text.split() if _CY_RE.fullmatch(t)]
    return [*source.forbidden_forms, *DRIFT_PHRASES, *cy]


def check_meaning(source: ParaphraseSource, paraphrase: str) -> list[str]:
    """Returns why a paraphrase may not mean what its source question means.

    Args:
        source: The source question and its meaning forms.
        paraphrase: The candidate paraphrase.

    Returns:
        Reason codes in spec order; empty when the paraphrase passes.
    """
    text = normalise(paraphrase)
    question = normalise(source.question)

    def has_any(forms: Sequence[str]) -> bool:
        return any(_contains(text, f) for f in forms)

    reasons = []
    if source.company_forms and not has_any(source.company_forms):
        reasons.append("company_missing")
    if any(not has_any(_period_forms(source, y)) for y in source.years):
        reasons.append("period_missing")
    if any(not has_any(m) for m in source.metric_forms):
        reasons.append("metric_missing")
    if source.universe_forms and not has_any(source.universe_forms):
        reasons.append("universe_missing")
    if any(_contains(text, p) and not _contains(question, p)
           for p in _drift_phrases(source, text)):
        reasons.append("semantic_drift")
    return reasons


def _axis(key: str, share: float, salt: str) -> str:
    """The eval-intents axis rule: ``validation`` iff the salted hash falls below ``share``."""
    digest = hashlib.sha1(f"{salt}:{key}".encode()).hexdigest()
    return "validation" if int(digest[:8], 16) / 2**32 < share else "dev"


def _read_replies(reply_paths: Sequence[str | Path],
                  prompt_version: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Reads and parses each reply file; returns ``(provenance, parsed)`` pairs."""
    out = []
    seen_hashes: dict[str, str] = {}
    for raw_path in reply_paths:
        path = Path(raw_path)
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        if sha in seen_hashes:
            raise ValueError(f"{path.name} has the same bytes as {seen_hashes[sha]}")
        seen_hashes[sha] = path.name
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"{path.name}: not UTF-8") from None
        try:
            parsed = parse_reply(text)
        except ValueError as exc:
            raise ValueError(f"{path.name}: {exc}") from None
        if parsed["prompt_version"] != prompt_version:
            raise ValueError(f"{path.name}: prompt version differs from {prompt_version}")
        prov = {"file": path.name, "sha256": sha, "model": parsed["model"],
                "date": parsed["date"], "prompt_version": parsed["prompt_version"]}
        out.append((prov, parsed))
    return out


def _drop_duplicates(
    sources: Sequence[ParaphraseSource],
    accepted: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Applies the duplicate rule to kept-record candidates, in the order given.

    Within one intent, a candidate whose normalised text equals a source question's or an
    earlier kept paraphrase's is a duplicate (``exact`` when the texts are identical, else
    ``near``). The order is the caller's: ascending ``paraphrase_id``.

    Returns:
        ``(kept, duplicates)``.
    """
    # Per intent: normalised text -> (the id it belongs to, its exact text).
    seen: dict[str, dict[str, tuple[str, str]]] = {}
    for s in sorted(sources, key=lambda s: s.case_id):
        seen.setdefault(s.intent_id, {}).setdefault(normalise(s.question),
                                                    (s.case_id, s.question))
    kept = []
    duplicates = []
    for rec in accepted:
        key = normalise(rec["paraphrase"])
        intent_seen = seen.setdefault(rec["intent_id"], {})
        if key in intent_seen:
            of, of_text = intent_seen[key]
            duplicates.append({"paraphrase_id": rec["paraphrase_id"], "case_id": rec["case_id"],
                               "persona": rec["persona"], "duplicate_of": of,
                               "kind": "exact" if rec["paraphrase"] == of_text else "near",
                               "source_sha256": rec["source_sha256"]})
            continue
        intent_seen[key] = (rec["paraphrase_id"], rec["paraphrase"])
        kept.append(rec)
    return kept, duplicates


def import_replies(
    sources: Sequence[ParaphraseSource],
    reply_paths: Sequence[str | Path],
    *,
    prompt_version: str,
    share: float,
    salt: str,
    sealed: bool = False,
) -> dict:
    """Imports reply files into a frozen, hashed paraphrase set.

    Args:
        sources: The exported questions.
        reply_paths: The saved reply files.
        prompt_version: The instruction version the replies must name.
        share: The validation share of each split axis.
        salt: The split salt.
        sealed: Whether the set is a held-out one whose report shows no text.

    Returns:
        The paraphrase set.

    Raises:
        ValueError: On any import rule; the message never quotes question or paraphrase text.
    """
    if not reply_paths:
        raise ValueError("no reply files")
    _unique_ids(sources)
    if isinstance(share, bool) or not isinstance(share, int | float) or math.isnan(share) \
            or not 0.0 <= share <= 1.0:
        raise ValueError(f"share must be in [0, 1], got {share!r}")
    by_id = {s.case_id: s for s in sources}
    files = _read_replies(reply_paths, prompt_version)

    records: dict[str, dict[str, Any]] = {}
    for prov, parsed in files:
        for r in parsed["records"]:
            if r["case_id"] not in by_id:
                raise ValueError(f"{prov['file']} line {r['line']}: unknown case_id "
                                 f"{r['case_id']}")
            pid = f"{r['case_id']}:{r['persona']}"
            if pid in records:
                raise ValueError(f"{prov['file']} line {r['line']}: {pid} appears twice")
            records[pid] = {**r, "source_sha256": prov["sha256"]}

    review = []
    candidates = []
    for pid in sorted(records):
        r = records[pid]
        reasons = check_meaning(by_id[r["case_id"]], r["paraphrase"])
        if reasons:
            review.append({"paraphrase_id": pid, "case_id": r["case_id"],
                           "persona": r["persona"], "paraphrase": r["paraphrase"],
                           "reasons": reasons, "source_sha256": r["source_sha256"]})
        else:
            candidates.append((pid, r))

    accepted = []
    for pid, r in candidates:
        src = by_id[r["case_id"]]
        persona_axis = _axis(r["persona"], share, salt)
        axes = (src.intent_axis, src.template_axis, persona_axis)
        accepted.append({"paraphrase_id": pid, "case_id": r["case_id"],
                         "intent_id": src.intent_id, "persona": r["persona"],
                         "paraphrase": r["paraphrase"], "source_sha256": r["source_sha256"],
                         "intent_axis": src.intent_axis, "template_axis": src.template_axis,
                         "persona_axis": persona_axis,
                         "split": "validation" if "validation" in axes else "dev"})
    kept, duplicates = _drop_duplicates(sources, accepted)

    pset: dict[str, Any] = {
        "schema": SCHEMA,
        "prompt_version": prompt_version,
        "sealed": bool(sealed),
        "split": {"share": share, "salt": salt},
        "sources": sorted((prov for prov, _ in files), key=lambda p: p["sha256"]),
        "paraphrases": kept,
        "review": review,
        "duplicates": duplicates,
    }
    pset["content_hash"] = _content_hash(pset)
    return pset


def canonical_bytes(obj: dict) -> bytes:
    """Returns the canonical JSON bytes of a set (sorted keys, indent 2, UTF-8, newline).

    Args:
        obj: A JSON-able dict.

    Returns:
        The bytes.
    """
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _content_hash(pset: Mapping[str, Any]) -> str:
    body = {k: v for k, v in pset.items() if k != "content_hash"}
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def _check_hash(pset: object) -> str:
    if not isinstance(pset, dict) or not isinstance(pset.get("content_hash"), str):
        raise ValueError("the paraphrase set has no content_hash")
    actual = _content_hash(pset)
    if pset["content_hash"] != actual:
        raise ValueError("the paraphrase set's content_hash does not match its content")
    return actual


def write_paraphrase_set(pset: dict, path: str | Path) -> str:
    """Writes a paraphrase set after checking its content hash.

    Args:
        pset: The set.
        path: The output file.

    Returns:
        The content hash.

    Raises:
        ValueError: When the content hash is missing or wrong.
    """
    digest = _check_hash(pset)
    Path(path).write_bytes(canonical_bytes(pset))
    return digest


def load_paraphrase_set(path: str | Path) -> dict:
    """Reads a paraphrase set and verifies its content hash.

    Args:
        path: The file.

    Returns:
        The set.

    Raises:
        ValueError: When the content hash is missing or does not match.
    """
    p = Path(path)
    try:
        pset = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        raise ValueError(f"{p.name}: not a JSON paraphrase set") from None
    _check_hash(pset)
    return pset


_RECORD_LISTS = ("paraphrases", "review", "duplicates")


def _suite_questions(name: str, cases: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, case in enumerate(cases):
        cid, question = case.get("id"), case.get("question")
        if not (isinstance(cid, str) and _non_empty(cid)
                and isinstance(question, str) and _non_empty(question)):
            raise ValueError(f"{name} suite: case {i} lacks a non-empty id or question")
        if cid in out:
            raise ValueError(f"{name} suite: case id {cid} appears twice")
        out[cid] = question
    return out


def carry_over_set(
    old_cases: Iterable[Mapping[str, Any]],
    new_cases: Iterable[Mapping[str, Any]],
    pset: dict,
) -> tuple[list[str], dict]:
    """Splits a re-frozen suite into the cases to paraphrase and the records that carry over.

    The re-freeze rule (docs/eval/heldout_v2/preregistration.md): records of removed cases
    are dropped; added cases, and cases reworded in place (same id, a different question),
    are paraphrased afresh. A new-suite case carries over only when the old set keeps at
    least one paraphrase of it and the old suite holds it with the same question.

    Args:
        old_cases: The old frozen suite's cases (``id``, ``question``), for one split.
        new_cases: The new frozen suite's cases, for the same split.
        pset: The old paraphrase set of that split; its content hash is checked.

    Returns:
        ``(ids, carried)``: the new-suite case ids to paraphrase, sorted; and a set holding
        only the records (kept, review and duplicate) of carried cases, unchanged, with the
        reply-file provenance they still cite and a fresh content hash.

    Raises:
        ValueError: On a bad hash or a suite case without an id or question.
    """
    _check_hash(pset)
    old_q = _suite_questions("old", old_cases)
    new_q = _suite_questions("new", new_cases)
    kept_cases = {r["case_id"] for r in pset["paraphrases"]}
    carried_cases = {cid for cid, q in new_q.items()
                     if cid in kept_cases and old_q.get(cid) == q}
    ids = sorted(set(new_q) - carried_cases)
    carried: dict[str, Any] = {k: v for k, v in pset.items() if k not in _RECORD_LISTS}
    for key in _RECORD_LISTS:
        carried[key] = [r for r in pset[key] if r["case_id"] in carried_cases]
    cited = {r["source_sha256"] for key in _RECORD_LISTS for r in carried[key]}
    carried["sources"] = [p for p in pset["sources"] if p["sha256"] in cited]
    carried["content_hash"] = _content_hash(carried)
    return ids, carried


def merge_paraphrase_sets(
    base: dict,
    addition: dict,
    sources: Sequence[ParaphraseSource],
) -> dict:
    """Merges two paraphrase sets of one split into one frozen set.

    Meant for a re-freeze: ``base`` is a carried-over set (``carry_over_set``) and
    ``addition`` the import of the fresh replies. The duplicate rule runs again over the
    union of kept paraphrases, in ascending ``paraphrase_id`` order and seeded with the
    sources' questions, exactly as one import of all the replies would; review entries are
    taken as they are.

    Args:
        base: The first set; its content hash is checked.
        addition: The second set; its content hash is checked.
        sources: The split's sources (the new suite's), the duplicate rule's questions.

    Returns:
        The merged set, with its content hash.

    Raises:
        ValueError: When the sets differ in schema, prompt version, sealing or split
            parameters, share a paraphrase id or a reply file, or name a case not in
            ``sources``.
    """
    _check_hash(base)
    _check_hash(addition)
    _unique_ids(sources)
    for key in ("schema", "prompt_version", "sealed", "split"):
        if base.get(key) != addition.get(key):
            raise ValueError(f"the two sets differ in {key}")
    if base["schema"] != SCHEMA:
        raise ValueError(f"the sets' schema is not {SCHEMA}")
    known = {s.case_id for s in sources}
    seen_ids: set[str] = set()
    for pset in (base, addition):
        for key in _RECORD_LISTS:
            for r in pset[key]:
                pid = r["paraphrase_id"]
                if pid in seen_ids:
                    raise ValueError(f"paraphrase id {pid} appears in both sets or twice")
                seen_ids.add(pid)
                if r["case_id"] not in known:
                    raise ValueError(f"paraphrase id {pid}: case {r['case_id']} is not in "
                                     "the sources")
    shas = [p["sha256"] for p in base["sources"]] + [p["sha256"] for p in addition["sources"]]
    if len(set(shas)) != len(shas):
        raise ValueError("the two sets cite the same reply file")

    def by_pid(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(records, key=lambda r: r["paraphrase_id"])

    kept, new_dups = _drop_duplicates(sources, by_pid([*base["paraphrases"],
                                                       *addition["paraphrases"]]))
    merged: dict[str, Any] = {k: v for k, v in base.items() if k != "content_hash"}
    merged["sources"] = sorted([*base["sources"], *addition["sources"]],
                               key=lambda p: p["sha256"])
    merged["paraphrases"] = kept
    merged["review"] = by_pid([*base["review"], *addition["review"]])
    merged["duplicates"] = by_pid([*base["duplicates"], *addition["duplicates"], *new_dups])
    merged["content_hash"] = _content_hash(merged)
    return merged


def render_import_report(pset: dict) -> str:
    """Returns the import report; a sealed set's report holds no paraphrase text.

    Args:
        pset: The set.

    Returns:
        The report text.
    """
    kept = pset["paraphrases"]
    n_dev = sum(1 for p in kept if p["split"] == "dev")
    n_val = sum(1 for p in kept if p["split"] == "validation")
    lines = [f"paraphrases: {len(kept)}", f"review: {len(pset['review'])}",
             f"duplicates: {len(pset['duplicates'])}",
             f"split: dev={n_dev} validation={n_val}",
             f"content_hash: {pset['content_hash']}"]
    for r in pset["review"]:
        lines.append(f"review {r['case_id']} {r['persona']} {','.join(r['reasons'])}")
        if not pset["sealed"]:
            lines.append(f"  text: {r['paraphrase']}")
    for d in pset["duplicates"]:
        lines.append(f"duplicate {d['case_id']} {d['persona']} of {d['duplicate_of']}")
    return "\n".join(lines) + "\n"
