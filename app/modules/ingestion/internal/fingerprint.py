"""Streaming chunked SHA-256 fingerprinting and ingestion duplicate detection."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import BinaryIO

DEFAULT_CHUNK_SIZE = 65_536


@dataclass(frozen=True, slots=True)
class StreamFingerprint:
    """Content digest + byte size computed without buffering an entire stream into RAM."""

    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class DuplicateDecision[T]:
    """Pure decision telling ingestion whether to reuse an existing document record."""

    is_duplicate: bool
    fingerprint: StreamFingerprint
    existing_record: T | None = None


def compute_stream_fingerprint(
    stream: BinaryIO | Iterable[bytes | bytearray | memoryview],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> StreamFingerprint:
    """
    Compute streaming SHA-256 hex digest and total byte size from a file-like stream or byte-chunk iterable.

    Reads strictly chunk-by-chunk so large documents never need full-file buffering in memory.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")

    hasher = hashlib.sha256()
    total_bytes = 0

    read_fn = getattr(stream, "read", None)
    if callable(read_fn):
        while True:
            chunk = read_fn(chunk_size)
            if not chunk:
                break
            view = memoryview(chunk)
            hasher.update(view)
            total_bytes += len(view)
    else:
        for chunk in stream:
            if not chunk:
                continue
            view = memoryview(chunk)
            hasher.update(view)
            total_bytes += len(view)

    return StreamFingerprint(sha256=hasher.hexdigest(), size_bytes=total_bytes)


def decide_duplicate_ingest[T](
    fingerprint: StreamFingerprint,
    existing_by_sha256: Mapping[str, T],
) -> DuplicateDecision[T]:
    """
    Check whether a computed SHA-256 already exists in the tenant's document catalog.

    Returns `DuplicateDecision(is_duplicate=True, existing_record=...)` when matched so
    callers can return the existing document version idempotently instead of duplicating storage.
    """
    normalized_digest = fingerprint.sha256.strip().lower()
    existing = existing_by_sha256.get(normalized_digest)
    if existing is not None:
        return DuplicateDecision(
            is_duplicate=True,
            fingerprint=fingerprint,
            existing_record=existing,
        )
    return DuplicateDecision(
        is_duplicate=False,
        fingerprint=fingerprint,
        existing_record=None,
    )
