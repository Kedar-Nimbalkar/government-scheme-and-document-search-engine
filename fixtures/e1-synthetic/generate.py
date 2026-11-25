"""Regenerate the synthetic PDFs used by the E1 reference tests."""

from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

ROOT = Path(__file__).resolve().parent
IMAGE = b"q 20 0 0 20 40 40 cm BI /W 1 /H 1 /CS /RGB /BPC 8 ID abc EI Q"


def text_line(words: str) -> bytes:
    return f"BT /F1 12 Tf 40 700 Td ({words}) Tj ET\n".encode("ascii")


def write_pdf(name: str, *contents: bytes) -> None:
    writer = PdfWriter()
    writer.add_metadata({"/Title": "Synthetic test fixture - not an official document"})
    for data in contents:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): DictionaryObject({
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }),
            }),
        })
        if data:
            stream = DecodedStreamObject()
            stream.set_data(data)
            page.replace_contents(stream)
    writer.write(ROOT / name)


if __name__ == "__main__":
    write_pdf(
        "digital-and-mixed.pdf",
        text_line("SYNTHETIC fixture grant grant housing"),
        b"",
        text_line("SYNTHETIC housing grant") + IMAGE,
        IMAGE,
    )
    write_pdf("image-only.pdf", IMAGE)
