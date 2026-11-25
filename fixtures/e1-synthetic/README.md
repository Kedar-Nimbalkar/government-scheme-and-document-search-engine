# Synthetic E1 fixtures

These generated PDFs and scheme records are test data. They carry no official
government information. IDs are fixed solely to make fixture runs repeatable.

- `digital-and-mixed.pdf` has digital text on page 1, a blank page 2,
  digital text plus an inline image on page 3, and an image-only page 4.
- `image-only.pdf` tests rejection when all pages lack digital text.
- `schemes.csv` supplies a record linked to a fixture scheme UUID and includes Kannada, Hindi, and English text.
- `index/` is the v2 snapshot produced by the [reference CLI](../../engines/e1/README.md) with the defaults and a fresh index root.

Regenerate the PDFs with `python fixtures/e1-synthetic/generate.py`.
