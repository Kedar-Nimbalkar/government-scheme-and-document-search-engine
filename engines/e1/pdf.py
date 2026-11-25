"""Page-aware extraction for digital PDFs."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError


class PdfExtractionError(ValueError):
    def __init__(self, message: str, *, omitted_pages: tuple[int, ...] = ()):
        super().__init__(message)
        self.omitted_pages = omitted_pages


class UnreadablePdfError(PdfExtractionError):
    code = "PDF_UNREADABLE"


class CorruptPdfError(PdfExtractionError):
    code = "PDF_CORRUPT"


class EncryptedPdfError(PdfExtractionError):
    code = "PDF_ENCRYPTED"


class NoTextPdfError(PdfExtractionError):
    code = "PDF_NO_TEXT"


@dataclass(frozen=True)
class ExtractedPdf:
    pages: tuple[tuple[int, str], ...]
    omitted_pages: tuple[int, ...]


def extract_pdf(path: str | Path) -> ExtractedPdf:
    """Fully read a digital PDF and retain its exact extracted text and page numbers."""
    pages = []
    omitted = []
    try:
        with Path(path).open("rb") as source:
            reader = PdfReader(source, strict=True)
            if reader.is_encrypted:
                raise EncryptedPdfError("Encrypted PDFs are not supported")
            for page_no, page in enumerate(reader.pages, start=1):
                text = page.extract_text()
                if text.strip():
                    pages.append((page_no, text))
                else:
                    omitted.append(page_no)
    except PdfExtractionError:
        raise
    except OSError as error:
        raise UnreadablePdfError("PDF could not be read") from error
    except (PyPdfError, ValueError, TypeError, IndexError, KeyError, RecursionError) as error:
        raise CorruptPdfError("PDF could not be parsed") from error

    if not pages:
        raise NoTextPdfError("PDF contains no extractable text", omitted_pages=tuple(omitted))
    return ExtractedPdf(tuple(pages), tuple(omitted))


def extract_pages(path: str | Path) -> Iterator[tuple[int, str]]:
    """Yield digital pages only, after the entire PDF has been read successfully."""
    yield from extract_pdf(path).pages
