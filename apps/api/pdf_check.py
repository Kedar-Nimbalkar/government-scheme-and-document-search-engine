"""Validate staged uploads using E1's digital PDF parser."""

import json
import sys

from engines.e1.pdf import PdfExtractionError, extract_pdf
from search_shared.tokenizer import tokenize


def main():
    try:
        pdf = extract_pdf(sys.argv[1], max_text_bytes=8 * 1024 * 1024)
        result = {"valid": True} if any(tokenize(text) for _, text in pdf.pages) else {"reason": "PDF_NO_TEXT"}
    except PdfExtractionError as error:
        result = {"reason": error.code}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
