"""Numbers, units and durations in folded English/German text.

Laya only chooses between options, so every value (brightness, temperature, ...) comes
from here. Input is folded text (see lang.fold); number words 0-100 come from unicode-rbnf.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

from unicode_rbnf import RbnfEngine

from .lang import fold

# German articles double as "on" particles ("schalte das Licht ein"), so they only count as
# 1 directly before a unit ("eine Stunde"). Same for English "a"/"an".
_AMBIGUOUS_ONE = frozenset({"ein", "eine", "einen", "einer", "eines", "einem"})
_ONE_BEFORE_UNIT = re.compile(
    r"(?<!\w)(?:ein|eine|einen|einer|a|an|one)\s+(?=(?:hours?|minutes?|seconds?|stunden?|minuten?|sekunden?)(?!\w))"
)
_FRACTIONS = [
    (re.compile(r"(?<!\w)(?:a\s+)?half\s+(?:an?\s+)?hour(?!\w)"), "30 min"),
    (re.compile(r"(?<!\w)halbe?n?\s+stunde(?!\w)"), "30 min"),
    (re.compile(r"(?<!\w)dreiviertelstunde(?!\w)"), "45 min"),
    (re.compile(r"(?<!\w)viertelstunde(?!\w)"), "15 min"),
    (re.compile(r"(?<!\w)(?:a\s+)?quarter\s+(?:of\s+)?(?:an\s+)?hour(?!\w)"), "15 min"),
    (re.compile(r"(?<!\w)(?:anderthalb|eineinhalb)(?!\w)"), "1.5"),
]
_UNITS = {
    "pct": r"%|prozent|percent|pct",
    "deg": r"°\s*[cf]?|grad(?:\s+celsius)?|degrees?(?:\s+(?:celsius|fahrenheit))?|celsius",
    "h": r"hours?|hrs?|stunden?|std|h",
    "min": r"minutes?|mins?|minuten?",
    "s": r"seconds?|secs?|sekunden?|sek|s",
}
_NUM_RE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)(?:\s*(?:"
    + "|".join(f"(?P<{unit}>{pattern})" for unit, pattern in _UNITS.items())
    + r")(?!\w))?"
)


@dataclass(frozen=True)
class Num:
    value: float
    unit: str  # "pct" | "deg" | "h" | "min" | "s" | "" (bare)


@cache
def _words(lang: str) -> tuple[dict[str, int], re.Pattern[str]]:
    engine = RbnfEngine.for_language(lang)
    table: dict[str, int] = {}
    for n in range(101):
        for text in engine.format_number(n).text_by_ruleset.values():
            word = fold(text).replace("-", " ").strip()
            if word and word not in _AMBIGUOUS_ONE:
                table.setdefault(word, n)
    alternation = "|".join(re.escape(w) for w in sorted(table, key=len, reverse=True))
    return table, re.compile(rf"(?<!\w)({alternation})(?!\w)")


def _digits(text: str, lang: str) -> str:
    """Rewrite spoken numbers as digits: 'einundzwanzig komma fuenf grad' -> '21.5 grad'."""
    for pattern, replacement in _FRACTIONS:
        text = pattern.sub(replacement, text)
    table, word_re = _words(lang)
    text = word_re.sub(lambda m: str(table[m.group(1)]), text)
    # "zweieinhalb" -> 2.5
    text = re.sub(
        r"(?<!\w)(\w+?)einhalb(?!\w)",
        lambda m: f"{table[m.group(1)]}.5" if m.group(1) in table else m.group(0),
        text,
    )
    text = re.sub(r"(?<!\w)(\d+)\s+(?:komma|point)\s+(\d+)(?!\w)", r"\1.\2", text)
    text = re.sub(r"(?<!\w)(\d+)\s+and\s+a\s+half(?!\w)", r"\1.5", text)
    return _ONE_BEFORE_UNIT.sub("1 ", text)


def strip(folded: str, lang: str) -> str:
    """`folded` without its numbers and their units: what is left is not a value."""
    return _NUM_RE.sub(" ", _digits(folded, lang))


def extract(folded: str, lang: str) -> list[Num]:
    """All numbers in `folded` text, in order, with the unit that follows them."""
    nums = []
    for m in _NUM_RE.finditer(_digits(folded, lang)):
        unit = next((u for u in _UNITS if m.group(u)), "")
        nums.append(Num(float(m.group(1)), unit))
    return nums
