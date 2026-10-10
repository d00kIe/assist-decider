"""Command line: assist-decider [serve|download] [options]."""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import ConfigError, Settings, load_settings, remember_model, remembered_model
from .logbuf import BusHandler, EventBus
from .providers import MODELS, make_provider


def _setup_logging(settings: Settings, bus: EventBus | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if bus is not None:
        handlers.append(BusHandler(bus))
    for handler in handlers:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=settings.log_level.upper(), handlers=handlers, force=True)
    for noisy in ("httpx", "huggingface_hub", "urllib3", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="assist-decider", description=__doc__)
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "download"])
    parser.add_argument("--config", help="TOML config file (or ASSIST_DECIDER_CONFIG)")
    parser.add_argument("--host", help="bind address (default 127.0.0.1; 0.0.0.0 for the LAN)")
    parser.add_argument("--port", type=int, help="port (default 8765)")
    parser.add_argument("--model", choices=MODELS, help="decision model")
    parser.add_argument("--device", help="auto, cpu, cuda, cuda:N, mps or xpu")
    parser.add_argument("--log-level", help="DEBUG, INFO, WARNING or ERROR")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    try:
        settings = load_settings(
            args.config,
            host=args.host,
            port=args.port,
            model=args.model,
            device=args.device,
            log_level=args.log_level,
        )
        if args.command == "download":
            _setup_logging(settings, None)
            make_provider(settings.model, settings.device).load()
            return 0
    except ConfigError as err:
        print(f"assist-decider: {err}", file=sys.stderr)
        return 2

    import uvicorn

    from .app import HeaderTimeoutH11Protocol, create_app

    bus = EventBus(settings.log_buffer)
    _setup_logging(settings, bus)
    app = create_app(
        provider=make_provider(remembered_model(settings), settings.device),
        make=lambda model: make_provider(model, settings.device),
        on_switch=lambda model: remember_model(settings, model),
        bus=bus,
        max_pending=settings.max_pending,
        max_body_bytes=settings.max_body_bytes,
    )
    scheme = "https" if settings.tls_certfile else "http"
    logging.getLogger(__name__).info(
        "Assist Decider %s on %s://%s:%d (log UI at /)",
        __version__,
        scheme,
        settings.host,
        settings.port,
    )
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        timeout_keep_alive=5,
        http=HeaderTimeoutH11Protocol,
        ssl_certfile=settings.tls_certfile,
        ssl_keyfile=settings.tls_keyfile,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
