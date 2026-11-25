"""Conservative cleanup shared by E3 cataloguing and E1 snapshot parsing."""

import re

MOJIBAKE = re.compile(r"(?:[\u00e2\u00e0\u00ef][\u0080-\u00bf\u0152-\u017e\u2000-\u2122]{2})+")
SECTIONS = (
    "Details", "Benefits", "Eligibility", "Exclusions", "Application Process",
    "Documents Required", "Frequently Asked Questions", "Sources And References",
)
PDF_HEADER = re.compile(
    r"Are\s+you\s+sure\s+you\s+want\s+to\s+sign\s+out\?.{0,1300}?"
    r"Cancel\s*Apply\s+Now\s*Check\s+Eligibility", re.DOTALL,
)
PDF_MESSAGES = re.compile(
    r"Something\s+went\s+wrong\.\s*Please\s+try\s+again\s+later\.\s*(?:Ok)?"
    r"|Are\s+you\s+sure\s+you\s+want\s+to\s+sign\s+out\?\s*Cancel\s*Sign\s+Out"
    r"|You\s+need\s+to\s+sign\s+in\s+before\s+applying\s+for\s+schemes\s*Cancel\s*Sign\s+In"
    r"|It\s+seems\s+you\s+have\s+already\s+initiated\s+your\s+application\s+earlier\."
    r"\s*To\s+know\s+more\s+please\s+visit\s*Cancel\s*Apply\s+Now"
)


def repair(text):
    def decode(match):
        try:
            return match[0].encode("cp1252").decode("utf-8")
        except UnicodeError:
            return match[0]

    return MOJIBAKE.sub(decode, text).replace("\ufeff", "").replace("\u00e2\u20ac", "\u201d")


def normalize_space(text):
    return re.sub(r"\n{3,}", "\n\n", "\n".join(
        re.sub(r"[^\S\n]+", " ", line).strip() for line in text.splitlines()
    )).strip()


def clean_article(text):
    text = repair(text)
    text = re.sub(r"\(?adsbygoogle\s*=\s*window\.adsbygoogle\s*\|\|\s*\[\]\)?\s*\.push\(\{\}\);?", "", text)
    text = re.sub(r"(?im)^[ \t]*(?:SAVE AS PDF|Home|Menu|Skip to content|Advertisement)[ \t]*$", "", text)
    text = re.sub(r"(?im)^Table of Contents[ \t]*\n[ \t]*\n[^\n]*\n", "", text, count=1)
    return normalize_space(text)


def snapshot_header(text):
    text = repair(text)
    match = PDF_HEADER.search(text)
    if not match:
        return None, ""
    title = " ".join(text[:match.start()].split())
    return title, text[match.end():]


def clean_snapshot_page(text):
    text = repair(text)
    match = PDF_HEADER.search(text)
    if match:
        text = text[:match.start()] + "\n\n" + text[match.end():]
    text = PDF_MESSAGES.sub("\n", text)
    text = re.sub(r"(?m)^[ \t]*(?:Sign\s+In\s*Back|Cancel\s*Sign\s+Out)[ \t]*$", "", text)
    for heading in SECTIONS:
        pattern = r"\s+".join(heading.split())
        text = re.sub(r"(?<![a-z ])" + pattern + r"(?=\s|[A-Z])", "\n\n" + heading + "\n", text)
    return normalize_space(text)
