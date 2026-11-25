"""Page-aware extraction for digital PDFs."""

from collections.abc import Iterator
from pathlib import Path

from pypdf import PdfReader


def extract_pages(path: str | Path) -> Iterator[tuple[int, str]]:
    """Yield nonblank text with its original one-based PDF page number."""
    with Path(path).open("rb") as source:
        reader = PdfReader(source, strict=True)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported")
        has_text = False
        for page_no, page in enumerate(reader.pages, start=1):
            text = page.extract_text().strip()
            if text:
                has_text = True
                yield page_no, text
        if not has_text:
            raise ValueError("PDF contains no extractable text")
