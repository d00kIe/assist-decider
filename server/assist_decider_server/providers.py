"""Decision providers: models that pick one option per question.

The pipeline only talks to `DecisionProvider`. To add a model (NLI, cross-encoder, a hosted
API...), implement the protocol and construct it in __main__.build_provider.
"""

from __future__ import annotations

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
