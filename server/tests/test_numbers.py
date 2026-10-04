import pytest

from assist_decider_server.lang import fold
from assist_decider_server.numbers import Num, extract


@pytest.mark.parametrize(
    ("text", "lang", "expected"),
    [
        ("set the light to 50%", "en", [Num(50, "pct")]),
        ("set the light to 50 percent", "en", [Num(50, "pct")]),
        ("set the heating to twenty one degrees", "en", [Num(21, "deg")]),
        ("2 hours and 15 minutes", "en", [Num(2, "h"), Num(15, "min")]),
        ("half an hour", "en", [Num(30, "min")]),
        ("21 point 5 degrees", "en", [Num(21.5, "deg")]),
        ("3 and a half minutes", "en", [Num(3.5, "min")]),
        ("Stell das Licht auf fünfzig Prozent", "de", [Num(50, "pct")]),
        ("Heizung auf 21,5 Grad", "de", [Num(21.5, "deg")]),
        ("einundzwanzig komma fünf Grad", "de", [Num(21.5, "deg")]),
        ("21°C", "de", [Num(21, "deg")]),
        ("anderthalb Minuten", "de", [Num(1.5, "min")]),
        ("zweieinhalb Stunden", "de", [Num(2.5, "h")]),
        ("eine halbe Stunde", "de", [Num(30, "min")]),
        ("eine Stunde", "de", [Num(1, "h")]),
        ("Rollladen auf dreißig", "de", [Num(30, "")]),
        # "ein" is the separable particle of "einschalten", not the number one.
        ("Schalte das Licht ein", "de", []),
        ("Küche, Flur", "de", []),
    ],
)
def test_extract(text: str, lang: str, expected: list[Num]) -> None:
    assert extract(fold(text), lang) == expected
