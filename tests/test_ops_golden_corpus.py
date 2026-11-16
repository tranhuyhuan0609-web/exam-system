"""GATE-06a / GATE-06b corpus manifest and per-subject verdicts (`app/ops/golden_corpus.py`)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from app.ops.golden_corpus import (
    MIN_HELD_OUT_PER_SUBJECT,
    CorpusEntry,
    CorpusManifestError,
    evaluate_subject_gate,
    main,
    parse_manifest,
    verify_corpus,
)
from app.ops.golden_metrics import CANONICAL_GOLDEN_CORPUS, DocumentExtractionObservation


def _sha(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def _corpus(held_out_subjects: tuple[str, ...] | None = None) -> list[CorpusEntry]:
    """The 11 canonical golden files plus 30 held-out: 8 Toán, 8 Vật lí, 7 Hóa học, 7 Sinh học."""
    subjects = held_out_subjects or ("MATH",) * 8 + ("PHYSICS",) * 8 + ("CHEMISTRY",) * 7 + ("BIOLOGY",) * 7
    entries = [CorpusEntry(s.corpus_id, s.subject, False, _sha(s.corpus_id), True) for s in CANONICAL_GOLDEN_CORPUS]
    entries += [
        CorpusEntry(f"heldout_{n:02d}_{subject.lower()}.pdf", subject, True, _sha(f"h{n}"), True)
        for n, subject in enumerate(subjects, start=1)
    ]
    return entries


def _observations(entries: list[CorpusEntry], *, golden_questions: bool = True) -> list[DocumentExtractionObservation]:
    expected = {s.corpus_id: s.expected_questions for s in CANONICAL_GOLDEN_CORPUS}
    out = []
    for e in entries:
        n = expected.get(e.corpus_id, 10) if golden_questions else 10
        out.append(
            DocumentExtractionObservation(
                corpus_id=e.corpus_id,
                is_held_out=e.is_held_out,
                true_positive_boundaries=n,
                false_positive_boundaries=0,
                false_negative_boundaries=0,
                expected_figures_and_formulas=5,
                preserved_figures_and_formulas=5,
                extracted_questions_total=n,
                clean_text_questions=n,
            )
        )
    return out


def test_the_canonical_41_paper_corpus_verifies_and_both_gates_pass_on_a_perfect_run():
    entries = _corpus()
    verification = verify_corpus(entries)
    assert verification.ok, verification.errors
    # 11 canonical golden files: 4 Toán, 5 Vật lí, 1 Hóa học, 1 Sinh học; plus the 8 + 8 + 7 + 7 held-out
    assert verification.counts == {
        "GOLDEN": 11,
        "HELD_OUT": 30,
        "MATH": 12,
        "PHYSICS": 13,
        "CHEMISTRY": 8,
        "BIOLOGY": 8,
    }
    for gate in ("GATE-06a", "GATE-06b"):
        report = evaluate_subject_gate(gate, entries, _observations(entries))
        assert report.passed, (gate, report.errors)
        assert report.quality is not None and report.quality.boundary_precision == 1.0


def test_thirty_toan_papers_do_not_make_a_hoa_sinh_held_out_set():
    """The hole in evaluate_extraction_quality: it counts held-out files, whatever their subject."""
    entries = _corpus(("MATH",) * 30)
    errors = verify_corpus(entries).errors
    assert "HELD_OUT_SUBJECT_BELOW_MINIMUM:CHEMISTRY:0<5" in errors
    assert "HELD_OUT_SUBJECT_BELOW_MINIMUM:BIOLOGY:0<5" in errors
    assert not evaluate_subject_gate("GATE-06b", entries, _observations(entries)).passed


def test_every_subject_needs_at_least_five_held_out_papers_not_just_one():
    assert MIN_HELD_OUT_PER_SUBJECT == 5
    skewed = _corpus(("MATH",) * 12 + ("PHYSICS",) * 12 + ("CHEMISTRY",) * 4 + ("BIOLOGY",) * 2)
    errors = verify_corpus(skewed).errors
    assert "HELD_OUT_SUBJECT_BELOW_MINIMUM:CHEMISTRY:4<5" in errors
    assert "HELD_OUT_SUBJECT_BELOW_MINIMUM:BIOLOGY:2<5" in errors
    assert not any("MATH" in e or "PHYSICS" in e for e in errors if e.startswith("HELD_OUT_SUBJECT"))
    assert not evaluate_subject_gate("GATE-06a", skewed, _observations(skewed)).passed, "the whole corpus must be sound"
    exactly_five = _corpus(("MATH",) * 10 + ("PHYSICS",) * 10 + ("CHEMISTRY",) * 5 + ("BIOLOGY",) * 5)
    assert verify_corpus(exactly_five).ok


def test_a_paper_observed_twice_is_reported_and_scored_once():
    entries = _corpus()
    observations = _observations(entries)
    chem = next(e.corpus_id for e in entries if e.subject == "CHEMISTRY" and e.is_held_out)
    first = next(o for o in observations if o.corpus_id == chem)
    # a second, bad run of the same paper must neither be summed into the aggregate nor raise
    bad = dataclasses.replace(first, true_positive_boundaries=0, false_negative_boundaries=10)
    for gate in ("GATE-06a", "GATE-06b"):  # also reported by the gate of the other subjects: it is an input defect
        report = evaluate_subject_gate(gate, entries, [*observations, bad])
        assert f"DUPLICATE_OBSERVATION:{chem}" in report.errors and not report.passed
    six_b = evaluate_subject_gate("GATE-06b", entries, [*observations, bad])
    assert six_b.errors == (f"DUPLICATE_OBSERVATION:{chem}",), "only the duplicate: the first run is the one scored"
    assert six_b.quality is not None and six_b.quality.boundary_recall == 1.0
    assert evaluate_subject_gate("GATE-06b", entries, observations).passed


def test_a_corpus_that_is_not_41_papers_is_refused_whatever_the_scores():
    entries = _corpus()
    short = [e for e in entries if e.corpus_id != "heldout_30_biology.pdf"]
    assert "HELD_OUT_COUNT:29!=30" in verify_corpus(short).errors
    assert "CORPUS_SIZE:40!=41" in verify_corpus(short).errors
    assert not evaluate_subject_gate("GATE-06b", short, _observations(short)).passed

    no_golden_03 = [e for e in entries if e.corpus_id != "golden_03_chem11_legacy.doc"]
    assert "MISSING_GOLDEN_FILE:golden_03_chem11_legacy.doc" in verify_corpus(no_golden_03).errors

    renamed = [dataclasses.replace(e, subject="BIOLOGY") if e.corpus_id.startswith("golden_03") else e for e in entries]
    assert any(x.startswith("GOLDEN_SUBJECT_MISMATCH:golden_03") for x in verify_corpus(renamed).errors)


def test_duplicates_unknown_subjects_unscrubbed_files_and_bad_hashes_are_refused():
    entries = _corpus()
    broken = [
        *entries[:-3],
        dataclasses.replace(entries[-3], subject="HISTORY"),
        dataclasses.replace(entries[-2], anonymized=False),
        dataclasses.replace(entries[-1], sha256="not-a-hash"),
        dataclasses.replace(entries[0]),  # the same id twice
    ]
    errors = verify_corpus(broken).errors
    assert f"DUPLICATE_CORPUS_ID:{entries[0].corpus_id}" in errors
    assert f"UNKNOWN_SUBJECT:{entries[-3].corpus_id}:HISTORY" in errors
    assert f"NOT_ANONYMIZED:{entries[-2].corpus_id}" in errors
    assert f"BAD_SHA256:{entries[-1].corpus_id}" in errors


def test_a_failing_hoa_hoc_paper_fails_06b_but_not_06a_and_a_perfect_toan_run_cannot_cover_it():
    entries = _corpus()
    observations = _observations(entries)
    target = next(e.corpus_id for e in entries if e.subject == "CHEMISTRY" and e.is_held_out)
    observations = [
        dataclasses.replace(o, true_positive_boundaries=2, false_negative_boundaries=8, false_positive_boundaries=3)
        if o.corpus_id == target
        else o
        for o in observations
    ]
    six_b = evaluate_subject_gate("GATE-06b", entries, observations)
    assert not six_b.passed and any(x.startswith("RECALL_BELOW_THRESHOLD") for x in six_b.errors)
    assert target in (six_b.quality.files_below_threshold if six_b.quality else ())
    assert evaluate_subject_gate("GATE-06a", entries, observations).passed


def test_every_paper_of_the_gates_subjects_must_have_been_run_and_nothing_outside_the_manifest():
    entries = _corpus()
    observations = _observations(entries)
    chem = next(e.corpus_id for e in entries if e.subject == "CHEMISTRY" and e.is_held_out)
    without = [o for o in observations if o.corpus_id != chem]
    assert f"MISSING_OBSERVATION:{chem}" in evaluate_subject_gate("GATE-06b", entries, without).errors
    # a Toán paper left out does not matter for 06b
    toan = next(e.corpus_id for e in entries if e.subject == "MATH" and e.is_held_out)
    assert evaluate_subject_gate("GATE-06b", entries, [o for o in observations if o.corpus_id != toan]).passed
    extra = [*observations, dataclasses.replace(observations[0], corpus_id="surprise.pdf")]
    assert "OBSERVATION_NOT_IN_MANIFEST:surprise.pdf" in evaluate_subject_gate("GATE-06b", entries, extra).errors
    flipped = [
        dataclasses.replace(o, is_held_out=not o.is_held_out) if o.corpus_id == chem else o for o in observations
    ]
    assert f"HELD_OUT_FLAG_MISMATCH:{chem}" in evaluate_subject_gate("GATE-06b", entries, flipped).errors


def test_only_the_two_corpus_gates_exist():
    with pytest.raises(ValueError, match="not a corpus gate"):
        evaluate_subject_gate("GATE-05", _corpus(), [])


def test_fixtures_are_rehashed_against_the_manifest(tmp_path: Path):
    entries = _corpus()
    for e in entries:
        (tmp_path / e.corpus_id).write_bytes(b"x")
    # the files hold b"x", the manifest hashes name strings: every file differs...
    assert all(x.startswith("FIXTURE_HASH_MISMATCH") for x in verify_corpus(entries, fixtures_dir=tmp_path).errors)
    # ...until the manifest carries the real digest; a deleted file is reported as missing
    real = [dataclasses.replace(e, sha256=hashlib.sha256(b"x").hexdigest()) for e in entries]
    assert verify_corpus(real, fixtures_dir=tmp_path).ok
    (tmp_path / real[0].corpus_id).unlink()
    assert verify_corpus(real, fixtures_dir=tmp_path).errors == (f"FIXTURE_MISSING:{real[0].corpus_id}",)


def test_manifest_parsing_is_strict():
    for bad in (None, [], {}, ["x"], [{"corpus_id": "a.pdf"}]):
        with pytest.raises(CorpusManifestError):
            parse_manifest(bad)
    good = {"corpus_id": "a.pdf", "subject": "MATH", "is_held_out": True, "sha256": "0" * 64, "anonymized": True}
    assert parse_manifest([good])[0].corpus_id == "a.pdf"
    for patch in ({"is_held_out": "yes"}, {"anonymized": 1}, {"corpus_id": "../a.pdf"}, {"subject": 3}):
        with pytest.raises(CorpusManifestError):
            parse_manifest([{**good, **patch}])


def test_cli_exit_codes_and_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    entries = _corpus()
    manifest = tmp_path / "corpus.json"
    manifest.write_text(json.dumps([dataclasses.asdict(e) for e in entries]), encoding="utf-8")
    assert main(["--manifest", str(manifest)]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["HELD_OUT"] == 30

    runs = tmp_path / "runs.json"
    runs.write_text(json.dumps([dataclasses.asdict(o) for o in _observations(entries)]), encoding="utf-8")
    assert main(["--manifest", str(manifest), "--observations", str(runs), "--gate", "GATE-06b"]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True

    runs.write_text(json.dumps([dataclasses.asdict(o) for o in _observations(entries)[:5]]), encoding="utf-8")
    assert main(["--manifest", str(manifest), "--observations", str(runs), "--gate", "GATE-06b"]) == 1
    assert "MISSING_OBSERVATION" in capsys.readouterr().out

    assert main(["--manifest", str(tmp_path / "nope.json")]) == 2
    manifest.write_text("not json", encoding="utf-8")
    assert main(["--manifest", str(manifest)]) == 2


# --- the papers pulled from the private QA bucket, and 06a and 06b judged on their own ---------------------------


class _FakeBucket:
    """An S3 client over a dict: `fget_object` writes the object where asked, or fails like a missing key."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.asked: list[tuple[str, str]] = []

    def fget_object(self, bucket_name: str, object_name: str, file_path: str) -> None:
        self.asked.append((bucket_name, object_name))
        if object_name not in self.objects:
            raise KeyError(object_name)
        Path(file_path).write_bytes(self.objects[object_name])


def _bucket_world(tmp_path: Path, prefix: str = "corpus/") -> tuple[Path, list[CorpusEntry], _FakeBucket]:
    """A manifest whose digests match the bytes in the fake bucket."""
    entries = [dataclasses.replace(e, sha256=hashlib.sha256(e.corpus_id.encode()).hexdigest()) for e in _corpus()]
    bucket = _FakeBucket({f"{prefix}{e.corpus_id}": e.corpus_id.encode() for e in entries})
    manifest = tmp_path / "corpus.json"
    manifest.write_text(json.dumps([dataclasses.asdict(e) for e in entries]), encoding="utf-8")
    return manifest, entries, bucket


def test_the_41_papers_are_pulled_from_the_qa_bucket_and_re_hashed(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    manifest, entries, bucket = _bucket_world(tmp_path)
    uri = "s3://ulp-qa-golden-private/corpus/"
    assert main(["--manifest", str(manifest), "--fixtures-uri", uri], s3_client=bucket) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert len(bucket.asked) == 41 and {b for b, _ in bucket.asked} == {"ulp-qa-golden-private"}
    assert bucket.asked[0][1].startswith("corpus/")

    # a paper that is not in the bucket, or changed in it, fails the corpus whatever the scores
    del bucket.objects[f"corpus/{entries[0].corpus_id}"]
    bucket.objects[f"corpus/{entries[1].corpus_id}"] = b"tampered"
    assert main(["--manifest", str(manifest), "--fixtures-uri", uri], s3_client=bucket) == 1
    errors = json.loads(capsys.readouterr().out)["errors"]
    assert f"FIXTURE_UNREADABLE:{entries[0].corpus_id}:KeyError" in errors
    assert f"FIXTURE_HASH_MISMATCH:{entries[1].corpus_id}" in errors


def test_only_the_qa_bucket_is_read_and_only_with_credentials_that_are_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    manifest, _entries, bucket = _bucket_world(tmp_path)
    for uri in ("s3://exam-media/corpus/", "https://example.com/x", "s3:///corpus/", "s3://ulp-qa-golden-private/../x"):
        assert main(["--manifest", str(manifest), "--fixtures-uri", uri], s3_client=bucket) == 2, uri
        assert capsys.readouterr().err.startswith("error:")
    assert bucket.asked == [], "a foreign address is refused before a single object is read"

    for name in ("QA_S3_ENDPOINT", "QA_S3_ACCESS_KEY", "QA_S3_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert main(["--manifest", str(manifest), "--fixtures-uri", "s3://ulp-qa-golden-private/"]) == 2
    assert "QA_S3_ENDPOINT" in capsys.readouterr().err, "no built-in credential to fall back on"

    parser_args = ["--manifest", str(manifest), "--fixtures-dir", str(tmp_path), "--fixtures-uri", "s3://x/"]
    with pytest.raises(SystemExit):  # a directory and a bucket at once is a mistake, not a choice
        main(parser_args)


def test_gate_all_judges_06a_and_06b_each_on_its_own_papers(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    entries = _corpus()
    manifest = tmp_path / "corpus.json"
    manifest.write_text(json.dumps([dataclasses.asdict(e) for e in entries]), encoding="utf-8")
    runs = tmp_path / "runs.json"
    observations = _observations(entries)
    runs.write_text(json.dumps([dataclasses.asdict(o) for o in observations]), encoding="utf-8")
    assert main(["--manifest", str(manifest), "--observations", str(runs), "--gate", "all"]) == 0
    both = json.loads(capsys.readouterr().out)
    assert both["passed"] is True and [g["gate"] for g in both["gates"]] == ["GATE-06a", "GATE-06b"]

    # one Hóa học paper falls apart: 06b fails, 06a still passes, and `all` fails
    chemistry = next(e.corpus_id for e in entries if e.subject == "CHEMISTRY" and e.is_held_out)
    broken = [
        dataclasses.replace(o, true_positive_boundaries=0, false_negative_boundaries=10, clean_text_questions=0)
        if o.corpus_id == chemistry
        else o
        for o in observations
    ]
    runs.write_text(json.dumps([dataclasses.asdict(o) for o in broken]), encoding="utf-8")
    assert main(["--manifest", str(manifest), "--observations", str(runs), "--gate", "all"]) == 1
    verdicts = {g["gate"]: g["passed"] for g in json.loads(capsys.readouterr().out)["gates"]}
    assert verdicts == {"GATE-06a": True, "GATE-06b": False}
    assert main(["--manifest", str(manifest), "--observations", str(runs), "--gate", "GATE-06a"]) == 0


def test_a_missing_paper_in_the_bucket_fails_both_gates_even_on_a_perfect_run(tmp_path: Path, capsys):
    manifest, entries, bucket = _bucket_world(tmp_path)
    runs = tmp_path / "runs.json"
    runs.write_text(json.dumps([dataclasses.asdict(o) for o in _observations(entries)]), encoding="utf-8")
    args = [
        "--manifest",
        str(manifest),
        "--observations",
        str(runs),
        "--fixtures-uri",
        "s3://ulp-qa-golden-private/corpus",
    ]
    assert main([*args, "--gate", "all"], s3_client=bucket) == 0
    capsys.readouterr()
    del bucket.objects[f"corpus/{entries[-1].corpus_id}"]
    assert main([*args, "--gate", "all"], s3_client=bucket) == 1
    gates = json.loads(capsys.readouterr().out)["gates"]
    assert [g["passed"] for g in gates] == [False, False], "a gate on a corpus that cannot be read is no gate"


def test_a_corpus_id_cannot_walk_out_of_the_fixtures_directory():
    good = {"corpus_id": "a.pdf", "subject": "MATH", "is_held_out": True, "sha256": "0" * 64, "anonymized": True}
    backslash = chr(92)
    for name in ("..", ".hidden.pdf", f"a{backslash}b.pdf", " a.pdf", "a/b.pdf", f"a{chr(0)}.pdf", f"a{chr(9)}b.pdf"):
        with pytest.raises(CorpusManifestError):
            parse_manifest([{**good, "corpus_id": name}])
