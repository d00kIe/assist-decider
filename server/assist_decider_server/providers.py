"""Decision providers: models that pick one option per question.

The pipeline only talks to `DecisionProvider`. To add a model (NLI, cross-encoder, a hosted
API...), implement the protocol and construct it in __main__.build_provider.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Question:
    instructions: str
    options: dict[str, str]  # key -> description


@dataclass(frozen=True)
class Answer:
    choice: str
    probs: dict[str, float]


class DecisionProvider(Protocol):
    name: str
    model: str
    languages: tuple[str, ...]
    device: str

    def load(self) -> None:
        """Load weights. Blocking; called once at startup."""

    def predict(
        self, state: dict[str, Any], questions: dict[str, Question], lang: str
    ) -> dict[str, Answer]:
        """Answer every question about `state`. Blocking; never called concurrently."""


# checkpoint -> (Hugging Face repo, languages the pipeline supports with it)
LAYA_CHECKPOINTS = {
    "english": ("convaiinnovations/laya", ("en",)),
    "multilingual": ("convaiinnovations/laya-multilingual", ("en", "de")),
}


class LayaProvider:
    """Laya (https://github.com/NandhaKishorM/laya), one checkpoint kept resident."""

    name = "laya"

    def __init__(self, model: str, device: str = "auto") -> None:
        if model not in LAYA_CHECKPOINTS:
            raise ValueError(
                f"Unknown Laya model {model!r}, choose from {sorted(LAYA_CHECKPOINTS)}"
            )
        self.model = model
        self.languages = LAYA_CHECKPOINTS[model][1]
        self.device = device
        self._agent: Any = None

    def load(self) -> None:
        # transformers can hang importing TensorFlow when it happens to be installed.
        os.environ.setdefault("USE_TF", "0")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        import laya
        from laya.revisions import PINNED_REVISIONS

        repo = LAYA_CHECKPOINTS[self.model][0]
        started = time.perf_counter()
        # The revision is the upstream-reviewed commit: the download is content-addressed,
        # so a tampered or moved branch cannot change what we load.
        self._agent = laya.load(
            repo,
            device=None if self.device == "auto" else self.device,
            revision=PINNED_REVISIONS[repo],
        )
        self.device = str(getattr(self._agent, "device", self.device))
        _LOGGER.info(
            "Loaded Laya %s (%s@%s) on %s in %.1fs",
            self.model,
            repo,
            PINNED_REVISIONS[repo][:12],
            self.device,
            time.perf_counter() - started,
        )
        # The first call on MPS/CUDA compiles kernels; do it now, not on the first command.
        started = time.perf_counter()
        self.predict(
            {"utterance": "warm up"}, {"q": Question("Warm up?", {"a": "yes", "b": "no"})}, "en"
        )
        _LOGGER.info("Warm-up done in %.0f ms", (time.perf_counter() - started) * 1000)

    def predict(
        self, state: dict[str, Any], questions: dict[str, Question], lang: str
    ) -> dict[str, Answer]:
        raw = self._agent.predict(
            state,
            {
                key: {"type": "choice", "instructions": q.instructions, "criteria": dict(q.options)}
                for key, q in questions.items()
            },
            lang=lang,
        )["answers"]
        return {
            key: Answer(choice=raw[key]["choice"], probs=dict(raw[key]["probabilities"]))
            for key in questions
        }


def _torch_device(device: str) -> tuple[Any, str, Any]:
    """(torch, resolved device, dtype) for the transformer-backbone providers."""
    os.environ.setdefault("USE_TF", "0")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    import torch

    if device == "auto":
        device = (
            "cuda"
            if torch.cuda.is_available()
            else "mps"
            if torch.backends.mps.is_available()
            else "cpu"
        )
    dtype = torch.bfloat16
    if device.startswith("cuda") and not torch.cuda.is_bf16_supported():
        dtype = torch.float16  # GeForce before Ampere
    return torch, device, dtype


def _raw_options(q: dict[str, Any]) -> list[tuple[str, Any]]:
    """[(value, description or None)] of a raw choice / score / noul question, in order."""
    crit = q.get("criteria")
    if q["type"] == "choice":
        opts = [(str(k), v) for k, v in crit.items()]
    elif q["type"] == "score":
        opts = [(str(i), v) for i, v in enumerate(crit)]
    else:
        crit = {str(k).lower(): v for k, v in (crit or {}).items()}
        opts = [
            ("no", crit.get("no", crit.get("false"))),
            ("yes", crit.get("yes", crit.get("true"))),
        ]
    if not 1 <= len(opts) <= 62:
        raise ValueError("a question takes 1-62 options")
    return opts


class _ScoringProvider:
    """predict() and warm-up for providers whose score() answers raw choice/noul/score
    questions ({"type", "instructions", "criteria"}, as in Laya) with Laya-shaped answers."""

    model: str
    device: str

    def predict(
        self, state: dict[str, Any], questions: dict[str, Question], lang: str
    ) -> dict[str, Answer]:
        raw = self.score(
            state,
            {
                k: {"type": "choice", "instructions": q.instructions, "criteria": q.options}
                for k, q in questions.items()
            },
        )
        return {k: Answer(choice=a["choice"], probs=a["probabilities"]) for k, a in raw.items()}

    def score(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, dict]:
        raise NotImplementedError

    @staticmethod
    def _answers(questions: dict[str, dict[str, Any]], probs: list[list[float]]) -> dict[str, dict]:
        out = {}
        for (key, q), p in zip(questions.items(), probs, strict=True):
            dist = dict(zip([v for v, _ in _raw_options(q)], p, strict=True))
            out[key] = {
                "choice": max(dist, key=dist.__getitem__),
                "probabilities": dist,
                "noul": dist.get("yes"),
                "score": sum(float(v) * x for v, x in dist.items())
                if q["type"] == "score"
                else None,
            }
        return out

    def _loaded(self, repo: str, revision: str, started: float) -> None:
        _LOGGER.info(
            "Loaded %s (%s@%s) on %s in %.1fs",
            self.model,
            repo,
            revision[:12],
            self.device,
            time.perf_counter() - started,
        )
        started = time.perf_counter()
        self.predict(
            {"utterance": "warm up"}, {"q": Question("Warm up?", {"a": "yes", "b": "no"})}, "en"
        )
        _LOGGER.info("Warm-up done in %.0f ms", (time.perf_counter() - started) * 1000)


# name -> (Hugging Face repo, reviewed commit, calibration temperature from its inference.py)
INTERN_CHECKPOINTS = {
    "intern-decision-0.8b": (
        "internlm/Intern-Decision-0.8B",
        "85a0cc5a99d67ea8d56dfe98115689212867171d",
        2.747760550703,
    ),
    "intern-decision-2b": (
        "internlm/Intern-Decision-2B",
        "8797836c65fc91a2435b1fb6850b5f0aabd75cc3",
        2.100509348278,
    ),
}
_DECISION = "<decision>"
_SYMBOLS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_SYSTEM = (
    "You are a careful decision assistant. Use the state and decision schema in the user "
    "message to make the requested decisions. For every field, choose exactly one answer "
    "symbol (e.g. A, B, C, ...) from its listed options and return one valid JSON object "
    "mapping each field name to its chosen symbol. Use the field names and symbols exactly "
    "as given. Do not include explanations, Markdown, or extra text."
)
_NOUL_TEXT = {
    "no": "The answer is no (negative, or disagree with the claim).",
    "yes": "The answer is yes (affirmative, or align with the claim).",
}


class InternDecisionProvider(_ScoringProvider):
    """Intern-Decision (https://huggingface.co/internlm/Intern-Decision-0.8B), text only.

    Rebuilds the prompt of the checkpoint's own inference.py (text path) instead of importing
    it: no downloaded code runs, and Pillow/torchvision are not needed. One forward pass reads
    the logit just before each <decision> marker of a JSON answer skeleton.
    """

    name = "intern-decision"
    languages = ("en", "de")

    def __init__(self, model: str, device: str = "auto") -> None:
        if model not in INTERN_CHECKPOINTS:
            raise ValueError(f"Unknown model {model!r}, choose from {sorted(INTERN_CHECKPOINTS)}")
        self.model = model
        self.device = device
        self._repo, self._revision, self._temperature = INTERN_CHECKPOINTS[model]

    def load(self) -> None:
        started = time.perf_counter()
        torch, self.device, dtype = _torch_device(self.device)
        from huggingface_hub import snapshot_download
        from transformers import AutoTokenizer, Qwen3_5ForConditionalGeneration

        # Pinned commit: the download is content-addressed, a moved branch can't change it.
        path = snapshot_download(
            self._repo,
            revision=self._revision,
            allow_patterns=["*.json", "*.jinja", "*.txt", "*.safetensors"],
        )
        self._torch = torch
        self._tok = AutoTokenizer.from_pretrained(path, local_files_only=True)
        self._marker = self._tok.convert_tokens_to_ids(_DECISION)
        self._symbol_ids = [self._tok.encode(s, add_special_tokens=False)[0] for s in _SYMBOLS]
        self._model = (
            Qwen3_5ForConditionalGeneration.from_pretrained(
                path, dtype=dtype, local_files_only=True, attn_implementation="sdpa"
            )
            .to(self.device)
            .eval()
        )
        self._loaded(self._repo, self._revision, started)

    def score(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, dict]:
        """Laya-shaped answers for choice, noul (yes/no) and score questions."""
        lines = []
        for key, q in questions.items():
            lines.append(f"{key}: {q.get('instructions', '')}")
            for s, (v, d) in zip(_SYMBOLS, _raw_options(q), strict=False):
                d = _NOUL_TEXT[v] if q["type"] == "noul" and d in (None, "") else d
                lines.append(f"    {s} = {v}: {d}")
        user = (
            "Return one answer for every field using the supplied answer symbols.\n\n## State\n"
            + json.dumps(state, ensure_ascii=False, indent=2)
            + "\n## Decision schema\n"
            + "\n".join(lines)
        ).replace(_DECISION, "decision")  # the marker is a special token; never from input
        skeleton = json.dumps(dict.fromkeys(questions, _DECISION), ensure_ascii=False, indent=4)
        text = self._tok.apply_chat_template(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
                {"role": "assistant", "content": skeleton},
            ],
            tokenize=False,
            add_generation_prompt=False,
            enable_thinking=False,
            add_vision_id=True,
        )
        torch = self._torch
        ids = self._tok(text, add_special_tokens=False, return_tensors="pt")["input_ids"]
        positions = (ids[0] == self._marker).nonzero().flatten() - 1
        if len(positions) != len(questions):
            raise ValueError("decision marker count mismatch")
        with torch.inference_mode():
            logits = self._model(
                input_ids=ids.to(self.device),
                use_cache=False,
                logits_to_keep=positions.to(self.device),
            ).logits[0]
        probs = []
        for i, q in enumerate(questions.values()):
            # softmax(log(softmax(z)) / T) == softmax(z / T): the checkpoint's calibration.
            z = logits[i, self._symbol_ids[: len(_raw_options(q))]].float() / self._temperature
            probs.append(torch.softmax(z, -1).tolist())
        return self._answers(questions, probs)


# name -> (Hugging Face repo, reviewed commit). The base model, its commit, the head and the
# calibration temperature come from the checkpoint's head.pt.
KEV_CHECKPOINTS = {
    "kev-0.8b": ("jaredpalmer/kev-0.8b", "bf75a6a8848ea6960ff2ed108d9ed44c2941174f"),
}
# Kev's delimiters: rarely used Qwen special tokens for <state> <q> <opt> </opt> <decide>.
_KEV_SPECIAL = [
    "<|fim_prefix|>",
    "<|fim_middle|>",
    "<|box_start|>",
    "<|box_end|>",
    "<|fim_suffix|>",
]
_KEV_SPECIAL_RE = re.compile(r"<\|([A-Za-z0-9_]+)\|>")


def _kev_render(v: Any, indent: int = 0) -> str:
    """kev/api.py render(): str | object | array as labelled text."""
    pad = "  " * indent
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        return str(v)
    if isinstance(v, list):
        return "\n".join(f"{pad}- {_kev_render(x, indent + 1).lstrip()}" for x in v)
    return "\n".join(
        f"{pad}{k}:\n{_kev_render(x, indent + 1)}"
        if isinstance(x, (dict, list))
        else f"{pad}{k}: {_kev_render(x)}"
        for k, x in v.items()
    )


class KevProvider(_ScoringProvider):
    """Kev (https://github.com/jaredpalmer/kev, commit 5e42a7a): Qwen3.5 base + LoRA + pointer head.

    Rebuilds kev/api.py (rendering), kev/model.py (encode, row form, PointerHead) and
    kev/checkpoint.py (LoRA merged in fp32) without the kev package or peft. Every question is
    its own causal row: <state> state <q> instructions (<opt> option </opt>)... <decide>; the
    head scores each </opt> hidden state against the <decide> one.
    """

    name = "kev"
    languages = ("en", "de")  # trained on English only; German is measured, not promised

    def __init__(self, model: str, device: str = "auto") -> None:
        if model not in KEV_CHECKPOINTS:
            raise ValueError(f"Unknown model {model!r}, choose from {sorted(KEV_CHECKPOINTS)}")
        self.model = model
        self.device = device
        self._repo, self._revision = KEV_CHECKPOINTS[model]

    def load(self) -> None:
        started = time.perf_counter()
        torch, self.device, dtype = _torch_device(self.device)
        from huggingface_hub import snapshot_download
        from safetensors.torch import load_file
        from transformers import AutoModelForCausalLM, AutoTokenizer

        path = snapshot_download(
            self._repo, revision=self._revision, allow_patterns=["*.json", "*.safetensors", "*.pt"]
        )
        meta = torch.load(f"{path}/head.pt", map_location="cpu", weights_only=True)
        base, base_rev = meta["base"], meta["base_revision"]
        self._tok = AutoTokenizer.from_pretrained(base, revision=base_rev)
        # kev uses eager attention off CUDA (its float masks); rows here only need padding masks.
        attn = "sdpa" if self.device.startswith("cuda") else "eager"
        lm = AutoModelForCausalLM.from_pretrained(
            base, revision=base_rev, dtype=torch.float32, attn_implementation=attn
        ).model
        with open(f"{path}/adapter_config.json") as f:
            cfg = json.load(f)
        lora = load_file(f"{path}/adapter_model.safetensors")
        merged = 0
        with torch.no_grad():  # W += B @ A * alpha / r in fp32, as peft's merge_and_unload
            for key, a in lora.items():
                if key.endswith(".lora_A.weight"):
                    name = key.removeprefix("base_model.model.").removesuffix(".lora_A.weight")
                    b = lora[key.replace(".lora_A.", ".lora_B.")]
                    lm.get_submodule(name).weight += (b @ a) * (cfg["lora_alpha"] / cfg["r"])
                    merged += 1
        if not merged or merged * 2 != len(lora):
            raise ValueError(f"{self._repo}: unexpected adapter layout")
        self._lm = lm.to(device=self.device, dtype=dtype).eval()
        head = {k: v.to(self.device, torch.float32) for k, v in meta["head"].items()}
        self._head = head
        self._head_scale = 1 / (head["q.weight"].shape[0] ** 0.5) / meta["temperature"]
        self._special = [self._tok.convert_tokens_to_ids(t) for t in _KEV_SPECIAL]
        self._pad = self._tok.pad_token_id if self._tok.pad_token_id is not None else 0
        self._torch = torch
        self._loaded(self._repo, self._revision, started)

    def _tokens(self, text: str) -> list[int]:
        # caller text can never produce delimiter tokens: <|x|> becomes <¦x¦> first (kev's rule)
        return self._tok(_KEV_SPECIAL_RE.sub(r"<¦\1¦>", text), add_special_tokens=False).input_ids

    def score(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, dict]:
        torch = self._torch
        st, q_id, o_id, c_id, d_id = self._special
        S = [st, *self._tokens(_kev_render(state))]
        rows, reads = [], []
        for q in questions.values():
            branch = [q_id, *self._tokens(_kev_render(q.get("instructions")))]
            ends = []
            for v, d in _raw_options(q):
                text = (
                    _kev_render(d)
                    if q["type"] == "score"
                    else v
                    if d in (None, "")
                    else f"{v}: {_kev_render(d)}"
                )
                branch += [o_id, *self._tokens(text), c_id]
                ends.append(len(S) + len(branch) - 1)
            branch.append(d_id)
            rows.append(S + branch)
            reads.append((len(rows[-1]) - 1, ends))
        width = max(len(r) for r in rows)
        ids = torch.full((len(rows), width), self._pad)
        att = torch.zeros((len(rows), width), dtype=torch.long)
        for i, r in enumerate(rows):
            ids[i, : len(r)] = torch.tensor(r)
            att[i, : len(r)] = 1
        pos = torch.arange(width).expand(len(rows), width)
        h = self._head
        with torch.inference_mode():
            hidden = self._lm(
                input_ids=ids.to(self.device),
                position_ids=pos.to(self.device),
                attention_mask=att.to(self.device),
            ).last_hidden_state.float()
            probs = []
            for i, (decide, ends) in enumerate(reads):
                qv = hidden[i, decide] @ h["q.weight"].T + h["q.bias"]
                kv = hidden[i, ends] @ h["k.weight"].T + h["k.bias"]
                probs.append(torch.softmax(kv @ qv * self._head_scale, -1).tolist())
        return self._answers(questions, probs)


# name -> (Hugging Face repo, reviewed commit = tag v1.2.1). Temperature and noul floor from its
# serve_config.json.
H2O_CHECKPOINTS = {
    "h2o-lightning-4b": ("h2oai/h2o-lightning-4b", "542e9eff5ce7e5d69eb457fbe54abb535992ab20"),
}
_H2O_SYSTEM = (
    "You are a decision engine. You read a record and answer one question about it by choosing "
    "exactly one option. Reply with a single letter."
)
_H2O_LABELS = _SYMBOLS[:52] + "αβγδεζηθικ"  # serve_config.json labels, first 62
_H2O_TEMPERATURE = 0.8
_H2O_NOUL_FLOOR = 0.801


class H2OLightningProvider(_ScoringProvider):
    """H2O-Lightning-4B (https://huggingface.co/h2oai/h2o-lightning-4b), text only.

    Rebuilds h2o_lightning_shim.py's text path instead of running it behind vLLM: one prompt per
    question (system, `record:/question:/options:` user turn, thinking off, "Answer:" prefill),
    the label logits " A", " B"... read in fp32 (config.json head_dtype), softmax at T=0.8, then
    the yes/no floor.
    """

    name = "h2o-lightning"
    languages = ("en", "de")  # evaluated mostly in English; German is measured, not promised

    def __init__(self, model: str, device: str = "auto") -> None:
        if model not in H2O_CHECKPOINTS:
            raise ValueError(f"Unknown model {model!r}, choose from {sorted(H2O_CHECKPOINTS)}")
        self.model = model
        self.device = device
        self._repo, self._revision = H2O_CHECKPOINTS[model]

    def load(self) -> None:
        started = time.perf_counter()
        torch, self.device, dtype = _torch_device(self.device)
        from huggingface_hub import snapshot_download
        from transformers import AutoTokenizer, Qwen3_5ForConditionalGeneration

        path = snapshot_download(
            self._repo,
            revision=self._revision,
            allow_patterns=["*.json", "*.jinja", "*.txt", "*.safetensors"],
        )
        self._torch = torch
        self._tok = AutoTokenizer.from_pretrained(path, local_files_only=True)
        self._label_ids = []
        for s in _H2O_LABELS:  # the shim's check: " <label>" is one token at the answer slot
            ids = self._tok.encode("Answer: " + s, add_special_tokens=False)
            if ids[:-1] != self._tok.encode("Answer:", add_special_tokens=False):
                raise ValueError(f"{self._repo}: label {s!r} is not one token")
            self._label_ids.append(ids[-1])
        self._model = (
            Qwen3_5ForConditionalGeneration.from_pretrained(
                path, dtype=dtype, local_files_only=True, attn_implementation="sdpa"
            )
            .to(self.device)
            .eval()
        )
        self._head = self._model.get_output_embeddings().weight[self._label_ids].detach().float()
        self._loaded(self._repo, self._revision, started)

    def _tokens(self, text: str, **kw: Any) -> list[int]:
        return self._tok(text, add_special_tokens=False, **kw)["input_ids"]

    def score(self, state: Any, questions: dict[str, dict[str, Any]]) -> dict[str, dict]:
        torch = self._torch
        record = (
            state
            if isinstance(state, str)
            else json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        )
        probs = []
        for q in questions.values():
            opts = _raw_options(q)
            if q["type"] == "noul":  # the shim asks [true, false]
                opts = [
                    ("true", opts[1][1] or "the statement holds"),
                    ("false", opts[0][1] or "it does not"),
                ]
            descs = [
                v if d is None else d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)
                for v, d in opts
            ]
            lines = "\n".join(
                f"{s}) {v}: {d}" for s, (v, _), d in zip(_H2O_LABELS, opts, descs, strict=False)
            )
            instr = q.get("instructions") or "Answer the question below."
            instr = instr if isinstance(instr, str) else json.dumps(instr, indent=1)
            user = f"record: {record}\nquestion: {instr.lstrip()}\noptions:\n{lines}"
            # caller text never becomes a special token (<|im_end|> in an utterance stays text)
            ids = [
                *self._tokens(f"<|im_start|>system\n{_H2O_SYSTEM}<|im_end|>\n<|im_start|>user\n"),
                *self._tokens(user.strip(), split_special_tokens=True),
                *self._tokens("<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\nAnswer:"),
            ]
            with torch.inference_mode():
                h = (
                    self._model.model(
                        input_ids=torch.tensor([ids], device=self.device), use_cache=False
                    )
                    .last_hidden_state[0, -1]
                    .float()
                )
            p = torch.softmax(self._head[: len(opts)] @ h / _H2O_TEMPERATURE, -1).tolist()
            if q["type"] == "noul":
                y = p[0]
                if max(y, 1 - y) < _H2O_NOUL_FLOOR:  # commit_noul: the answer never changes
                    y = _H2O_NOUL_FLOOR if y >= 0.5 else 1 - _H2O_NOUL_FLOOR
                p = [1 - y, y]  # back to _raw_options' [no, yes]
            probs.append(p)
        return self._answers(questions, probs)


MODELS = sorted([*LAYA_CHECKPOINTS, *INTERN_CHECKPOINTS, *KEV_CHECKPOINTS, *H2O_CHECKPOINTS])


def make_provider(model: str, device: str = "auto") -> DecisionProvider:
    if model in INTERN_CHECKPOINTS:
        return InternDecisionProvider(model, device)
    if model in H2O_CHECKPOINTS:
        return H2OLightningProvider(model, device)
    if model in KEV_CHECKPOINTS:
        return KevProvider(model, device)
    return LayaProvider(model, device)
