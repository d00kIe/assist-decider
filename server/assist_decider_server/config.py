"""Server settings: defaults < TOML file < ASSIST_DECIDER_* environment < command line."""

from __future__ import annotations

import logging
import os
import stat
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

from .providers import LAYA_CHECKPOINTS

ENV_PREFIX = "ASSIST_DECIDER_"
MIN_TOKEN_LENGTH = 32
_LOGGER = logging.getLogger(__name__)


class ConfigError(Exception):
    """Invalid configuration; the message is shown to the user."""


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    token: str | None = None
    token_file: str | None = None
    model: str = "multilingual"
    device: str = "auto"
    max_pending: int = 4
    max_body_bytes: int = 1_048_576
    log_buffer: int = 2000
    log_level: str = "INFO"
    tls_certfile: str | None = None
    tls_keyfile: str | None = None

    def __repr__(self) -> str:  # never print the token
        shown = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "token"}
        return f"Settings({shown}, token={'set' if self.token else 'unset'})"


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
        if (
            "token" in data
            and os.name != "nt"
            and os.stat(path).st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        ):
            _LOGGER.warning(
                "%s contains the token but is readable by others; run: chmod 600 %s", path, path
            )
        values.update(data)

    for name in names:
        if (env := os.environ.get(ENV_PREFIX + name.upper())) is not None:
            values[name] = env
    values.update({k: v for k, v in overrides.items() if v is not None})

    settings = Settings(**{k: _coerce(k, v) for k, v in values.items()})
    if settings.token and settings.token_file:
        raise ConfigError("Set either token or token_file, not both (check file and environment)")
    if settings.token_file:
        try:
            settings = replace(settings, token=Path(settings.token_file).read_text().strip())
        except OSError as err:
            raise ConfigError(f"Cannot read token_file: {err}") from None

    if settings.model not in LAYA_CHECKPOINTS:
        raise ConfigError(f"model must be one of {', '.join(LAYA_CHECKPOINTS)}")
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


def require_token(settings: Settings) -> str:
    if not settings.token or len(settings.token) < MIN_TOKEN_LENGTH:
        raise ConfigError(
            f"A token of at least {MIN_TOKEN_LENGTH} characters is required. Create one with "
            f"'assist-decider gen-token' and set {ENV_PREFIX}TOKEN, token or token_file."
        )
    return settings.token
