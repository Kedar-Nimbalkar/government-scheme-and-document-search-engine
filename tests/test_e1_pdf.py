import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pypdf import PdfWriter

from engines.e1.pdf import extract_pages
from search_shared.index_reader import read_snapshot


class PdfExtractionTests(unittest.TestCase):
    def test_pdf_fixture_matches_indexed_page(self):
        fixture = Path(__file__).resolve().parents[1] / "fixtures" / "v1"
        _, segments = read_snapshot(fixture.parent / "v2" / "index")
        passage = segments[0]["passages"][0]
        self.assertEqual(
            list(extract_pages(fixture / "housing-guidelines.pdf")),
            [(passage["page_no"], passage["text"])],
        )

    def test_pdf_without_extractable_text_is_rejected(self):
        with TemporaryDirectory() as directory:
            blank = Path(directory) / "blank.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            writer.write(blank)
            with self.assertRaisesRegex(ValueError, "no extractable text"):
                list(extract_pages(blank))
