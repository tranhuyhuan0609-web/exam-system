"""Database service layer for A06 OCR & Layout Segmentation Pipeline (PDFEngine + Pure Core)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.events import EventEnvelopeContract
from app.infrastructure.pdf_engine import PDFEngineAdapter, PDFEngineError, PDFPasswordRequired, Rect
from app.infrastructure.storage import Storage, get_bronze_storage
from app.modules.assets.public import (
    AssetVisibility,
    VirusScanStatus,
    finalize_asset_upload,
    get_asset_by_id,
    initiate_asset_upload_session,
)
from app.modules.layout.internal.geometry import BBoxRect, merge_intersecting_rects_union_find
from app.modules.layout.internal.margins import (
    LayoutProfileConfig,
    PageLayoutInput,
    PageTextLine,
    RepeatingMarginDetection,
    detect_dynamic_repeating_margins,
    normalize_margin_text,
)
from app.modules.layout.internal.models import ExtractedPage, LayoutRegion
from app.modules.outbox.public import enqueue
from app.modules.parsers.public import get_extraction_run


class LayoutValidationError(ValueError):
    """Raised when layout segmentation inputs or page coordinates are invalid."""


class OcrJobNotFoundError(LookupError):
    """Raised when an OCR/layout job (extraction_run) is missing or outside caller tenant."""


@dataclass(frozen=True, slots=True)
class LayoutRegionView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    extracted_page_id: uuid.UUID
    region_type: str
    bbox_json: dict[str, Any]
    cropped_asset_id: uuid.UUID | None
    extracted_text: str | None


@dataclass(frozen=True, slots=True)
class ExtractedPageView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    extraction_run_id: uuid.UUID
    page_no: int
    render_asset_id: uuid.UUID | None
    width_pt: float
    height_pt: float
    detected_header_y0: float | None
    detected_footer_y1: float | None
    raw_text: str | None
    regions: tuple[LayoutRegionView, ...]


@dataclass(frozen=True, slots=True)
class OcrLayoutJobView:
    job_id: uuid.UUID
    extraction_run_id: uuid.UUID
    tenant_id: uuid.UUID
    document_version_id: uuid.UUID
    profile_code: str
    parser_version: str
    pages: tuple[ExtractedPageView, ...]


def _decimal_pt(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(f"{value:.2f}")


def _bbox_dict(x0: float, y0: float, x1: float, y1: float) -> dict[str, float]:
    return {
        "x0": round(float(x0), 2),
        "y0": round(float(y0), 2),
        "x1": round(float(x1), 2),
        "y1": round(float(y1), 2),
    }


def _to_region_view(row: LayoutRegion) -> LayoutRegionView:
    return LayoutRegionView(
        id=row.id,
        tenant_id=row.tenant_id,
        extracted_page_id=row.extracted_page_id,
        region_type=row.region_type,
        bbox_json=dict(row.bbox_json or {}),
        cropped_asset_id=row.cropped_asset_id,
        extracted_text=row.extracted_text,
    )


def _to_page_view(row: ExtractedPage, regions: list[LayoutRegion]) -> ExtractedPageView:
    return ExtractedPageView(
        id=row.id,
        tenant_id=row.tenant_id,
        extraction_run_id=row.extraction_run_id,
        page_no=row.page_no,
        render_asset_id=row.render_asset_id,
        width_pt=float(row.width_pt),
        height_pt=float(row.height_pt),
        detected_header_y0=(
            float(row.detected_header_y0) if row.detected_header_y0 is not None else None
        ),
        detected_footer_y1=(
            float(row.detected_footer_y1) if row.detected_footer_y1 is not None else None
        ),
        raw_text=row.raw_text,
        regions=tuple(_to_region_view(r) for r in regions),
    )


async def _store_figure_crop_asset(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    png_bytes: bytes,
    filename: str,
    correlation_id: str,
    storage: Storage,
) -> uuid.UUID:
    sha256 = hashlib.sha256(png_bytes).hexdigest()
    init_res = await initiate_asset_upload_session(
        session,
        tenant_id=tenant_id,
        mime_type="image/png",
        sha256=sha256,
        size_bytes=len(png_bytes),
        filename=filename,
        asset_role="FIGURE_CROP",
        requires_pro=False,
        visibility=AssetVisibility.TENANT_PRIVATE,
        deduplicate=True,
    )
    if init_res.deduplicated:
        return init_res.asset.id

    fin_res = await finalize_asset_upload(
        session,
        tenant_id=tenant_id,
        asset_id=init_res.asset.id,
        observed_sha256=sha256,
        observed_mime_type="image/png",
        observed_size_bytes=len(png_bytes),
        scan_status=VirusScanStatus.CLEAN,
        raw_bytes=png_bytes,
        correlation_id=correlation_id,
        storage=storage,
    )
    return fin_res.asset.id


def _collect_page_inputs(
    pdf: PDFEngineAdapter,
) -> tuple[list[PageLayoutInput], list[tuple[float, float, str]]]:
    inputs: list[PageLayoutInput] = []
    meta: list[tuple[float, float, str]] = []
    for idx in range(pdf.page_count):
        width_pt, height_pt = pdf.page_size(idx)
        lines = pdf.lines(idx)
        raw_text = pdf.text(idx)
        inputs.append(
            PageLayoutInput(
                page_index=idx,
                page_width=width_pt,
                page_height=height_pt,
                lines=tuple(
                    PageTextLine(
                        text=ln.text,
                        bbox=BBoxRect(ln.rect.x0, ln.rect.y0, ln.rect.x1, ln.rect.y1),
                    )
                    for ln in lines
                ),
            )
        )
        meta.append((width_pt, height_pt, raw_text))
    return inputs, meta


def _classify_margin_cutoffs(
    *,
    page_layout: PageLayoutInput,
    margins: RepeatingMarginDetection,
    cfg: LayoutProfileConfig,
) -> tuple[float | None, float | None, list[tuple[str, PageTextLine]], list[Rect]]:
    """Classify dropped repeating margin lines on one page into HEADER/FOOTER/watermark."""
    dropped = margins.dropped_lines_by_page.get(page_layout.page_index, ())
    if not dropped:
        return None, None, [], []

    top_limit = page_layout.page_height * cfg.header_band_ratio
    bottom_limit = page_layout.page_height * (1.0 - cfg.footer_band_ratio)
    header_y1_vals: list[float] = []
    footer_y0_vals: list[float] = []
    typed_regions: list[tuple[str, PageTextLine]] = []
    exclude_rects: list[Rect] = []

    for line in dropped:
        norm = normalize_margin_text(line.text)
        exclude_rects.append(Rect(line.bbox.x0, line.bbox.y0, line.bbox.x1, line.bbox.y1))
        if line.bbox.y0 <= top_limit and norm in margins.header_patterns:
            header_y1_vals.append(line.bbox.y1)
            typed_regions.append(("HEADER", line))
        elif line.bbox.y1 >= bottom_limit and norm in margins.footer_patterns:
            footer_y0_vals.append(line.bbox.y0)
            typed_regions.append(("FOOTER", line))

    detected_header_y0 = max(header_y1_vals) if header_y1_vals else None
    detected_footer_y1 = min(footer_y0_vals) if footer_y0_vals else None
    return detected_header_y0, detected_footer_y1, typed_regions, exclude_rects


async def _populate_single_page_regions(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    pdf: PDFEngineAdapter,
    page_row: ExtractedPage,
    page_idx: int,
    width_pt: float,
    height_pt: float,
    typed_margin_lines: list[tuple[str, PageTextLine]],
    exclude_rects: list[Rect],
    cfg: LayoutProfileConfig,
    crop_figures: bool,
    correlation_id: str,
    storage: Storage,
) -> list[LayoutRegion]:
    regions: list[LayoutRegion] = []

    for region_type, line in typed_margin_lines:
        row = LayoutRegion(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            extracted_page_id=page_row.id,
            region_type=region_type,
            bbox_json=_bbox_dict(line.bbox.x0, line.bbox.y0, line.bbox.x1, line.bbox.y1),
            extracted_text=line.text,
        )
        session.add(row)
        regions.append(row)

    # Tables: verify Table.rows contains text so overlapping vector figure rects are not misclassified as tables
    real_tables = [
        table
        for table in pdf.tables(page_idx)
        if any((cell or "").strip() for row in table.rows for cell in row)
    ]
    for table in real_tables:
        row = LayoutRegion(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            extracted_page_id=page_row.id,
            region_type="TABLE",
            bbox_json=_bbox_dict(table.rect.x0, table.rect.y0, table.rect.x1, table.rect.y1),
            extracted_text="\n".join(
                " | ".join(cell or "" for cell in r) for r in table.rows
            ),
        )
        session.add(row)
        regions.append(row)

    # Figures via PDFEngineAdapter graphics + Pure Core merge_intersecting_rects_union_find
    real_table_rects = [t.rect.expanded(4.0) for t in real_tables]
    raw_drawing_rects = [
        BBoxRect(r.x0, r.y0, r.x1, r.y1)
        for r in pdf.drawing_rects(page_idx, include_tables=True)
        if r.x1 >= r.x0
        and r.y1 >= r.y0
        and not any(r.overlap_ratio(t) >= 0.5 for t in real_table_rects)
    ]
    clustered_figures = merge_intersecting_rects_union_find(
        raw_drawing_rects,
        pad=cfg.cluster_padding_pt,
    )
    page_area = max(1.0, width_pt * height_pt)
    for fig_idx, fig in enumerate(clustered_figures, start=1):
        cx0 = max(0.0, min(width_pt, fig.x0))
        cy0 = max(0.0, min(height_pt, fig.y0))
        cx1 = max(cx0, min(width_pt, fig.x1))
        cy1 = max(cy0, min(height_pt, fig.y1))
        cw, ch = cx1 - cx0, cy1 - cy0
        if cw < cfg.min_figure_width or ch < cfg.min_figure_height:
            continue
        # Drop tiny isolated corner badges in header/footer margin bands
        in_margin_band = cy1 <= height_pt * cfg.header_band_ratio or cy0 >= height_pt * (1.0 - cfg.footer_band_ratio)
        if in_margin_band and (cw * ch) / page_area <= cfg.max_badge_area_ratio:
            continue

        cropped_asset_id: uuid.UUID | None = None
        if crop_figures:
            png_bytes = pdf.render_png(page_idx, dpi=150, clip=Rect(cx0, cy0, cx1, cy1))
            cropped_asset_id = await _store_figure_crop_asset(
                session,
                tenant_id=tenant_id,
                png_bytes=png_bytes,
                filename=f"run_{page_row.extraction_run_id}_p{page_row.page_no}_fig{fig_idx}.png",
                correlation_id=correlation_id,
                storage=storage,
            )

        fig_row = LayoutRegion(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            extracted_page_id=page_row.id,
            region_type="FIGURE",
            bbox_json=_bbox_dict(cx0, cy0, cx1, cy1),
            cropped_asset_id=cropped_asset_id,
            extracted_text=None,
        )
        session.add(fig_row)
        regions.append(fig_row)

    # Question / content text blocks (with detected repeating margins excluded)
    for block in pdf.blocks(page_idx, exclude=exclude_rects):
        cleaned = block.text.strip()
        if not cleaned:
            continue
        block_row = LayoutRegion(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            extracted_page_id=page_row.id,
            region_type="QUESTION_BLOCK",
            bbox_json=_bbox_dict(block.rect.x0, block.rect.y0, block.rect.x1, block.rect.y1),
            extracted_text=cleaned,
        )
        session.add(block_row)
        regions.append(block_row)

    await session.flush()
    return regions


async def _resolve_pdf_bytes(
    session: AsyncSession,
    *,
    raw_asset_id: uuid.UUID,
    raw_pdf_bytes: bytes | None,
    storage: Storage,
) -> bytes:
    if raw_pdf_bytes is not None:
        return raw_pdf_bytes
    asset = await get_asset_by_id(session, asset_id=raw_asset_id)
    return await storage.get(asset.object_key)


async def run_ocr_layout_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    extraction_run_id: uuid.UUID,
    config: LayoutProfileConfig | None = None,
    crop_figures: bool = True,
    raw_pdf_bytes: bytes | None = None,
    correlation_id: str = "a06-ocr-layout-job",
    storage: Storage | None = None,
) -> OcrLayoutJobView:
    """Run multi-page PDF layout segmentation, union-find figure clustering, and margin detection."""
    run_ctx = await get_extraction_run(session, run_id=extraction_run_id)
    active_storage = storage or get_bronze_storage()
    pdf_bytes = await _resolve_pdf_bytes(
        session,
        raw_asset_id=run_ctx.raw_asset_id,
        raw_pdf_bytes=raw_pdf_bytes,
        storage=active_storage,
    )
    cfg = config if config is not None else LayoutProfileConfig()

    # Clear existing ExtractedPage rows for idempotent re-execution on the same ExtractionRun
    await session.execute(
        delete(ExtractedPage).where(ExtractedPage.extraction_run_id == extraction_run_id)
    )
    await session.flush()

    page_views: list[ExtractedPageView] = []
    try:
        with PDFEngineAdapter.open(pdf_bytes) as pdf:
            page_inputs, page_meta = _collect_page_inputs(pdf)
            margins = detect_dynamic_repeating_margins(page_inputs, config=cfg)

            for idx, page_input in enumerate(page_inputs):
                width_pt, height_pt, raw_text = page_meta[idx]
                header_y0, footer_y1, typed_margins, exclude_rects = _classify_margin_cutoffs(
                    page_layout=page_input,
                    margins=margins,
                    cfg=cfg,
                )
                page_row = ExtractedPage(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    extraction_run_id=extraction_run_id,
                    page_no=idx + 1,
                    width_pt=_decimal_pt(width_pt),  # type: ignore[arg-type]
                    height_pt=_decimal_pt(height_pt),  # type: ignore[arg-type]
                    detected_header_y0=_decimal_pt(header_y0),
                    detected_footer_y1=_decimal_pt(footer_y1),
                    raw_text=raw_text,
                )
                session.add(page_row)
                await session.flush()

                regions = await _populate_single_page_regions(
                    session,
                    tenant_id=tenant_id,
                    pdf=pdf,
                    page_row=page_row,
                    page_idx=idx,
                    width_pt=width_pt,
                    height_pt=height_pt,
                    typed_margin_lines=typed_margins,
                    exclude_rects=exclude_rects,
                    cfg=cfg,
                    crop_figures=crop_figures,
                    correlation_id=correlation_id,
                    storage=active_storage,
                )
                page_views.append(_to_page_view(page_row, regions))
    except PDFPasswordRequired as exc:
        raise LayoutValidationError("password-protected PDF cannot be segmented") from exc
    except PDFEngineError as exc:
        raise LayoutValidationError(f"failed to read PDF for layout analysis: {exc}") from exc

    await enqueue(
        session,
        EventEnvelopeContract(
            event_type="layout.ocr_job_completed",
            tenant_id=tenant_id,
            aggregate_type="extraction_run",
            aggregate_id=extraction_run_id,
            correlation_id=correlation_id,
            payload={
                "extraction_run_id": str(extraction_run_id),
                "page_count": len(page_views),
                "region_count": sum(len(p.regions) for p in page_views),
            },
        ),
    )

    return OcrLayoutJobView(
        job_id=extraction_run_id,
        extraction_run_id=extraction_run_id,
        tenant_id=tenant_id,
        document_version_id=run_ctx.document_version_id,
        profile_code=run_ctx.profile_code,
        parser_version=run_ctx.parser_version,
        pages=tuple(page_views),
    )


async def get_ocr_layout_job(
    session: AsyncSession,
    *,
    extraction_run_id: uuid.UUID,
) -> OcrLayoutJobView:
    """Fetch persisted ExtractedPages and LayoutRegions for an ExtractionRun under active RLS."""
    try:
        run_ctx = await get_extraction_run(session, run_id=extraction_run_id)
    except Exception as exc:
        raise OcrJobNotFoundError(f"OCR layout job {extraction_run_id} not found") from exc

    page_rows = (
        await session.execute(
            select(ExtractedPage)
            .where(ExtractedPage.extraction_run_id == extraction_run_id)
            .order_by(ExtractedPage.page_no.asc())
        )
    ).scalars().all()

    page_ids = [p.id for p in page_rows]
    regions_by_page: dict[uuid.UUID, list[LayoutRegion]] = {pid: [] for pid in page_ids}
    if page_ids:
        region_rows = (
            await session.execute(
                select(LayoutRegion)
                .where(LayoutRegion.extracted_page_id.in_(page_ids))
                .order_by(LayoutRegion.region_type.asc(), LayoutRegion.id.asc())
            )
        ).scalars().all()
        for reg in region_rows:
            regions_by_page.setdefault(reg.extracted_page_id, []).append(reg)

    return OcrLayoutJobView(
        job_id=extraction_run_id,
        extraction_run_id=extraction_run_id,
        tenant_id=run_ctx.tenant_id,
        document_version_id=run_ctx.document_version_id,
        profile_code=run_ctx.profile_code,
        parser_version=run_ctx.parser_version,
        pages=tuple(_to_page_view(p, regions_by_page.get(p.id, [])) for p in page_rows),
    )


async def reprocess_ocr_layout_page(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    extraction_run_id: uuid.UUID,
    page_no: int,
    config: LayoutProfileConfig | None = None,
    crop_figures: bool = True,
    raw_pdf_bytes: bytes | None = None,
    correlation_id: str = "a06-ocr-page-reprocess",
    storage: Storage | None = None,
) -> ExtractedPageView:
    """Reprocess layout segmentation & regions for strictly one 1-based `page_no`."""
    if isinstance(page_no, bool) or not isinstance(page_no, int) or page_no < 1:
        raise LayoutValidationError("page must be a positive integer >= 1")

    try:
        run_ctx = await get_extraction_run(session, run_id=extraction_run_id)
    except Exception as exc:
        raise OcrJobNotFoundError(f"OCR layout job {extraction_run_id} not found") from exc

    active_storage = storage or get_bronze_storage()
    pdf_bytes = await _resolve_pdf_bytes(
        session,
        raw_asset_id=run_ctx.raw_asset_id,
        raw_pdf_bytes=raw_pdf_bytes,
        storage=active_storage,
    )
    cfg = config if config is not None else LayoutProfileConfig()

    try:
        with PDFEngineAdapter.open(pdf_bytes) as pdf:
            if page_no > pdf.page_count:
                raise LayoutValidationError(
                    f"requested page {page_no} exceeds document page_count {pdf.page_count}"
                )
            page_inputs, page_meta = _collect_page_inputs(pdf)
            margins = detect_dynamic_repeating_margins(page_inputs, config=cfg)

            target_idx = page_no - 1
            page_input = page_inputs[target_idx]
            width_pt, height_pt, raw_text = page_meta[target_idx]
            header_y0, footer_y1, typed_margins, exclude_rects = _classify_margin_cutoffs(
                page_layout=page_input,
                margins=margins,
                cfg=cfg,
            )

            existing_page = (
                await session.execute(
                    select(ExtractedPage).where(
                        ExtractedPage.extraction_run_id == extraction_run_id,
                        ExtractedPage.page_no == page_no,
                    )
                )
            ).scalar_one_or_none()

            if existing_page is None:
                page_row = ExtractedPage(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    extraction_run_id=extraction_run_id,
                    page_no=page_no,
                    width_pt=_decimal_pt(width_pt),  # type: ignore[arg-type]
                    height_pt=_decimal_pt(height_pt),  # type: ignore[arg-type]
                    detected_header_y0=_decimal_pt(header_y0),
                    detected_footer_y1=_decimal_pt(footer_y1),
                    raw_text=raw_text,
                )
                session.add(page_row)
                await session.flush()
            else:
                page_row = existing_page
                await session.execute(
                    delete(LayoutRegion).where(LayoutRegion.extracted_page_id == page_row.id)
                )
                page_row.width_pt = _decimal_pt(width_pt)  # type: ignore[assignment]
                page_row.height_pt = _decimal_pt(height_pt)  # type: ignore[assignment]
                page_row.detected_header_y0 = _decimal_pt(header_y0)
                page_row.detected_footer_y1 = _decimal_pt(footer_y1)
                page_row.raw_text = raw_text
                await session.flush()

            regions = await _populate_single_page_regions(
                session,
                tenant_id=tenant_id,
                pdf=pdf,
                page_row=page_row,
                page_idx=target_idx,
                width_pt=width_pt,
                height_pt=height_pt,
                typed_margin_lines=typed_margins,
                exclude_rects=exclude_rects,
                cfg=cfg,
                crop_figures=crop_figures,
                correlation_id=correlation_id,
                storage=active_storage,
            )
    except LayoutValidationError:
        raise
    except PDFPasswordRequired as exc:
        raise LayoutValidationError("password-protected PDF cannot be reprocessed") from exc
    except PDFEngineError as exc:
        raise LayoutValidationError(f"failed to read PDF page {page_no}: {exc}") from exc

    await enqueue(
        session,
        EventEnvelopeContract(
            event_type="layout.page_reprocessed",
            tenant_id=tenant_id,
            aggregate_type="extracted_page",
            aggregate_id=page_row.id,
            correlation_id=correlation_id,
            payload={
                "extraction_run_id": str(extraction_run_id),
                "extracted_page_id": str(page_row.id),
                "page_no": page_no,
                "region_count": len(regions),
            },
        ),
    )

    return _to_page_view(page_row, regions)
