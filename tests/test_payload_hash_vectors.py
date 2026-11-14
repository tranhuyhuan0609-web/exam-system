"""Cross-language payload hash vector verification against frontend fixtures (pure, no conftest)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.contracts.autosave import canonical_json, compute_payload_hash

_VECTOR_PATH = (
    Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "__fixtures__" / "payload-hash-vectors.json"
)
_VECTORS = json.loads(_VECTOR_PATH.read_text(encoding="utf-8"))


def test_payload_hash_vector_fixture_has_at_least_20_cases() -> None:
    assert len(_VECTORS) >= 20


@pytest.mark.parametrize("vector", _VECTORS, ids=[v["name"] for v in _VECTORS])
def test_python_canonical_json_and_hash_match_shared_vectors(vector: dict) -> None:
    payload = vector["payload"]
    assert canonical_json(payload) == vector["expected_canonical_json"]
    assert compute_payload_hash(payload) == vector["expected_sha256"]
