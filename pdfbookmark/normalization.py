"""Search-only normalization. Original titles are always retained in models."""

import re
import unicodedata

PREFIX = re.compile(
    r"^\s*(?:(?:第\s*[0-9一二三四五六七八九十百零〇]+\s*[章節項部編])"
    r"|(?:(?:chapter|section|part|appendix)\s+[\divxlcdmA-Z]+)"
    r"|(?:付録\s*[A-Za-z0-9一二三四五六七八九十]*)"
    r"|(?:\d+(?:\s*[.\-]\s*\d+)*(?:\s*[.)、:]|\s+|(?=[^\d\s.\-]))))\s*",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(c for c in text if unicodedata.category(c)[0] in "LN")


def split_heading(text: str) -> tuple[str, str]:
    text = unicodedata.normalize("NFKC", text).strip()
    match = PREFIX.match(text)
    if match:
        return normalize(match[0]), normalize(text[match.end() :])
    return "", normalize(text)


def printed_number(value: str | None) -> tuple[str, int] | None:
    """Keep numbering domains separate (arabic, roman, appendix:A, ...)."""
    if not value:
        return None
    value = unicodedata.normalize("NFKC", value).strip()
    if re.fullmatch(r"\d+", value):
        return "arabic", int(value)
    if re.fullmatch(r"(?i)M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})", value):
        numbers = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
        result = last = 0
        for char in reversed(value.upper()):
            n = numbers[char]
            result += -n if n < last else n
            last = max(last, n)
        return "roman", result
    appendix = re.fullmatch(r"([A-Za-z]+)\s*[-–−.]\s*(\d+)", value)
    if appendix:
        return f"appendix:{appendix[1].upper()}", int(appendix[2])
    return None
