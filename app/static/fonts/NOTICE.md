# Bundled fonts

| File | Font | Licence | Used by |
| --- | --- | --- | --- |
| `DejaVuSans.ttf` | DejaVu Sans (https://dejavu-fonts.github.io/) | Bitstream Vera Fonts licence plus public-domain DejaVu changes (permissive, redistribution allowed; full text: https://dejavu-fonts.github.io/License.html and in the font's `name` table) | `app/infrastructure/pdf_engine.py` (`DEFAULT_STAMP_FONT`): Vietnamese text stamped on PDFs (footers, banners, watermarks) |

PyMuPDF's base-14 "helv" could not draw most Vietnamese letters; this font covers the whole alphabet.
