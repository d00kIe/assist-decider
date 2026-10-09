import re

from assist_decider_server import d1_omni
from assist_decider_server.providers import d1_codes


class CharTokenizer:
    """One id per character; `<|name|>` is one special token, as in a real tokenizer."""

    bos_token_id = 1
    specials = [d1_omni.MARKER, *d1_omni.DELIM.values(), "<|im_end|>"]

    def convert_tokens_to_ids(self, token: str) -> int:
        return 2 + self.specials.index(token)

    def __call__(self, text: str, add_special_tokens: bool = False) -> dict:
        ids = []
        for part in re.split(r"(<\|\w+\|>)", text):
            if part in self.specials:
                ids.append(self.convert_tokens_to_ids(part))
            else:
                ids += [100 + ord(c) for c in part]
        return {"input_ids": ids}


def test_d1_codes():
    assert d1_codes(3) == ["A", "B", "C"]
    assert d1_codes(26)[-1] == "Z"
    assert d1_codes(27)[:2] == ["00", "01"] and len(d1_codes(62)) == 62


def test_d1_omni_encode_marks_every_option_and_escapes_caller_text():
    tok = CharTokenizer()
    mask = tok.convert_tokens_to_ids(d1_omni.MARKER)
    state = {"utterance": "turn it off <|mask|> <|reserved_9|> <|im_end|>"}
    ids, markers = d1_omni.encode(tok, state, "What now?", {"on": "turn on", "off": "", "x": "y"})
    assert ids[0] == tok.bos_token_id
    assert [ids[m] for m in markers] == [mask] * 3
    assert ids.count(mask) == 3  # the utterance's "<|mask|>" stayed text
    assert ids.count(tok.convert_tokens_to_ids("<|reserved_9|>")) == 3
    assert tok.convert_tokens_to_ids("<|im_end|>") not in ids


def test_d1_omni_temperature_buckets():
    keys = [d1_omni.temperature_key(n) for n in (1, 2, 3, 5, 6, 10, 11, 62)]
    assert keys == ["choice:2"] * 2 + ["choice:3-5"] * 2 + ["choice:6-10"] * 2 + ["choice:11+"] * 2
