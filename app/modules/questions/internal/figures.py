"""Figures of a question: cut out of the source PDF, stored as FIGURE_CROP assets, shown with the stem.

The PDF reader (`parsers`) already knows where every figure of a question is (`figure_bboxes`: page number and a box in
PDF points). This module renders each box from the PDF itself (no model is asked to draw anything), keeps the PNGs as
tenant-private `FIGURE_CROP` assets and gives back the `media` entries the stem carries:

    stem_json = {"text": "...", "media": [{"type": "image", "asset_id": "...", "alt": "Hình 1"}]}

`media`, `asset_id` and `alt` are on the exam-room allowlist, so the sanitized paper carries them to the student; the
asset is read through its signed URL. The same box list also gives the model a picture of the question (see
`render_question_image`), because a vision model reads formulas and diagrams far better than the PDF's text layer.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.pdf_engine import PDFEngineAdapter, PDFEngineError, PDFPasswordRequired, Rect
from app.modules.assets.public import (
    AssetVisibility,
    VirusScanStatus,
    finalize_asset_upload,
    initiate_asset_upload_session,
)

FIGURE_DPI = 200.0
QUESTION_IMAGE_DPI = 150.0
MIN_FIGURE_POINTS = 14.0  # a box smaller than this on a side is a stray mark, not a figure
MAX_FIGURES_PER_QUESTION = 6
FIGURE_PAD_POINTS = 3.0


def _rect(box: Mapping[str, Any], pad: float = 0.0) -> Rect | None:
    try:
        x0, y0, x1, y1 = (float(box[k]) for k in ("x0", "y0", "x1", "y1"))
    except (KeyError, TypeError, ValueError):
        return None
    if x1 - x0 < MIN_FIGURE_POINTS or y1 - y0 < MIN_FIGURE_POINTS:
        return None
    return Rect(max(0.0, x0 - pad), max(0.0, y0 - pad), x1 + pad, y1 + pad)


def crop_figures(
    pdf_bytes: bytes, figure_bboxes: Sequence[Mapping[str, Any]], *, dpi: float = FIGURE_DPI
) -> list[bytes]:
    """PNGs of the figure boxes of one question (blocking; run it in a thread). A box that cannot be rendered is
    skipped: a question without its figure is still a question, and the caller reports how many were cut."""
    if not figure_bboxes:
        return []
    out: list[bytes] = []
    seen: set[tuple[int, int, int, int, int]] = set()
    try:
        with PDFEngineAdapter.open(pdf_bytes) as pdf:
            for box in figure_bboxes[:MAX_FIGURES_PER_QUESTION]:
                rect = _rect(box, FIGURE_PAD_POINTS)
                page_no = int(box.get("page_no") or 0)
                if rect is None or not 1 <= page_no <= pdf.page_count:
                    continue
                key = (page_no, round(rect.x0), round(rect.y0), round(rect.x1), round(rect.y1))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    out.append(pdf.render_png(page_no - 1, dpi, rect))
                except PDFEngineError:
                    continue
    except (PDFEngineError, PDFPasswordRequired):
        return []
    return out


def render_question_image(
    pdf_bytes: bytes, page_number: int, bbox: Mapping[str, Any], *, dpi: float = QUESTION_IMAGE_DPI
) -> bytes | None:
    """A picture of the whole question (stem, options, figure) for a vision model; None when it cannot be cut."""
    rect = _rect(bbox, 4.0)
    if rect is None:
        return None
    try:
        with PDFEngineAdapter.open(pdf_bytes) as pdf:
            if not 1 <= page_number <= pdf.page_count:
                return None
            return pdf.render_png(page_number - 1, dpi, rect)
    except (PDFEngineError, PDFPasswordRequired):
        return None


async def store_figure_assets(
    session: AsyncSession, *, tenant_id: uuid.UUID, pngs: Sequence[bytes], correlation_id: str = "question-figures"
) -> list[uuid.UUID]:
    """Keep the PNGs as tenant-private FIGURE_CROP assets (the same picture twice is one asset)."""
    ids: list[uuid.UUID] = []
    for png in pngs:
        sha256 = hashlib.sha256(png).hexdigest()
        init = await initiate_asset_upload_session(
            session,
            tenant_id=tenant_id,
            mime_type="image/png",
            sha256=sha256,
            size_bytes=len(png),
            filename=f"figure-{sha256[:12]}.png",
            asset_role="FIGURE_CROP",
            requires_pro=False,
            visibility=AssetVisibility.TENANT_PRIVATE,
            deduplicate=True,
        )
        if init.deduplicated:
            asset_id = init.asset.id
        else:
            fin = await finalize_asset_upload(
                session,
                tenant_id=tenant_id,
                asset_id=init.asset.id,
                observed_sha256=sha256,
                observed_mime_type="image/png",
                observed_size_bytes=len(png),
                scan_status=VirusScanStatus.CLEAN,
                raw_bytes=png,
                correlation_id=correlation_id,
            )
            asset_id = fin.asset.id
        if asset_id not in ids:
            ids.append(asset_id)
    return ids


def media_for(asset_ids: Sequence[uuid.UUID]) -> list[dict[str, str]]:
    return [{"type": "image", "asset_id": str(a), "alt": f"Hình {n}"} for n, a in enumerate(asset_ids, start=1)]


def pictures_for(pdf_bytes: bytes, drafts: Sequence[Any]) -> list[tuple[bytes | None, tuple[bytes, ...]]]:
    """For each extracted question (anything with `page_number`, `bbox_json`, `figure_bboxes`): the picture of the
    question and the PNGs of its figures. Blocking: run it in a thread. A question that has none gets (None, ())."""
    out: list[tuple[bytes | None, tuple[bytes, ...]]] = []
    for draft in drafts:
        image = render_question_image(pdf_bytes, int(draft.page_number), draft.bbox_json or {})
        out.append((image, tuple(crop_figures(pdf_bytes, draft.figure_bboxes or ()))))
    return out
