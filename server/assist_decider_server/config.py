"""Server settings: defaults < TOML file < ASSIST_DECIDER_* environment < command line."""

from __future__ import annotations

import json
import logging
import os
import tomllib
from dataclasses import dataclass, fields
from typing import Any

from .providers import MODELS

_LOGGER = logging.getLogger(__name__)
ENV_PREFIX = "ASSIST_DECIDER_"


class ConfigError(Exception):
    """Invalid configuration; the message is shown to the user."""


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    model: str = "multilingual"
    device: str = "auto"
    max_pending: int = 4
    max_body_bytes: int = 1_048_576
    log_buffer: int = 2000
    log_level: str = "INFO"
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    # The model last chosen in Home Assistant; default ~/.local/state/assist-decider/state.json
    state_file: str | None = None


def _coerce(name: str, value: Any) -> Any:
    kind = {f.name: f.type for f in fields(Settings)}[name]
    if value is None or value == "":
        return None if "None" in str(kind) else value
    if "int" in str(kind):
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{name} must be a number, got {value!r}") from None
    return str(value)


def load_settings(config_file: str | None = None, **overrides: Any) -> Settings:
    names = {f.name for f in fields(Settings)}
    values: dict[str, Any] = {}

    path = config_file or os.environ.get(f"{ENV_PREFIX}CONFIG")
    if path:
        try:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as err:
            raise ConfigError(f"Cannot read config file {path}: {err}") from None
        if unknown := set(data) - names:
            raise ConfigError(f"Unknown settings in {path}: {', '.join(sorted(unknown))}")
        values.update(data)

    for name in names:
        if (env := os.environ.get(ENV_PREFIX + name.upper())) is not None:
            values[name] = env
    values.update({k: v for k, v in overrides.items() if v is not None})

    settings = Settings(**{k: _coerce(k, v) for k, v in values.items()})
    if settings.model not in MODELS:
        raise ConfigError(f"model must be one of {', '.join(MODELS)}")
    if settings.device not in ("auto", "cpu", "mps", "xpu") and not settings.device.startswith(
        "cuda"
    ):
        raise ConfigError("device must be auto, cpu, cuda, cuda:N, mps or xpu")
    if not 0 < settings.port < 65536:
        raise ConfigError("port must be 1-65535")
    if settings.max_pending < 1 or settings.max_body_bytes < 1024 or settings.log_buffer < 10:
        raise ConfigError("max_pending >= 1, max_body_bytes >= 1024 and log_buffer >= 10 required")
    if settings.log_level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise ConfigError("log_level must be DEBUG, INFO, WARNING or ERROR")
    if bool(settings.tls_certfile) != bool(settings.tls_keyfile):
        raise ConfigError("tls_certfile and tls_keyfile must be set together")
    return settings


def _state_path(settings: Settings) -> str:
    if settings.state_file:
        return settings.state_file
    root = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(root, "assist-decider", "state.json")


def remembered_model(settings: Settings) -> str:
    """The model last chosen in Home Assistant. A changed `model` setting wins: the choice is
    only kept while the setting is what it was when the choice was made."""
    path = _state_path(settings)
    try:
        with open(path) as fh:
            state = json.load(fh)
    except FileNotFoundError:
        return settings.model
    except (OSError, ValueError) as err:
        _LOGGER.warning("Ignoring %s: %s", path, err)
        return settings.model
    if (
        isinstance(state, dict)
        and state.get("configured") == settings.model
        and state.get("model") in MODELS
    ):
        if state["model"] != settings.model:
            _LOGGER.info("Using %s, chosen in Home Assistant (%s)", state["model"], path)
        return str(state["model"])
    return settings.model


def remember_model(settings: Settings, model: str) -> None:
    path = _state_path(settings)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as fh:
            json.dump({"model": model, "configured": settings.model}, fh)
        os.replace(path + ".tmp", path)  # never a half-written file
    except OSError as err:
        _LOGGER.warning("Cannot save the model choice to %s: %s", path, err)
