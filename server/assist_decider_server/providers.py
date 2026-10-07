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


# name -> (Hugging Face repo, reviewed commit, calibration temperature from its inference.py)
INTERN_CHECKPOINTS = {
    "intern-decision-0.8b": (
        "internlm/Intern-Decision-0.8B",
        "85a0cc5a99d67ea8d56dfe98115689212867171d",
        2.747760550703,
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


class InternDecisionProvider:
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
        os.environ.setdefault("USE_TF", "0")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoTokenizer, Qwen3_5ForConditionalGeneration

        started = time.perf_counter()
        if self.device == "auto":
            self.device = (
                "cuda"
                if torch.cuda.is_available()
                else "mps"
                if torch.backends.mps.is_available()
                else "cpu"
            )
        dtype = torch.bfloat16
        if self.device.startswith("cuda") and not torch.cuda.is_bf16_supported():
            dtype = torch.float16  # GeForce before Ampere
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
        _LOGGER.info(
            "Loaded %s (%s@%s) on %s in %.1fs",
            self.model,
            self._repo,
            self._revision[:12],
            self.device,
            time.perf_counter() - started,
        )
        started = time.perf_counter()
        self.predict(
            {"utterance": "warm up"}, {"q": Question("Warm up?", {"a": "yes", "b": "no"})}, "en"
        )
        _LOGGER.info("Warm-up done in %.0f ms", (time.perf_counter() - started) * 1000)

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
        """Laya-shaped answers for choice, noul (yes/no) and score questions."""
        options: dict[str, list[tuple[str, str]]] = {}
        lines = []
        for key, q in questions.items():
            crit = q.get("criteria")
            if q["type"] == "choice":
                opts = [(str(k), str(v)) for k, v in crit.items()]
            elif q["type"] == "score":
                opts = [(str(i), str(v)) for i, v in enumerate(crit)]
            else:
                opts = [
                    ("no", "The answer is no (negative, or disagree with the claim)."),
                    ("yes", "The answer is yes (affirmative, or align with the claim)."),
                ]
            if not 1 <= len(opts) <= len(_SYMBOLS):
                raise ValueError(f"question {key!r}: 1-{len(_SYMBOLS)} options")
            options[key] = opts
            lines.append(f"{key}: {q.get('instructions', '')}")
            lines += [f"    {s} = {v}: {d}" for s, (v, d) in zip(_SYMBOLS, opts, strict=False)]
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
        out = {}
        for i, (key, opts) in enumerate(options.items()):
            # softmax(log(softmax(z)) / T) == softmax(z / T): the checkpoint's calibration.
            z = logits[i, self._symbol_ids[: len(opts)]].float() / self._temperature
            probs = dict(zip([v for v, _ in opts], torch.softmax(z, -1).tolist(), strict=True))
            best = max(probs, key=probs.__getitem__)
            out[key] = {
                "choice": best,
                "probabilities": probs,
                "noul": probs.get("yes"),
                "score": sum(float(v) * p for v, p in probs.items())
                if questions[key]["type"] == "score"
                else None,
            }
        return out


MODELS = sorted([*LAYA_CHECKPOINTS, *INTERN_CHECKPOINTS])


def make_provider(model: str, device: str = "auto") -> DecisionProvider:
    if model in INTERN_CHECKPOINTS:
        return InternDecisionProvider(model, device)
    return LayaProvider(model, device)
