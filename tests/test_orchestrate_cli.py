"""Tests for the Parallel Wave Orchestrator (`scripts/orchestrate.py`)."""

from __future__ import annotations

from scripts.orchestrate import (
    WAVES,
    build_agent_prompt,
    check_paths_against_lane,
    get_wave,
    main,
)


def test_all_eight_waves_have_disjoint_module_and_core_lanes() -> None:
    assert len(WAVES) == 8
    all_tasks: set[str] = set()
    for wave in WAVES:
        all_tasks.update(wave.claude.tasks)
        all_tasks.update(wave.gemini.tasks)
        assert wave.claude.branch != wave.gemini.branch
        # Gemini is never allowed to touch alembic/versions/, app/model_registry.py, or app/main.py
        ok_alembic, _ = check_paths_against_lane(wave.gemini, ["alembic/versions/001_new.py"])
        ok_reg, _ = check_paths_against_lane(wave.gemini, ["app/model_registry.py"])
        ok_main, _ = check_paths_against_lane(wave.gemini, ["app/main.py"])
        assert not ok_alembic and not ok_reg and not ok_main

    # All 36 sequential tasks TASK-01..TASK-36 are covered across the 8 waves
    expected = {f"TASK-{i:02d}" for i in range(1, 37)}
    assert expected == all_tasks


def test_wave1_lane_boundaries_enforce_strict_separation() -> None:
    w1 = get_wave("1")
    assert w1.wave_id == "WAVE-1"

    # Claude can edit pdf_preview.py and requirements.txt in WAVE-1, but not frontend/ or outbox/
    ok, errs = check_paths_against_lane(
        w1.claude,
        ["app/application/pdf_preview.py", "requirements.txt", "tests/test_pdf_engine.py"],
    )
    assert ok and not errs

    ok_bad, errs_bad = check_paths_against_lane(w1.claude, ["app/modules/outbox/router.py"])
    assert not ok_bad and "FORBIDDEN_PATH" in errs_bad[0]

    # Gemini can edit outbox/taxonomy/curriculum in WAVE-1, but not pdf_preview.py or alembic/
    ok_g, errs_g = check_paths_against_lane(
        w1.gemini,
        ["app/modules/outbox/router.py", "app/modules/taxonomy/router.py", "tests/test_outbox_api.py"],
    )
    assert ok_g and not errs_g

    ok_g_bad, errs_g_bad = check_paths_against_lane(w1.gemini, ["app/application/pdf_preview.py"])
    assert not ok_g_bad and "OUT_OF_LANE_PATH" in errs_g_bad[0]


def test_cli_list_and_prompt_commands(capsys) -> None:
    rc_list = main(["list"])
    assert rc_list == 0
    out_list = capsys.readouterr().out
    assert "WAVE-1" in out_list and "WAVE-8" in out_list

    rc_prompt = main(["prompt", "WAVE-1", "--agent", "both"])
    assert rc_prompt == 0
    out_prompt = capsys.readouterr().out
    assert "TASK-01 -> TASK-02 -> TASK-03" in out_prompt
    assert "TASK-04 -> TASK-10" in out_prompt

    prompt_text = build_agent_prompt(get_wave("WAVE-2"), "claude")
    assert "TASK-05 -> TASK-06 -> TASK-07" in prompt_text
    assert "Claude Opus / Fable" in prompt_text
