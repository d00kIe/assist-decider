"""Decision providers: models that pick one option per question.

The pipeline only talks to `DecisionProvider`. To add a model (NLI, cross-encoder, a hosted
API...), implement the protocol and construct it in __main__.build_provider.
"""

from __future__ import annotations

import json
import logging
import os
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


def _check(questions: dict[str, Question]) -> None:
    if not all(1 <= len(q.options) <= 62 for q in questions.values()):
        raise ValueError("a question takes 1-62 options")


class _ScoringProvider:
    """Shared by the providers that score each option with a transformer model."""

    model: str
    device: str

    @staticmethod
    def _answers(questions: dict[str, Question], probs: list[list[float]]) -> dict[str, Answer]:
        out = {}
        for (key, q), p in zip(questions.items(), probs, strict=True):
            dist = dict(zip(q.options, p, strict=True))
            out[key] = Answer(choice=max(dist, key=dist.__getitem__), probs=dist)
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

    def predict(
        self, state: dict[str, Any], questions: dict[str, Question], lang: str
    ) -> dict[str, Answer]:
        _check(questions)
        # Neutral field names: the caller's keys are for code, not for the model.
        fields = [f"q{i}" for i in range(1, len(questions) + 1)]
        lines = []
        for field, q in zip(fields, questions.values(), strict=True):
            lines.append(f"{field}: {q.instructions}")
            for s, (v, d) in zip(_SYMBOLS, q.options.items(), strict=False):
                lines.append(f"    {s} = {v}: {d}")
        user = (
            "Return one answer for every field using the supplied answer symbols.\n\n## State\n"
            + json.dumps(state, ensure_ascii=False, indent=2)
            + "\n## Decision schema\n"
            + "\n".join(lines)
        ).replace(_DECISION, "decision")  # the marker is a special token; never from input
        skeleton = json.dumps(dict.fromkeys(fields, _DECISION), ensure_ascii=False, indent=4)
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
            z = logits[i, self._symbol_ids[: len(q.options)]].float() / self._temperature
            probs.append(torch.softmax(z, -1).tolist())
        return self._answers(questions, probs)


# name -> (Hugging Face repo, reviewed commit). LFM Open License v1.0.
D1_CHECKPOINTS = {
    "d1-3b": ("LiquidAI/d1-3B", "051bcc464b01b9f92942b364d9586b0ef5912432"),
}


def d1_codes(n: int) -> list[str]:
    """d1's option codes: A..Z, or 00.. past 26 options (prompt.py option_codes)."""
    return [chr(65 + i) for i in range(n)] if n <= 26 else [f"{i:02d}" for i in range(n)]


class D1Provider(_ScoringProvider):
    """d1-3B (https://huggingface.co/LiquidAI/d1-3B), text only.

    Rebuilds the defaults of the checkpoint's prompt.py and runner.py instead of running its remote
    code: no system turn, the state as indented JSON, options as "<code> <description>", and each
    option scored by the log-probability of its code (bare or after a space) at the answer slot.
    """

    name = "d1"
    languages = ("en", "de")

    def __init__(self, model: str, device: str = "auto") -> None:
        if model not in D1_CHECKPOINTS:
            raise ValueError(f"Unknown model {model!r}, choose from {sorted(D1_CHECKPOINTS)}")
        self.model = model
        self.device = device
        self._repo, self._revision = D1_CHECKPOINTS[model]

    def load(self) -> None:
        started = time.perf_counter()
        torch, self.device, dtype = _torch_device(self.device)
        from huggingface_hub import snapshot_download
        from transformers import Lfm2VlForConditionalGeneration, PreTrainedTokenizerFast

        path = snapshot_download(
            self._repo,
            revision=self._revision,
            allow_patterns=["*.json", "*.jinja", "*.safetensors"],
        )
        self._torch = torch
        self._tok = PreTrainedTokenizerFast.from_pretrained(path, local_files_only=True)
        self._code_ids = {}
        for code in d1_codes(26) + d1_codes(62):
            bare = self._tokens(code)
            if len(bare) != 1:
                raise ValueError(f"{self._repo}: option code {code!r} is not one token")
            spaced = self._tokens(" " + code)
            self._code_ids[code] = bare + (spaced if len(spaced) == 1 else [])
        self._model = (
            Lfm2VlForConditionalGeneration.from_pretrained(
                path,
                dtype=torch.float32 if self.device == "cpu" else dtype,
                local_files_only=True,
                attn_implementation="sdpa",
            )
            .to(self.device)
            .eval()
        )
        self._loaded(self._repo, self._revision, started)

    def _tokens(self, text: str, **kw: Any) -> list[int]:
        return self._tok(text, add_special_tokens=False, **kw)["input_ids"]

    def predict(
        self, state: dict[str, Any], questions: dict[str, Question], lang: str
    ) -> dict[str, Answer]:
        _check(questions)
        torch = self._torch
        state_text = json.dumps(state, ensure_ascii=False, indent=2)
        probs = []
        for q in questions.values():
            codes = d1_codes(len(q.options))
            lines = "\n".join(
                f"{c} {d or k.replace('_', ' ')}"
                for c, (k, d) in zip(codes, q.options.items(), strict=True)
            )
            user = (
                f"user\n{state_text}\n\n\nQUESTION:\n{q.instructions}\n\nOptions:\n{lines}"
                "\n\nReply with the option code only."
            )
            # caller text never becomes a special token (<|im_end|> in an utterance stays text)
            ids = [
                *self._tokens("<|startoftext|><|im_start|>"),
                *self._tokens(user, split_special_tokens=True),
                *self._tokens("<|im_end|>\n<|im_start|>assistant\n"),
            ]
            with torch.inference_mode():
                z = self._model(
                    input_ids=torch.tensor([ids], device=self.device), logits_to_keep=1
                ).logits[0, -1]
                logz = torch.log_softmax(z.float(), -1)
                scores = torch.stack([logz[self._code_ids[c]].max() for c in codes])
                probs.append(torch.softmax(scores, -1).tolist())
        return self._answers(questions, probs)


# name -> (Hugging Face repo, reviewed commit). LFM Open License v1.0.
D1_OMNI_CHECKPOINTS = {
    "d1-omni-600m": ("LiquidAI/d1-omni-600M", "02b55d7076f15129e59ab3f94783f32c4b088674"),
}


class D1OmniProvider(_ScoringProvider):
    """d1-omni-600M (https://huggingface.co/LiquidAI/d1-omni-600M), text only: see d1_omni.py.

    Text answers are calibrated with the per-option-count temperatures in its config.json.
    """

    name = "d1-omni"
    languages = ("en", "de")

    def __init__(self, model: str, device: str = "auto") -> None:
        if model not in D1_OMNI_CHECKPOINTS:
            raise ValueError(f"Unknown model {model!r}, choose from {sorted(D1_OMNI_CHECKPOINTS)}")
        self.model = model
        self.device = device
        self._repo, self._revision = D1_OMNI_CHECKPOINTS[model]

    def load(self) -> None:
        started = time.perf_counter()
        torch, self.device, _ = _torch_device(self.device)
        from huggingface_hub import snapshot_download
        from safetensors import safe_open
        from transformers import PreTrainedTokenizerFast

        from . import d1_omni

        path = snapshot_download(
            self._repo, revision=self._revision, allow_patterns=["*.json", "*.safetensors"]
        )
        with open(f"{path}/config.json") as f:
            config = json.load(f)
        self._temperatures = config["temperatures"]
        # the tokenizer class itself: AutoTokenizer reads config.json and offers its remote code
        self._tok = PreTrainedTokenizerFast.from_pretrained(path, local_files_only=True)
        net = d1_omni.D1Omni(config)
        with safe_open(f"{path}/model.safetensors", "pt") as f:  # vision/audio towers stay on disk
            weights = {k: f.get_tensor(k) for k in f.keys() if k.startswith(("encoder.", "head."))}  # noqa: SIM118
        net.load_state_dict(weights, strict=True)
        # The model card: float16 keeps the float32 answers on GPUs, bfloat16 does not.
        dtype = torch.float32 if self.device == "cpu" else torch.float16
        self._net = net.to(self.device, dtype).eval()
        self._encode = d1_omni.encode
        self._temperature_key = d1_omni.temperature_key
        self._torch = torch
        self._loaded(self._repo, self._revision, started)

    def predict(
        self, state: dict[str, Any], questions: dict[str, Question], lang: str
    ) -> dict[str, Answer]:
        _check(questions)
        torch = self._torch
        probs = []
        for q in questions.values():
            ids, markers = self._encode(self._tok, state, q.instructions, q.options)
            t = self._temperatures.get(
                self._temperature_key(len(q.options)), self._temperatures.get("choice", 1.0)
            )
            with torch.inference_mode():
                z = self._net(
                    torch.tensor([ids], device=self.device),
                    torch.tensor(markers, device=self.device),
                )
                probs.append(torch.softmax(z / t, -1).tolist())
        return self._answers(questions, probs)


MODELS = sorted([*LAYA_CHECKPOINTS, *INTERN_CHECKPOINTS, *D1_CHECKPOINTS, *D1_OMNI_CHECKPOINTS])


def make_provider(model: str, device: str = "auto") -> DecisionProvider:
    if model in INTERN_CHECKPOINTS:
        return InternDecisionProvider(model, device)
    if model in D1_CHECKPOINTS:
        return D1Provider(model, device)
    if model in D1_OMNI_CHECKPOINTS:
        return D1OmniProvider(model, device)
    return LayaProvider(model, device)
