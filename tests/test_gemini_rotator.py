"""Tests for Gemini multi-key rotation and pool management."""

import pytest

from app.ai.key_rotator import GeminiKeyPool, mask_key, parse_keys


def test_parse_keys():
    # Comma separated
    keys = parse_keys("key1,key2,key3")
    assert keys == ["key1", "key2", "key3"]

    # Mixed separators (semicolon, newline, whitespace) and deduplication
    raw = " key1; key2\nkey3 , key2 \t key4 "
    assert parse_keys(raw) == ["key1", "key2", "key3", "key4"]

    # List input
    assert parse_keys(["keyA,keyB", "keyC"]) == ["keyA", "keyB", "keyC"]
    assert parse_keys("") == []
    assert parse_keys(None) == []


def test_mask_key():
    assert mask_key("AIzaSy1234567890abcdef") == "AIzaSy12...cdef"
    assert mask_key("short") == "sho...rt"
    assert mask_key("") == "<empty>"


@pytest.mark.anyio
async def test_key_pool_round_robin():
    pool = GeminiKeyPool(["key1", "key2", "key3"])
    assert pool.total_keys == 3
    assert not pool.is_empty()

    # Round robin turns
    client1, k1 = await pool.get_active_client()
    client2, k2 = await pool.get_active_client()
    client3, k3 = await pool.get_active_client()
    client4, k4 = await pool.get_active_client()

    keys_chosen = [k1, k2, k3, k4]
    assert keys_chosen == ["key2", "key3", "key1", "key2"]


@pytest.mark.anyio
async def test_key_pool_exhaustion_and_cooldown():
    pool = GeminiKeyPool(["keyA", "keyB", "keyC"])

    # Mark keyA as exhausted
    await pool.mark_exhausted("keyA", cooldown_minutes=30, reason="Quota exceeded 429")

    # Available keys should now only be keyB and keyC
    avail = await pool.get_all_available_keys()
    assert "keyA" not in avail
    assert set(avail) == {"keyB", "keyC"}

    # Repeated calls should only pick from keyB and keyC
    for _ in range(4):
        _, key = await pool.get_active_client()
        assert key in ("keyB", "keyC")

    # Mark keyB as exhausted
    await pool.mark_exhausted("keyB", cooldown_minutes=30)
    avail = await pool.get_all_available_keys()
    assert avail == ["keyC"]

    # Only keyC remains
    _, key = await pool.get_active_client()
    assert key == "keyC"

    # Recover keyA
    await pool.mark_recovered("keyA")
    avail = await pool.get_all_available_keys()
    assert set(avail) == {"keyA", "keyC"}


@pytest.mark.anyio
async def test_key_pool_status_summary():
    pool = GeminiKeyPool(["AIzaSyKey1111111111", "AIzaSyKey2222222222"])
    summary = pool.status_summary()
    assert summary["total"] == 2
    assert summary["available_count"] == 2
    assert summary["cooldown_count"] == 0

    await pool.mark_exhausted("AIzaSyKey1111111111", cooldown_minutes=15)
    summary2 = pool.status_summary()
    assert summary2["available_count"] == 1
    assert summary2["cooldown_count"] == 1


def test_openai_compat_multi_key_rotation():
    from app.pipeline.adapters.ai_chain import OpenAICompatEnricher

    # Multi-key Groq enricher initialization
    enricher = OpenAICompatEnricher(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key="gsk_key1, gsk_key2; gsk_key3",
        model="llama3-70b",
    )
    assert len(enricher._keys) == 3
    assert enricher._keys == ["gsk_key1", "gsk_key2", "gsk_key3"]

