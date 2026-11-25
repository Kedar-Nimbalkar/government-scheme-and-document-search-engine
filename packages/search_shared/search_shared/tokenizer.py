import unicodedata


def tokenize(text: str) -> list[str]:
    """Apply NFKC and casefold, preserving combining marks within words."""
    words = []
    current = []
    normalized = unicodedata.normalize("NFKC", text).casefold()
    for char in normalized:
        category = unicodedata.category(char)[0]
        if category in {"L", "N"} or (category == "M" and current):
            current.append(char)
        elif current:
            words.append("".join(current))
            current.clear()
    if current:
        words.append("".join(current))
    return words
