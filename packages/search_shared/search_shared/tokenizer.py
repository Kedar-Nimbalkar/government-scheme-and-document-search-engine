import re
import unicodedata

TOKENIZER_VERSION = "nfkc-casefold-v1"


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _words(normalized: str):
    start = None
    for end, char in enumerate(normalized):
        category = unicodedata.category(char)[0]
        if category in {"L", "N"} or (category == "M" and start is not None):
            if start is None:
                start = end
        elif start is not None:
            yield start, end
            start = None
    if start is not None:
        yield start, len(normalized)


def tokenize(text: str) -> list[str]:
    normalized = _normalize(text)
    return [normalized[start:end] for start, end in _words(normalized)]


def tokenize_with_spans(text: str) -> list[tuple[str, dict[str, int]]]:
    """Return normalized terms and original, half-open UTF-16 spans."""
    normalized = _normalize(text)
    offsets = [0]
    for char in text:
        offsets.append(offsets[-1] + (2 if ord(char) > 0xFFFF else 1))

    # Unicode whitespace is a normalization boundary. Map within each
    # non-whitespace run, keeping composition and expansion together.
    mapped = []
    parts = []
    for run in re.finditer(r"\s|\S+", text):
        value = run.group()
        part = _normalize(value)
        parts.append(part)
        individual = [_normalize(char) for char in value]
        if "".join(individual) == part:
            for i, char_part in enumerate(individual, start=run.start()):
                mapped.extend([(i, i + 1)] * len(char_part))
        else:
            # Map rewritten suffixes to the source ranges that formed them.
            previous = ""
            ranges = []
            for i in range(1, len(value) + 1):
                current = _normalize(value[:i])
                common = 0
                while common < min(len(previous), len(current)) and previous[common] == current[common]:
                    common += 1
                end = run.start() + i
                start = ranges[common][0] if common < len(ranges) else end - 1
                ranges[common:] = [(start, end)] * (len(current) - common)
                previous = current
            mapped.extend(ranges)
    if "".join(parts) != normalized:
        raise ValueError("Unsupported normalization boundary in source text")
    return [
        (normalized[start:end], {"start": offsets[mapped[start][0]], "end": offsets[mapped[end - 1][1]]})
        for start, end in _words(normalized)
    ]
