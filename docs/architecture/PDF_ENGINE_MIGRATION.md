# PDF engine migration: PyMuPDF (AGPLv3) to pypdfium2 + pdfplumber (ADR-010, task T0.7)

Status: adapter done; PR A (read-only callers) migrated, see "Migration status" below. `pymupdf` is still a dependency.
The adapter is `app/infrastructure/pdf_engine.py` (`PDFEngineAdapter`), tests in `tests/test_pdf_engine.py`.

Licences: pypdfium2 Apache-2.0 / BSD-3 (bundles PDFium, BSD-3); pdfplumber MIT (pdfminer.six MIT);
Pillow HPND. PyMuPDF is AGPLv3 and is the reason for the move.

## Adapter contract (what callers get)

* One coordinate system everywhere: PDF points, **top-left origin**, y grows down. This is what fitz exposes
  (`page.rect`, `get_text`, `get_drawings`, `get_image_info`), so existing geometry code keeps its numbers.
* `PDFEngineAdapter.open(path | bytes)` is a context manager; invalid or empty input raises `PDFEngineError`.
* Page-by-page: no per-page caches, every call releases its page.

| Adapter method | Replaces (fitz) |
| --- | --- |
| `open(...)`, `page_count`, `close()` | `fitz.open`, `doc.page_count`, `len(doc)`, `with ... as doc` |
| `page_size(i)`, `page_rect(i)` | `page.rect.width/height` |
| `render(i, dpi, clip=None)`, `render_png(...)` | `page.get_pixmap(dpi=, clip=)` + `pix.tobytes("png")` / `pix.save` |
| `words(i, clip, exclude)`, `lines(i, ...)`, `chars(i, ...)` | `get_text("words")`, `get_text("dict")` lines/spans (chars carry size, font, colour) |
| `text(i, clip, exclude)` | `get_text("text", clip=)`; `exclude` replaces redact-then-read |
| `text_stream(i)` | `get_text("text")` in content-stream order (no layout sort) |
| `find_text(i, needle)` | `page.search_for(needle)` |
| `tables(i)`, `table_rects(i)` | `page.find_tables()` |
| `images(i)` | `get_images()` + `get_image_rects()`, `get_image_info()` |
| `drawings(i)`, `drawing_rects(i)` | `get_drawings()` (rect, colours, stroke/fill; no opacity) |
| `merge_intersecting_rects_union_find(rects, pad)` | hand-rolled merge in `universal_ingestor.py:356` and `extract_engine.py:324` |

## Inventory

Difficulty: **E** = adapter method is a near drop-in; **M** = adapter covers it but semantics or ordering differ and
tests must be re-baselined; **H** = needs a capability outside the adapter (see Gaps).
Line numbers are from branch `feat/w0-pdf-engine-adapter` (base `main` at fc39ae0).

### Application files

| File | fitz capability (lines) | Replacement | Diff. |
| --- | --- | --- | --- |
| `app/ai/essay_exam.py` | open + `needs_pass` + `page_count` (1398-1406) | `open`, `page_count`; password raises `PDFEngineError` (see gap G9) | E |
| | `page.rect` (780, 1451) | `page_size` | E |
| | `get_text("dict")` spans/lines with colour for `is_light_text` (787-800) | `chars`/`lines` (colour and size per char); span grouping is ours | M |
| | `get_text("blocks")` per page (1448) | `lines` then block grouping (gap G5) | M |
| | `get_pixmap(dpi)` + PNG bytes (1454-1458); `remove_light_text` mutates the page, then re-renders | `render`; the mutation is a redaction (gap G3), replace by `exclude`/rendering-time masking | H |
| `app/ai/extract_engine.py` | open as context manager (221, 256, 313) | `open` | E |
| | `get_text("blocks", clip=Rect)` (228) | `lines(clip=)` + block grouping | M |
| | `get_images` + `get_image_rects` (263-264, 319-320) | `images` | E |
| | `get_text("blocks")` for captions (271-278) | `lines` + grouping | M |
| | `Rect` algebra: `&`, padding, `Rect(b[:4])` (278-298, 324-340) | `Rect.intersection`, `expanded`, `union` | E |
| | `get_pixmap(clip=, dpi=300)` + PNG (301-302, 342-343) | `render_png(clip=)` | E |
| `app/ai/figures.py` | `page.rect` (43, 126) | `page_size` | E |
| | `get_drawings()` with `stroke_opacity`/`fill_opacity`, colours (69-80) | `drawings` (colours yes, opacity no: gap G4) | M |
| | `get_image_info()` width/height/bbox (81-86) | `images` (`src_width`, `src_height`, `rect`) | E |
| | `get_text("dict")` spans for light-text and geometry (82, 107) | `chars`/`lines` | M |
| | `add_redact_annot` + `apply_redactions(text=REMOVE, images/graphics=NONE)` (104-117) | gap G3 (object removal) | H |
| | `redact_diagram_interior_text`: blocks inside a rect redacted (153-160) | `text(exclude=)` for reading; object removal only if the page is re-rendered (G3) | H |
| `app/ai/local_text.py` | `open`; `get_text("text")` for each page (315-329) | `text_stream` (closest to fitz order) | M |
| `app/ai/normalize.py` | open, `page_count` limit (76-86) | `open`, `page_count` | E |
| | `get_pixmap(dpi).save(path)`, original and light-text-removed render (90-95) | `render`, `Image.save`; "clean" render depends on G3 | H |
| `app/ai/pipeline.py` | `get_text("text")[:24000]` per page (149-152) | `text_stream` | M |
| `app/application/answer_keys.py` | open from stream; `pdf[i].get_pixmap(dpi=150).tobytes("png")` (50-52) | `render_png(dpi=150)` | E |
| | `fitz.Pixmap(data)` to PNG for JPEG/PNG upload (55-56) | Pillow `Image.open(...).save(..., "PNG")` (not a PDF concern) | E |
| `app/application/exam_pdf.py` | `search_for` of "KỲ THI", "Câu 1", "Câu I", "Chương ", "PRO:", "CHÍNH THỨC" (52-72) | `find_text` (no Unicode normalisation: gap G6) | M |
| | `add_redact_annot(fill=white)` + `apply_redactions(images=REMOVE)` (60-76) | gap G3 | H |
| | `dst = fitz.open()`, `insert_pdf`, `new_page(width, height+70)`, `show_pdf_page` (48-49, 85-95) | gap G1 (assemble/import/place pages) | H |
| | `insert_image(logo, keep_proportion)`, `insert_text(..., "helv")` footer and page numbers (84-101) | gap G2 (stamping) | H |
| | `save` (105) | `pypdfium2`/`pypdf` save (G1) | H |
| `app/application/matrix_import.py` | open from stream; `get_text()` per page; `page_count`; `get_pixmap(dpi=150).tobytes("png")` (119-125) | `open`, `text_stream`, `page_count`, `render_png` | E |
| `app/application/pdf_preview.py` | `insert_pdf(from_page, to_page)` slice (30) | gap G1 | H |
| | `insert_textbox` watermark/footer, alignment constants (39-44, 64-69) | gap G2 | H |
| | `tobytes(garbage=3, deflate=True)` (46, 71) | gap G1 (save options) | M |
| | open from stream + page count (26, 51, 60) | `open`, `page_count` | E |
| `app/cli.py` | open from stream, `get_pixmap(dpi=round(width / (page.rect.width / 72)))` + PNG (172-184) | `page_size`, `render` | E |
| `app/pipeline/harvest/anthologies.py` | `insert_pdf` of many PDFs into one cover document, `page_count` for start pages, `tobytes(garbage=3, deflate=True)` (30-37) | gap G1 | H |
| `app/pipeline/use_cases/library_split.py` | `get_text("text")` per page, `page.rect` (44-46) | `text_stream`, `page_size` | E |
| | `get_pixmap(dpi=120, clip=)` of head/foot bands (34, 47-48) | `render(clip=)` | E |
| | `Pixmap.pdfocr_tobytes(language="vie", tessdata=)` then re-open and `get_text` (35-36) | gap G7 (OCR) | H |
| | `insert_pdf(from_page, to_page)` + `tobytes` (55-57) | gap G1 | H |
| `universal_ingestor.py` (repo root) | `search_for` on first page, many keywords (172-173, 184-195, 858-859) | `find_text` | M |
| | `add_redact_annot` / `apply_redactions` (185-199, 335-337, 478-479) | gap G3 | H |
| | `tobytes`, `save`, `insert_pdf`, `new_page`, `show_pdf_page`, `draw_line`, `Shape.draw_line` (202-274, 483) | gap G1/G2 | H |
| | `insert_text`, `insert_image` branding (214-228, 250-252, 488-498) | gap G2 | H |
| | `get_images` + `get_image_rects` (302-303), own `_merge_overlapping_rects` (356-361) | `images`, `merge_intersecting_rects_union_find` | E |
| | `get_pixmap(clip=, dpi=300).save` (331) | `render(clip=).save` | E |
| | `get_text("blocks", clip=)`, `get_text("text", clip=)`, `get_text()` (341, 501, 852) | `lines`/`text` with `clip` | M |

### Test files (fixture construction is the real work here)

| File | fitz use | Replacement |
| --- | --- | --- |
| `tests/test_ai_import.py` (3, 18-22) | `open()`, new page, `draw_rect` as a "figure" | pypdfium2 raw builder (see `_Builder` in `tests/test_pdf_engine.py`) |
| `tests/test_answer_keys.py` (5, 83) | `Pixmap(csRGB, IRect)` to PNG | Pillow `Image.new` |
| `tests/test_books.py` (5, 16, 106) | build a PDF, re-open stored bytes | builder; `PDFEngineAdapter.open(bytes)` |
| `tests/test_exam_import_keys.py` (185-235) | `insert_htmlbox` with Vietnamese text, `draw_rect` | needs a font-capable writer: Chromium `page.pdf()` (Playwright is already a prod dependency) or reportlab with a TTF (BSD) |
| `tests/test_figure_bands.py` (5, 20-51) | `Page` fixtures, `insert_image(stream=)` | builder with `PdfImage` |
| `tests/test_figures.py` (1, 10-16) | `open()`, `draw_rect` | builder |
| `tests/test_freemium_pro.py` (13, 51, 280, 298) | build a PDF; read result PDFs back | builder; `PDFEngineAdapter.open` + `page_count` |
| `tests/test_library_publish.py` (8, 209) | read a result PDF | `PDFEngineAdapter.open` |
| `tests/test_library_split.py` (6, 63-65, 146-149) | `insert_htmlbox` text pages, scanned (image-only) pages | Chromium PDF for text pages, Pillow `save(..., "PDF")` for scans |
| `tests/test_local_text.py` (1) | fitz mention | check at migration time |

## Gaps (what pypdfium2 + pdfplumber cannot do directly)

| # | Missing capability | Used by | Proposed replacement | Licence |
| --- | --- | --- | --- | --- |
| G1 | **Assembling PDFs**: `insert_pdf` (slice/merge), `new_page`, `show_pdf_page` (place a source page into a taller page), `save`/`tobytes` with compression | `pdf_preview`, `anthologies`, `library_split`, `exam_pdf`, `universal_ingestor` | Slicing and merging: `pypdfium2.PdfDocument.import_pages()` + `save()` (already installed). Placing a page inside a bigger page: `pypdf` `PdfWriter.add_blank_page()` + `PageObject.merge_transformed_page()`, or pdfium form XObjects (`PdfPage.as_xobject`). Compression: `pypdf` `compress_identical_objects()` / `PageObject.compress_content_streams()`. | pypdfium2 Apache-2.0/BSD-3; pypdf BSD-3 |
| G2 | **Writing onto a page**: `insert_text`, `insert_textbox` (alignment), `insert_image`, `draw_line`. Base-14 "helv" cannot draw many Vietnamese letters; the replacement must embed a TTF. | `exam_pdf`, `pdf_preview`, `universal_ingestor` | Draw an overlay with `reportlab` (canvas with a registered TTF, `drawImage` for logos) and merge it with `pypdf` `merge_page`. Alternative without a new dependency: pdfium raw API (`FPDFText_LoadFont` with a TTF, `FPDFPageObj_NewImageObj`, path objects), as the test builder does for standard fonts. | reportlab BSD-3; pypdf BSD-3 |
| G3 | **Redaction**: `add_redact_annot` + `apply_redactions` really remove text (and optionally images) from the content stream. Neither library edits content streams. | `figures`, `essay_exam`, `normalize` (light-text removal), `exam_pdf`, `universal_ingestor` | Split by purpose. (a) Reading text outside a region: `text(exclude=)` / `words(exclude=)` (no PDF edit at all). (b) Rendering with text removed: use the pdfium object API (`page.get_objects()`, `page.remove_obj()` for text/image objects whose bbox intersects the rect, `gen_content()`), on an in-memory copy of the page, then render. (c) Redacting a PDF that is handed to a customer (header strip, "PRO:" band, "CHÍNH THỨC" box): remove the intersecting objects as in (b) and paint a white rectangle; if images partly overlap the rectangle, the whole image object goes (fitz clips pixels). Last resort: rasterise the page with pdfium, paint the rectangle with Pillow, re-wrap with Pillow `save(..., "PDF")`, which loses the text layer. | pypdfium2 Apache-2.0/BSD-3; Pillow HPND |
| G4 | **Drawing semantics**: fitz `get_drawings()` reports `stroke_opacity`/`fill_opacity`, and one dict per path. pdfplumber has no opacity, treats clipping paths differently, and classifies paths as rect/line/curve. `figures.visible_and_dark` uses opacity. | `figures.py` | Use `Drawing.stroke_color/fill_color`; for opacity read the pdfium page-object colour alpha (`FPDFPageObj_GetFillColor`) or drop the opacity test and re-baseline the figure tests. Treat the equivalence as unproven until `test_figures.py` and `test_figure_bands.py` pass on real exam PDFs. | n/a |
| G5 | **`get_text("blocks")` / `get_text("dict")` structure**: fitz returns blocks in content-stream order with block numbers; pdfplumber gives chars/words/lines sorted by position. Multi-column pages come out in a different order. | `essay_exam`, `extract_engine`, `universal_ingestor` | Add `blocks(i)` to the adapter in the migration PR: cluster `lines` by vertical gap and x-overlap (pdfminer layout `LTTextBox` via `pdfplumber` `layout` is a possible base). Keep `text_stream()` for callers that only need plain text in stream order. | MIT |
| G6 | **Text search**: `search_for` normalises and is case-insensitive; `find_text` matches on code points (pdfium). NFC vs NFD Vietnamese and ligatures can differ. | `exam_pdf`, `universal_ingestor` | Normalise needle to NFC and test the keywords ("KỲ THI", "Câu 1", "CHÍNH THỨC") on the golden corpus; fall back to a word-level match over `words()` after `unicodedata.normalize`. | stdlib |
| G7 | **OCR**: `Pixmap.pdfocr_tobytes` runs Tesseract inside MuPDF. | `library_split._ocr` | Render the head/foot band with `render(clip=)` and call `pytesseract.image_to_string(lang="vie")` (needs the `tesseract` binary and `vie.traineddata` already used via `tessdata`). | pytesseract Apache-2.0; Tesseract Apache-2.0 |
| G8 | **Fixture writer with real fonts**: tests use `insert_htmlbox` (HTML with embedded Noto) to get Vietnamese text. | 9 test files | Chromium `page.pdf()` for text/table fixtures (Playwright is a runtime dependency, but tests must `importorskip`/skip when browsers are absent), `reportlab` for deterministic fixtures, Pillow for scanned (image-only) pages. | Apache-2.0 / BSD-3 / HPND |
| G9 | **Password-protected files**: fitz exposes `needs_pass`. The adapter raises `PDFEngineError` when pdfium refuses to open; callers cannot yet tell "password" from "corrupt". | `essay_exam` | Add a `PDFPasswordRequired(PDFEngineError)` subclass (pdfium error code `FPDF_ERR_PASSWORD`) in the migration PR. | n/a |
| G10 | **Rendering fidelity and speed**: pixel output differs from MuPDF (anti-aliasing, hairlines); pdfplumber text/table extraction is slower and uses more memory per page than MuPDF. | all rendering callers; bulk ingestion | Compare renders with a tolerance in golden tests, never byte-equality; keep page-by-page processing (the adapter does); use `text_stream()` (pdfium) instead of pdfplumber when only plain text is needed. | n/a |

## Migration status

### PR A (read-only callers), done

| File | Moved to the adapter | Still on PyMuPDF, and why |
| --- | --- | --- |
| `app/application/answer_keys.py` | PDF page render (`render_png(dpi=150)`); the JPEG/PNG upload is converted with Pillow | nothing (the module no longer imports pymupdf) |
| `app/application/matrix_import.py` | text layer (`text_stream(newline_terminated=True)`), page count, page render | nothing |
| `app/ai/pipeline.py` | per-page text context (`text_stream`) | nothing |
| `app/ai/local_text.py` | plain per-page text in `read_pdf_text` | `read_essay_pdf` (in `essay_exam.py`, PR B) is called first and still uses fitz |
| `app/ai/normalize.py` | open, password detection (`PDFPasswordRequired`, G9), page-count limit, render of the original page PNG | `page_geometry(page)` and `remove_light_text(page)` + the "clean" render (redaction, G3; geometry is PR B) |
| `app/pipeline/use_cases/library_split.py` | page text, page size, rendering of the header/footer bands | OCR (`pdfocr_tobytes`, G7, needs `pytesseract`, not added yet): the pdfium render is wrapped in a `pymupdf.Pixmap` for it; `cut()` (G1, `insert_pdf`) |
| `app/cli.py` | nothing | both call sites open fitz pages only to feed `page_geometry` and `remove_light_text` + clean render; moves with PR B/C |

Adapter additions: `PDFPasswordRequired(PDFEngineError)` (G9) and `text_stream(i, newline_terminated=True)`.

Known differences handled at the call sites: PyMuPDF text ends every line (and page) with a newline, pdfium does
not, hence `newline_terminated`; clip renders can differ by one pixel in size (pdfium rounds, MuPDF rounds up);
invalid PDFs raise `PDFEngineError` instead of `RuntimeError` (`library_split.split_document` catches both).

### Still to do

PR B and PR C as listed in "Proposed migration order". Remaining fitz inventory rows are unchanged except the files above.

## Proposed migration order (3 PRs, lowest risk first)

1. **PR A: read-only callers (all marked E/M, no PDF writing).** `app/application/answer_keys.py`,
   `app/application/matrix_import.py`, `app/cli.py`, `app/ai/normalize.py` (page count + plain render only; the
   light-text "clean" render stays on fitz until PR C), `app/ai/pipeline.py`, `app/ai/local_text.py`,
   `app/pipeline/use_cases/library_split.py` (text, render, page size; OCR via G7). Add `PDFPasswordRequired` (G9).
   Tests: the matching test files switch to the builder (G8). Roll-back is a single revert; fitz stays installed.
2. **PR B: structure and geometry readers.** `app/ai/extract_engine.py`, `app/ai/figures.py` (read side),
   `app/ai/essay_exam.py` (read/render side). Needs `blocks()` (G5) and the opacity decision (G4). Gate on the golden
   exam corpus: block order, figure bboxes and table detection must match within tolerance before merging.
3. **PR C: writers and redaction, then remove the dependency.** `app/application/pdf_preview.py`,
   `app/pipeline/harvest/anthologies.py` (pure G1, do these first inside the PR), then `app/application/exam_pdf.py`,
   `universal_ingestor.py` (G1+G2+G3, highest risk because the output is customer-visible), and the redaction
   remnants in `figures.py`, `essay_exam.py`, `normalize.py`. Finish by deleting the `pymupdf` requirement and
   moving the remaining tests off fitz.

## Dependencies to add later (not added by this task)

* `pypdf` (BSD-3), for G1/G2 in PR C.
* `reportlab` (BSD-3), for G2 overlays and for fixtures (G8); optional if the pdfium raw API is preferred.
* `pytesseract` (Apache-2.0), for G7 in PR A.
