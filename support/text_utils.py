from __future__ import annotations

import re

# Emoji/pictograph/symbol ranges. Built from chr() rather than literal
# escapes so this module's own pattern definition doesn't trip
# test_logs_are_ascii_only.
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2B00, 0x2BFF),
    (0x1F1E6, 0x1F1FF),
    (0xFE00, 0xFE0F),
    (0x200D, 0x200D),
    (0x2190, 0x21FF),
)
_EMOJI_RE = re.compile(
    "[" + "".join(chr(a) + "-" + chr(b) if a != b else chr(a) for a, b in _EMOJI_RANGES) + "]+"
)


def _mojibake_score(s: str) -> int:
    try:
        t = str(s or "")
    except Exception:
        return 0
    markers = ("Ã", "Â", "â", "ð", "Ð", "Ñ", "�")
    return sum(t.count(m) for m in markers)


def _normalize_text(msg) -> str:
    """
    Repair common UTF-8/CP1252 mojibake and normalize risky UI glyphs.
    """
    try:
        text = str(msg)
    except Exception:
        return str(msg)

    # Fast path fixes for already-corrupted sequences seen in UI/logs.
    direct_map = {
        "…": "...",
        "–": "-",
        "—": "-",
        "•": "-",
        "→": "->",
        "←": "<-",
        "↑": "Up",
        "↓": "Down",
        "×": "x",
        "≥": ">=",
        "≤": "<=",
        "±": "+/-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    }
    for bad, good in direct_map.items():
        text = text.replace(bad, good)

    best = text
    best_score = _mojibake_score(best)

    for _ in range(3):
        improved = False
        for codec in ("cp1252", "latin1"):
            try:
                cand = best.encode(codec, errors="strict").decode("utf-8", errors="strict")
            except Exception:
                continue
            score = _mojibake_score(cand)
            if score < best_score:
                best, best_score = cand, score
                improved = True
        if not improved:
            break

    # Convert typographic characters to plain ASCII for consistent GUI rendering.
    ascii_map = {
        "\u2026": "...",
        "\u2013": "-",
        "\u2014": "-",
        "\u2022": "-",
        "\u2192": "->",
        "\u2190": "<-",
        "\u2191": "Up",
        "\u2193": "Down",
        "\u00d7": "x",
        "\u2265": ">=",
        "\u2264": "<=",
        "\u00b1": "+/-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\ufeff": "",
    }

    for bad, good in ascii_map.items():
        best = best.replace(bad, good)

    # Strip decorative emoji/pictographs. They read as unprofessional in the UI
    # and turn into mojibake ("") in non-UTF-8 log sinks. Punctuation stays.
    best = _EMOJI_RE.sub("", best)
    best = re.sub(r"[ \t]{2,}", " ", best)          # tidy gaps left behind
    best = re.sub(r"(?m)^[ \t]+", "", best)          # trim leading space per line
    return best.strip("\n") if "\n" in best else best.strip()


def format_bytes(size: int) -> str:
    power = 2 ** 10
    n = 0
    units = ["B", "KB", "MB", "GB"]
    while size > power and n < len(units) - 1:
        size /= power
        n += 1
    return f"{size:.2f} {units[n]}"
