"""Entry-point layer — argument parsing and logging setup."""

from __future__ import annotations

import argparse
import logging
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Telegram Backup Sender Bot — watches a directory and uploads new files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        default=None,
        help="Path to a .env file (system env vars always override .env values).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )

    # Lazy imports so logging is configured before any module emits messages
    from .config import load_settings
    from .service import BackupSenderService

    try:
        settings = load_settings(env_file=args.env_file)
    except ValueError as exc:
        logging.critical("Configuration error: %s", exc)
        sys.exit(1)

    BackupSenderService(settings).run()


if __name__ == "__main__":
    main()
