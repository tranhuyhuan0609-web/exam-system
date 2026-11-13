# PDF engine migration, PR B: golden corpus report (ADR-010, task T0.8b)

Numbers only. Source: `tests/golden/snapshot.py` (one JSON per file under the git-ignored golden folder) compared by
`tests/golden/test_pdf_characterization.py`. "Before" is the PyMuPDF snapshot taken at the branch base, "after" the migrated
readers (`PDFEngineAdapter`: pypdfium2 + pdfplumber). Question counts are those of the non-AI import path (`read_locally`).

## 1. Questions found by the non-AI path, versus expected

Expected counts are the tech lead's. Precision = found labels inside 1..expected / found; recall = those / expected (labels are the
question numbers the reader assigned; it cannot be checked beyond the numbers). g12 (Word file, 2 pages after LibreOffice) has no expected count.

| File | Expected | Found before | Found after | Precision before/after | Recall before/after | Labels equal | Pages equal |
| --- | --- | --- | --- | --- | --- | --- | --- |
| g01 | 5 | 5 | 5 | 1.00 / 1.00 | 1.00 / 1.00 | yes | yes |
| g02 | 5 | 5 | 5 | 1.00 / 1.00 | 1.00 / 1.00 | yes | yes |
| g03 | 8 | 8 | 8 | 1.00 / 1.00 | 1.00 / 1.00 | yes | yes |
| g04 | 21 | 15 | 15 | 1.00 / 1.00 | 0.71 / 0.71 | yes | yes |
| g05 | 8 | 8 | 8 | 1.00 / 1.00 | 1.00 / 1.00 | yes | yes |
| g06 | 8 | 7 | 7 | 1.00 / 1.00 | 0.88 / 0.88 | yes | yes |
| g07 | 5 | 5 | 5 | 1.00 / 1.00 | 1.00 / 1.00 | yes | yes |
| g08 | 10 | 10 | 10 | 1.00 / 1.00 | 1.00 / 1.00 | yes | yes |
| g09 | 5 | 5 | 5 | 1.00 / 1.00 | 1.00 / 1.00 | yes | yes |
| g10 | 220 | 12 | 12 | 1.00 / 1.00 | 0.05 / 0.05 | yes | yes |
| g11 | 67 | 24 | 24 | 1.00 / 1.00 | 0.36 / 0.36 | yes | yes |
| g12 | unknown | 5 | 5 | n/a / n/a | n/a / n/a | yes | yes |

## 2. Every check, per file (after versus before; 1.0 = identical)

Thresholds: page_count 1.0, page_size 1.0, words_jaccard 0.99, text_similarity 0.97, question_count 1.0, question_labels 1.0, question_pages 1.0, question_text 0.98, question_figures 1.0, graphics_boxes 1.0, image_boxes 1.0, figure_bands 1.0, unlabeled_diagrams 1.0, clusters_3 1.0, clusters_25 1.0, essay_line_count 0.98, essay_line_text 0.97.
Box checks give the share of boxes that have a partner with IoU 0.90 or more (one-to-one) over the larger count; page-level values are the
minimum over the pages of the file. `*` marks a value under its threshold, covered by a tolerance (section 4).

| File | page_count | page_size | words_jaccard | text_similarity | question_count | question_labels | question_pages | question_text | question_figures | graphics_boxes | image_boxes | figure_bands | unlabeled_diagrams | clusters_3 | clusters_25 | essay_line_count | essay_line_text |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| g01 | 1.0000 | 1.0000 | 0.9741* | 0.9889 | 1.0000 | 1.0000 | 1.0000 | 0.8847* | 1.0000 | 1.0000 | 1.0000 | 0.6667* | 1.0000 | 1.0000 | 1.0000 | 0.8621* | 0.9275* |
| g02 | 1.0000 | 1.0000 | 0.9799* | 0.9940 | 1.0000 | 1.0000 | 1.0000 | 0.9637* | 1.0000 | 0.9825* | 0.9583* | 0.6667* | 1.0000 | 1.0000 | 1.0000 | 0.9107* | 0.9686* |
| g03 | 1.0000 | 1.0000 | 0.9880* | 0.9903 | 1.0000 | 1.0000 | 1.0000 | 0.9516* | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9091* | 1.0000 | 0.9403* | 0.9722 |
| g04 | 1.0000 | 1.0000 | 1.0000 | 0.9954 | 1.0000 | 1.0000 | 1.0000 | 0.8098* | 1.0000 | 1.0000 | 1.0000 | 0.5000* | 1.0000 | 1.0000 | 1.0000 | 0.9362* | 0.9908 |
| g05 | 1.0000 | 1.0000 | 0.9762* | 0.9951 | 1.0000 | 1.0000 | 1.0000 | 0.9761* | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9778* | 0.9919 |
| g06 | 1.0000 | 1.0000 | 0.9892* | 0.9937 | 1.0000 | 1.0000 | 1.0000 | 0.9729* | 1.0000 | 0.9394* | 0.8750* | 1.0000 | 0.5000* | 0.6667* | 1.0000 | 0.8919* | 0.9924 |
| g07 | 1.0000 | 1.0000 | 0.9831* | 0.9983 | 1.0000 | 1.0000 | 1.0000 | 0.9872 | 1.0000 | 0.8974* | 0.8571* | 0.0000* | 0.3333* | 0.2000* | 1.0000 | 0.9259* | 0.9921 |
| g08 | 1.0000 | 1.0000 | 1.0000 | 0.9872 | 1.0000 | 1.0000 | 1.0000 | 0.9751* | 1.0000 | 0.9000* | 0.8333* | 0.7500* | 0.6667* | 0.6000* | 0.7500* | 0.8837* | 0.9847 |
| g09 | 1.0000 | 1.0000 | 0.9600* | 0.9687* | 1.0000 | 1.0000 | 1.0000 | 0.9910 | 1.0000 | 0.9111* | 0.8571* | 0.6667* | 1.0000 | 1.0000 | 1.0000 | 0.8684* | 0.9168* |
| g10 | 1.0000 | 1.0000 | 0.9524* | 0.8903* | 1.0000 | 1.0000 | 1.0000 | 0.2121* | 1.0000 | 0.9023* | 0.2273* | 0.3333* | 1.0000 | 1.0000 | 1.0000 | 0.6078* | 0.7637* |
| g11 | 1.0000 | 1.0000 | 0.9825* | 0.9116* | 1.0000 | 1.0000 | 1.0000 | 0.3441* | 1.0000 | 1.0000 | 1.0000 | 0.5000* | 1.0000 | 1.0000 | 1.0000 | 0.8478* | 0.7613* |
| g12 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9900 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.9957 |

## 3. Raw counts, before / after

| File | Pages | Essay lines | Graphics boxes | Image boxes | Figure bands | Questions with a figure |
| --- | --- | --- | --- | --- | --- | --- |
| g01 | 3 / 3 | 158 / 179 | 76 / 76 | 25 / 25 | 5 / 5 | 3 / 3 |
| g02 | 3 / 3 | 127 / 120 | 126 / 125 | 54 / 53 | 7 / 7 | 5 / 5 |
| g03 | 5 / 5 | 197 / 201 | 699 / 685 | 242 / 242 | 8 / 8 | 5 / 5 |
| g04 | 3 / 3 | 142 / 142 | 103 / 100 | 0 / 0 | 4 / 4 | 4 / 4 |
| g05 | 1 / 1 | 45 / 44 | 105 / 103 | 1 / 1 | 3 / 3 | 2 / 2 |
| g06 | 3 / 3 | 70 / 76 | 58 / 58 | 16 / 14 | 5 / 5 | 6 / 6 |
| g07 | 2 / 2 | 82 / 77 | 208 / 208 | 41 / 37 | 5 / 6 | 5 / 5 |
| g08 | 3 / 3 | 110 / 108 | 107 / 105 | 40 / 32 | 8 / 8 | 10 / 10 |
| g09 | 5 / 5 | 249 / 267 | 184 / 184 | 30 / 29 | 12 / 12 | 4 / 4 |
| g10 | 56 / 56 | 2576 / 2692 | 7628 / 7539 | 247 / 213 | 216 / 217 | 6 / 6 |
| g11 | 11 / 11 | 576 / 570 | 355 / 355 | 22 / 22 | 22 / 22 | 0 / 0 |
| g12 | 2 / 2 | 35 / 35 | 54 / 54 | 52 / 52 | 2 / 2 | 1 / 1 |

## 4. Tolerances (`tests/golden/tolerances.json`)

65 entries. Each relaxes ONE check of ONE file down to the recorded value (the test fails if it falls more than 0.005 below it).
The list is long because the two engines segment text lines of formulas differently; see section 6. Nothing was relaxed for the
page count, page size, question count, question labels, question pages or the figure attached to a question: those are identical on all 12 files.

| File | Check | Measured | Reason |
| --- | --- | --- | --- |
| g01 | words_jaccard | 0.974 | words split at font and sub/superscript changes differ between the engines; page text is the same |
| g01 | question_text | 0.8847 | question text is assembled from line fragments that the engines split and order differently (math); no question is gained or lost |
| g01 | figure_bands | 0.6666 | band edges follow text-line fragments and vector paths that the engines report slightly differently; band count is equal except where noted in graphics |
| g01 | essay_line_count | 0.862 | sub/superscript and formula fragments are split into lines differently between the engines |
| g01 | essay_line_text | 0.9274 | sub/superscript and formula fragments are ordered differently within merged lines |
| g02 | words_jaccard | 0.9798 | words split at font and sub/superscript changes differ between the engines; page text is the same |
| g02 | question_text | 0.9637 | question text is assembled from line fragments that the engines split and order differently (math); no question is gained or lost |
| g02 | graphics_boxes | 0.9824 | one drawing in PyMuPDF is two paths (fill, then outline drawn separately) in PDFium |
| g02 | image_boxes | 0.9583 | PyMuPDF lists extra images (zero-area boxes at the page origin, or ones without an xref) that neither PDFium nor pdfplumber report |
| g02 | figure_bands | 0.6666 | band edges follow text-line fragments and vector paths that the engines report slightly differently; band count is equal except where noted in graphics |
| g02 | essay_line_count | 0.9107 | sub/superscript and formula fragments are split into lines differently between the engines |
| g02 | essay_line_text | 0.9685 | sub/superscript and formula fragments are ordered differently within merged lines |
| g03 | words_jaccard | 0.9879 | words split at font and sub/superscript changes differ between the engines; page text is the same |
| g03 | question_text | 0.9515 | question text is assembled from line fragments that the engines split and order differently (math); no question is gained or lost |
| g03 | clusters_3 | 0.909 | clusters follow the vector paths that differ between the engines (see graphics_boxes) |
| g03 | essay_line_count | 0.9402 | sub/superscript and formula fragments are split into lines differently between the engines |
| g04 | question_text | 0.8097 | question text is assembled from line fragments that the engines split and order differently (math); no question is gained or lost |
| g04 | figure_bands | 0.5 | band edges follow text-line fragments and vector paths that the engines report slightly differently; band count is equal except where noted in graphics |
| g04 | essay_line_count | 0.9361 | sub/superscript and formula fragments are split into lines differently between the engines |
| g05 | words_jaccard | 0.9761 | words split at font and sub/superscript changes differ between the engines; page text is the same |
| g05 | question_text | 0.976 | question text is assembled from line fragments that the engines split and order differently (math); no question is gained or lost |
| g05 | essay_line_count | 0.9777 | sub/superscript and formula fragments are split into lines differently between the engines |
| g06 | words_jaccard | 0.9892 | words split at font and sub/superscript changes differ between the engines; page text is the same |
| g06 | question_text | 0.9729 | question text is assembled from line fragments that the engines split and order differently (math); no question is gained or lost |
| g06 | graphics_boxes | 0.9393 | PyMuPDF omits some vector paths that PDFium and pdfplumber both report (and lists zero-area boxes at the page origin) |
| g06 | image_boxes | 0.875 | PyMuPDF lists extra images (zero-area boxes at the page origin, or ones without an xref) that neither PDFium nor pdfplumber report |
| g06 | unlabeled_diagrams | 0.5 | clusters follow the vector paths that differ between the engines (see graphics_boxes) |
| g06 | clusters_3 | 0.6666 | clusters follow the vector paths that differ between the engines (see graphics_boxes) |
| g06 | essay_line_count | 0.8918 | sub/superscript and formula fragments are split into lines differently between the engines |
| g07 | words_jaccard | 0.983 | words split at font and sub/superscript changes differ between the engines; page text is the same |
| g07 | graphics_boxes | 0.8974 | PyMuPDF omits some vector paths that PDFium and pdfplumber both report (and lists zero-area boxes at the page origin) |
| g07 | image_boxes | 0.8571 | PyMuPDF lists extra images (zero-area boxes at the page origin, or ones without an xref) that neither PDFium nor pdfplumber report |
| g07 | figure_bands | -0.0001 | band edges follow text-line fragments and vector paths that the engines report slightly differently; band count is equal except where noted in graphics |
| g07 | unlabeled_diagrams | 0.3333 | clusters follow the vector paths that differ between the engines (see graphics_boxes) |
| g07 | clusters_3 | 0.2 | clusters follow the vector paths that differ between the engines (see graphics_boxes) |
| g07 | essay_line_count | 0.9259 | sub/superscript and formula fragments are split into lines differently between the engines |
| g08 | question_text | 0.975 | question text is assembled from line fragments that the engines split and order differently (math); no question is gained or lost |
| g08 | graphics_boxes | 0.9 | PyMuPDF omits some vector paths that PDFium and pdfplumber both report (and lists zero-area boxes at the page origin) |
| g08 | image_boxes | 0.8333 | PyMuPDF lists extra images (zero-area boxes at the page origin, or ones without an xref) that neither PDFium nor pdfplumber report |
| g08 | figure_bands | 0.75 | band edges follow text-line fragments and vector paths that the engines report slightly differently; band count is equal except where noted in graphics |
| g08 | unlabeled_diagrams | 0.6666 | clusters follow the vector paths that differ between the engines (see graphics_boxes) |
| g08 | clusters_3 | 0.5999 | clusters follow the vector paths that differ between the engines (see graphics_boxes) |
| g08 | clusters_25 | 0.75 | clusters follow the vector paths that differ between the engines (see graphics_boxes) |
| g08 | essay_line_count | 0.8837 | sub/superscript and formula fragments are split into lines differently between the engines |
| g09 | words_jaccard | 0.9599 | words split at font and sub/superscript changes differ between the engines; page text is the same |
| g09 | text_similarity | 0.9686 | math-heavy page: sub/superscript and fraction fragments are ordered differently; the adapter also keeps some text the PyMuPDF baseline dropped |
| g09 | graphics_boxes | 0.9111 | PyMuPDF omits some vector paths that PDFium and pdfplumber both report (and lists zero-area boxes at the page origin) |
| g09 | image_boxes | 0.8571 | PyMuPDF lists extra images (zero-area boxes at the page origin, or ones without an xref) that neither PDFium nor pdfplumber report |
| g09 | figure_bands | 0.6666 | band edges follow text-line fragments and vector paths that the engines report slightly differently; band count is equal except where noted in graphics |
| g09 | essay_line_count | 0.8684 | sub/superscript and formula fragments are split into lines differently between the engines |
| g09 | essay_line_text | 0.9167 | sub/superscript and formula fragments are ordered differently within merged lines |
| g10 | words_jaccard | 0.9523 | words split at font and sub/superscript changes differ between the engines; page text is the same |
| g10 | text_similarity | 0.8902 | math-heavy page: sub/superscript and fraction fragments are ordered differently; the adapter also keeps some text the PyMuPDF baseline dropped |
| g10 | question_text | 0.2121 | question text is assembled from line fragments that the engines split and order differently (math); no question is gained or lost |
| g10 | graphics_boxes | 0.9022 | PyMuPDF omits some vector paths that PDFium and pdfplumber both report (and lists zero-area boxes at the page origin) |
| g10 | image_boxes | 0.2272 | PyMuPDF lists extra images (zero-area boxes at the page origin, or ones without an xref) that neither PDFium nor pdfplumber report |
| g10 | figure_bands | 0.3333 | band edges follow text-line fragments and vector paths that the engines report slightly differently; band count is equal except where noted in graphics |
| g10 | essay_line_count | 0.6078 | sub/superscript and formula fragments are split into lines differently between the engines |
| g10 | essay_line_text | 0.7637 | sub/superscript and formula fragments are ordered differently within merged lines |
| g11 | words_jaccard | 0.9824 | words split at font and sub/superscript changes differ between the engines; page text is the same |
| g11 | text_similarity | 0.9116 | math-heavy page: sub/superscript and fraction fragments are ordered differently; the adapter also keeps some text the PyMuPDF baseline dropped |
| g11 | question_text | 0.344 | the adapter keeps question stems that the PyMuPDF baseline lost; the rest is fragment order in math |
| g11 | figure_bands | 0.5 | band edges follow text-line fragments and vector paths that the engines report slightly differently; band count is equal except where noted in graphics |
| g11 | essay_line_count | 0.8478 | sub/superscript and formula fragments are split into lines differently between the engines |
| g11 | essay_line_text | 0.7612 | sub/superscript and formula fragments are ordered differently within merged lines |

## 5. Timing (seconds, whole snapshot of a file: page readings + document readers)

Measured on the same machine, one file at a time, foreground. The migrated run also includes `PDFEngineAdapter` table detection (pdfplumber)
per page, which the PyMuPDF run replaced by `page.find_tables()` (skipped for g10 there).

| File | Pages | Before total | After total | Before read_locally | After read_locally | Before pages | After pages |
| --- | --- | --- | --- | --- | --- | --- | --- |
| g01 | 3 | 10.137 | 8.237 | 5.267 | 4.171 | 4.006 | 2.476 |
| g02 | 3 | 5.945 | 7.591 | 3.621 | 4.678 | 1.212 | 1.664 |
| g03 | 5 | 16.215 | 10.544 | 11.819 | 6.784 | 3.584 | 2.755 |
| g04 | 3 | 14.093 | 10.996 | 9.739 | 6.856 | 2.863 | 2.65 |
| g05 | 1 | 4.585 | 3.707 | 3.294 | 2.094 | 0.757 | 0.968 |
| g06 | 3 | 5.869 | 5.157 | 4.284 | 3.989 | 1.277 | 0.776 |
| g07 | 2 | 7.117 | 4.21 | 4.805 | 2.605 | 1.181 | 0.898 |
| g08 | 3 | 12.43 | 7.71 | 9.689 | 5.23 | 1.011 | 1.122 |
| g09 | 5 | 10.226 | 4.513 | 6.582 | 1.976 | 2.775 | 1.749 |
| g10 | 56 | 131.839 | 98.164 | 87.589 | 45.77 | 15.318 | 28.627 |
| g11 | 11 | 58.997 | 24.422 | 38.916 | 15.281 | 16.985 | 7.019 |
| g12 | 2 | 21.726 | 2.647 | 1.892 | 1.056 | 2.451 | 0.33 |
| all | 97 | 299.2 | 187.9 | | | | |

Notes on the timing table: the g12 "before" total includes the one-off LibreOffice conversion of the Word file (about 20 s); it is not
comparable. Without g12 the totals are 277.5 s before and 185.3 s after. The "pages" column of g10 got slower (15.3 s to 28.6 s) because the
migrated snapshot runs the pdfplumber table detector on all 56 pages (the PyMuPDF run skipped that probe for g10, where it crashes the
process); the application itself does not call `tables()` in these readers. `read_locally` is the figure that matters for imports: faster on
every file except g02 (3.6 s to 4.7 s, run-to-run noise on a 3-page file is about 1 s).

## 6. What differs, and what was verified

Identical on all 12 files: page count, page size, number of questions found, their labels, their pages and which of them carry a figure.
The snapshots could not be compared on the AI path (no network or model in this task); only the non-AI path was run.

Verified causes of the remaining differences (looked at on the files, not guessed):

* **Formula line fragments.** PyMuPDF and the adapter split and order sub/superscript and fraction fragments differently, so math-heavy
  question texts differ in character order (question_text, essay_line_*, words_jaccard). The adapter groups characters of one row into a
  line, keeps sub/superscripts inside it and inserts a space where a gap of 0.2 em separates two characters; PyMuPDF breaks lines at some
  font changes and not at others (rule not reproduced: tried baseline shift, span-per-line and gap sweeps; all were worse or equal).
* **g11 and g10 short questions.** In g11 questions 3 and 5 the adapter's text contains the question stem which the PyMuPDF baseline
  lost, so the similarity (0.34) understates quality there. Not checked question by question for g10.
* **Vector paths.** On g07 page 2 pdfium and pdfplumber both report three thin rectangles that PyMuPDF omits; PyMuPDF instead lists
  zero-area boxes at the page origin (g06 to g09). Figure bands and clusters on those pages follow.
* **Images.** On g10 page 26 PyMuPDF lists 26 images, 17 of them without an xref; pdfium and pdfplumber both see the 9 real ones.
* **Fill plus outline.** A path drawn by a fill operator and then an outline operator is one drawing in PyMuPDF only sometimes. The adapter
  merges them when the point lists are identical; the comparison counts identical boxes once. g02 keeps a residual of 0.98.
* **Watermark detection.** The adapter's `has_light_text` agrees with PyMuPDF's `remove_light_text(page) > 0` on all 97 pages of the corpus
  (68 pages have watermark text), so the lazily opened PyMuPDF redaction runs for exactly the pages it did before.

## 7. What still uses PyMuPDF and why

| Where | What | Why |
| --- | --- | --- |
| `app/ai/figures.py` | `remove_light_text`, `redact_diagram_interior_text` | redaction removes text from the page (G3), PR C |
| `app/ai/essay_exam.py` `_WatermarkRedactor` | clean render of pages with watermark text | needs the redaction above; opened lazily, only for flagged pages |
| `app/ai/normalize.py` `_open_pymupdf` | same, for the clean figure image | same |
| `app/cli.py` `clean_render` | redaction plus render at the stored resolution | same |
| not in scope (unchanged) | `exam_pdf.py`, `pdf_preview.py`, `anthologies.py`, `universal_ingestor.py`, `library_split.cut` and OCR | PDF writing, assembling, OCR (PR C) |

`extract_engine.py` no longer imports PyMuPDF.

## 8. Open risks

* The line and block grouping is calibrated on 12 exams (parameters in `app/infrastructure/pdf_engine.py`: `LINE_SHIFT_EM`,
  `LINE_BREAK_GAP_EM`, `SPACE_GAP_EM`, `SPAN_SIZE_RATIO`, `BLOCK_GAP_EM`). Other layouts can behave differently; a sweep of the
  parameters around the chosen values changed the essay-line similarity by less than 0.01.
* Text and path coordinates from pdfium assume a page whose box starts at the origin and is not rotated; none of the 97 corpus pages is rotated or
  has a box origin away from (0, 0) (checked), and there is no test for it. A rotated scan with a text layer would be read in unrotated coordinates.
* Form XObject transforms were validated against PyMuPDF on g02 (28 images) and by the whole corpus; deeper nesting is composed by the same
  loop but has no dedicated test.
* The block heuristic (gap below 0.35 line heights, stream order) differs from PyMuPDF paragraph blocks; only captions and the continuous
  text reader depend on it, both unchanged in outcome on the corpus (labels, counts and pages equal).
* 65 tolerances is a long list. They record that two layout engines disagree on formula fragments; they do not show a regression on the
  structure the importer needs (question boundaries, pages, figures). A reviewer who wants fewer entries has to accept either the PyMuPDF
  fragment order (not reproducible) or looser thresholds.
* Tesseract is not installed on this machine; OCR (library split) was not exercised.
* Mixed engines for the clean render: for pages with watermark text, the raw image comes from pdfium and the clean image from PyMuPDF;
  they differ by anti-aliasing (and possibly one pixel in size). Crops use relative 0-1000 boxes, so nothing depends on equal sizes, but a
  figure cut from the clean image is rendered by a different engine than the page image shown next to it.

## 9. Follow-up: reading order of fragments on one visual row

The readers sorted positioned line fragments by `(ymin // 6, xmin)`. Two fragments of the same visual row whose tops
fall on either side of a multiple of 6 were then emitted right-to-left (answer D before answer C). The PyMuPDF baseline
has the same defect on other questions, because it comes from the sort and not from the engine.
`_in_reading_order` in `app/ai/essay_exam.py` now groups fragments into rows anchored on the topmost fragment
(tolerance 5 thousandths of the page height) and orders each row left to right.

Questions whose answer markers A, B, C, D appear out of order in the extracted text (multiple-choice files only; markers
counted by hand-checked regex, with the `°C.` false positives removed):

| File | PyMuPDF baseline | Adapter before this fix | Adapter after this fix |
| --- | --- | --- | --- |
| g04 | 0 | 2 | 1 |
| g10 | 2 | 1 | 0 |
| g11 | 2 | 1 | 0 |
| all | 4 | 4 | 1 |

The remaining one (g04) has four fraction formulas as answers; their numerators, bars and denominators are separate
fragments and are not reassembled. Question counts, labels, pages and figures are unchanged on all 12 files. Six
tolerances moved slightly because the new order differs from the baseline's bucket order (`essay_line_count` on g02,
g04, g05, g07; `question_text` on g10; `essay_line_text` on g11).
