"""GATE-06a / GATE-06b: the corpus manifest (11 golden + 30 held-out papers) and the per-subject verdict.

`golden_metrics.evaluate_extraction_quality` scores whatever observations it is given; it counts held-out files but
does not know their subject, so 30 Toán papers would satisfy "30 held-out" for the Hóa học & Sinh học gate. The
corpus manifest closes that: a JSON list that says, for each of the 41 papers, its subject, whether it is held out,
the SHA-256 of the anonymised file and that its PII was scrubbed (Chapter 9: the papers live in a private bucket, never
in Git, so the manifest is the only part of the corpus that can be checked here).

    python -m app.ops.golden_corpus --manifest corpus.json [--fixtures-dir /tmp/golden_fixtures] \\
        [--observations runs.json --gate GATE-06b]

`--fixtures-dir` re-hashes the files the CI runner pulled from the bucket. `--observations` is the extraction run:
a JSON list of `DocumentExtractionObservation` fields. Exit code 0 passed, 1 not passed, 2 unreadable input.
What this cannot do: produce the 30 held-out papers or run the extraction on them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ops.golden_metrics import (
    CANONICAL_GOLDEN_CORPUS,
    DocumentExtractionObservation,
    GoldenEvaluationReport,
    evaluate_extraction_quality,
)

SUBJECTS = ("MATH", "PHYSICS", "CHEMISTRY", "BIOLOGY")
EXPECTED_GOLDEN = 11
EXPECTED_HELD_OUT = 30
EXPECTED_TOTAL = EXPECTED_GOLDEN + EXPECTED_HELD_OUT
# Chapter 9 says "41 papers, four subjects" but no per-subject count. Of the 30 held-out papers every subject must
# bring at least this many (30 / 4 = 7.5 on average), so no subject is a token presence and a skewed set, say 26 Toán
# and 1 each of the others, is refused. A product decision of this repository, not a figure of the specification.
MIN_HELD_OUT_PER_SUBJECT = 5
GATE_SUBJECTS: dict[str, tuple[str, ...]] = {
    "GATE-06a": ("MATH", "PHYSICS"),
    "GATE-06b": ("CHEMISTRY", "BIOLOGY"),
}
_SHA256 = re.compile(r"[0-9a-f]{64}")


class CorpusManifestError(ValueError):
    """The manifest file is not a list of well-formed entries (nothing can be verified)."""


@dataclass(frozen=True)
class CorpusEntry:
    corpus_id: str
    subject: str
    is_held_out: bool
    sha256: str
    anonymized: bool


@dataclass(frozen=True)
class CorpusVerification:
    ok: bool
    errors: tuple[str, ...]
    counts: dict[str, int]  # "GOLDEN", "HELD_OUT" and one per subject


@dataclass(frozen=True)
class SubjectGateReport:
    gate_id: str
    passed: bool
    errors: tuple[str, ...]
    corpus: CorpusVerification
    quality: GoldenEvaluationReport | None


def parse_manifest(raw: object) -> tuple[CorpusEntry, ...]:
    if not isinstance(raw, list) or not raw:
        raise CorpusManifestError("the manifest must be a non-empty JSON list")
    entries: list[CorpusEntry] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise CorpusManifestError(f"entry {index} is not an object")
        try:
            entry = CorpusEntry(
                corpus_id=item["corpus_id"],
                subject=item["subject"],
                is_held_out=item["is_held_out"],
                sha256=item["sha256"],
                anonymized=item["anonymized"],
            )
        except KeyError as exc:
            raise CorpusManifestError(f"entry {index} lacks {exc.args[0]!r}") from exc
        if not isinstance(entry.corpus_id, str) or not entry.corpus_id.strip() or "/" in entry.corpus_id:
            raise CorpusManifestError(f"entry {index}: corpus_id must be a plain file name")
        if not isinstance(entry.subject, str) or not isinstance(entry.sha256, str):
            raise CorpusManifestError(f"entry {index}: subject and sha256 must be strings")
        if not isinstance(entry.is_held_out, bool) or not isinstance(entry.anonymized, bool):
            raise CorpusManifestError(f"entry {index}: is_held_out and anonymized must be booleans")
        entries.append(entry)
    return tuple(entries)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def verify_corpus(entries: Sequence[CorpusEntry], *, fixtures_dir: Path | None = None) -> CorpusVerification:
    """Is it the corpus Chapter 9 describes: the 11 canonical golden files plus 30 held-out, all four subjects."""
    errors: list[str] = []
    ids = Counter(e.corpus_id for e in entries)
    errors += [f"DUPLICATE_CORPUS_ID:{cid}" for cid, n in sorted(ids.items()) if n > 1]
    golden = [e for e in entries if not e.is_held_out]
    held_out = [e for e in entries if e.is_held_out]

    canonical = {s.corpus_id: s.subject for s in CANONICAL_GOLDEN_CORPUS}
    golden_ids = {e.corpus_id for e in golden}
    errors += [f"MISSING_GOLDEN_FILE:{cid}" for cid in sorted(set(canonical) - golden_ids)]
    errors += [f"UNKNOWN_GOLDEN_FILE:{cid}" for cid in sorted(golden_ids - set(canonical))]
    errors += [
        f"GOLDEN_SUBJECT_MISMATCH:{e.corpus_id}:{e.subject}!={canonical[e.corpus_id]}"
        for e in golden
        if e.corpus_id in canonical and e.subject != canonical[e.corpus_id]
    ]
    if len(held_out) != EXPECTED_HELD_OUT:
        errors.append(f"HELD_OUT_COUNT:{len(held_out)}!={EXPECTED_HELD_OUT}")
    if len(entries) != EXPECTED_TOTAL:
        errors.append(f"CORPUS_SIZE:{len(entries)}!={EXPECTED_TOTAL}")

    for e in entries:
        if e.subject not in SUBJECTS:
            errors.append(f"UNKNOWN_SUBJECT:{e.corpus_id}:{e.subject}")
        if not e.anonymized:
            errors.append(f"NOT_ANONYMIZED:{e.corpus_id}")
        if _SHA256.fullmatch(e.sha256) is None:
            errors.append(f"BAD_SHA256:{e.corpus_id}")
    held_per_subject = Counter(e.subject for e in held_out)
    errors += [
        f"HELD_OUT_SUBJECT_BELOW_MINIMUM:{s}:{held_per_subject[s]}<{MIN_HELD_OUT_PER_SUBJECT}"
        for s in SUBJECTS
        if held_per_subject[s] < MIN_HELD_OUT_PER_SUBJECT
    ]

    if fixtures_dir is not None:
        for e in entries:
            path = fixtures_dir / e.corpus_id
            if not path.is_file():
                errors.append(f"FIXTURE_MISSING:{e.corpus_id}")
            elif _SHA256.fullmatch(e.sha256) and _file_sha256(path) != e.sha256:
                errors.append(f"FIXTURE_HASH_MISMATCH:{e.corpus_id}")

    counts = {"GOLDEN": len(golden), "HELD_OUT": len(held_out), **Counter(e.subject for e in entries)}
    return CorpusVerification(ok=not errors, errors=tuple(errors), counts=counts)


def evaluate_subject_gate(
    gate_id: str,
    entries: Sequence[CorpusEntry],
    observations: Iterable[DocumentExtractionObservation],
    *,
    fixtures_dir: Path | None = None,
) -> SubjectGateReport:
    """The verdict of GATE-06a (Toán, Vật lí) or GATE-06b (Hóa học, Sinh học).

    The whole 41-paper corpus must be complete (a gate on 3 of 41 papers is no gate), every paper of the gate's
    subjects must have been run, and the Chapter 9 thresholds are measured on those papers only, so a perfect Toán
    run cannot cover a failing Hóa học one.
    """
    if gate_id not in GATE_SUBJECTS:
        raise ValueError(f"{gate_id} is not a corpus gate (GATE-06a, GATE-06b)")
    corpus = verify_corpus(entries, fixtures_dir=fixtures_dir)
    errors = list(corpus.errors)
    subjects = set(GATE_SUBJECTS[gate_id])
    wanted = {e.corpus_id: e for e in entries if e.subject in subjects}
    all_runs = list(observations)
    # One paper counted twice would inflate every aggregate; the evaluator refuses it, so it is reported here and the
    # first run of the paper is the one scored.
    run_counts = Counter(o.corpus_id for o in all_runs)
    errors += [f"DUPLICATE_OBSERVATION:{cid}" for cid, n in sorted(run_counts.items()) if n > 1]
    first_runs: dict[str, DocumentExtractionObservation] = {}
    for run in all_runs:
        first_runs.setdefault(run.corpus_id, run)
    seen = list(first_runs.values())

    unknown = sorted({o.corpus_id for o in seen} - {e.corpus_id for e in entries})
    errors += [f"OBSERVATION_NOT_IN_MANIFEST:{cid}" for cid in unknown]
    mine = [o for o in seen if o.corpus_id in wanted]
    errors += [f"MISSING_OBSERVATION:{cid}" for cid in sorted(set(wanted) - {o.corpus_id for o in mine})]
    errors += [
        f"HELD_OUT_FLAG_MISMATCH:{o.corpus_id}" for o in mine if o.is_held_out != wanted[o.corpus_id].is_held_out
    ]

    quality: GoldenEvaluationReport | None = None
    if mine:
        quality = evaluate_extraction_quality(mine)
        errors += list(quality.failed_criteria)
    return SubjectGateReport(gate_id=gate_id, passed=not errors, errors=tuple(errors), corpus=corpus, quality=quality)


def _observations_from(raw: object) -> list[DocumentExtractionObservation]:
    if not isinstance(raw, list):
        raise CorpusManifestError("the observations file must be a JSON list")
    try:
        return [DocumentExtractionObservation(**item) for item in raw]
    except TypeError as exc:
        raise CorpusManifestError(f"bad observation: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the golden corpus manifest and score a GATE-06a/06b run.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--fixtures-dir", type=Path, help="directory with the anonymised files, re-hashed")
    parser.add_argument("--observations", type=Path, help="extraction run: JSON list of observations")
    parser.add_argument("--gate", choices=sorted(GATE_SUBJECTS), default="GATE-06b")
    args = parser.parse_args(argv)
    try:
        entries = parse_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
        if args.observations is None:
            corpus = verify_corpus(entries, fixtures_dir=args.fixtures_dir)
            print(json.dumps({"ok": corpus.ok, "errors": corpus.errors, "counts": corpus.counts}, indent=2))
            return 0 if corpus.ok else 1
        observations = _observations_from(json.loads(args.observations.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, CorpusManifestError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = evaluate_subject_gate(args.gate, entries, observations, fixtures_dir=args.fixtures_dir)
    summary: dict[str, Any] = {"gate": report.gate_id, "passed": report.passed, "errors": report.errors}
    if report.quality is not None:
        q = report.quality
        summary["quality"] = {
            "precision": q.boundary_precision,
            "recall": q.boundary_recall,
            "figure_formula": q.figure_formula_preservation_rate,
            "clean_text": q.clean_text_redaction_rate,
            "files_below_threshold": q.files_below_threshold,
        }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
