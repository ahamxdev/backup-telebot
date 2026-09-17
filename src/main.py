import asyncio

from app_logging.setup import configure_logging
from bootstrap.app import run_bot
from config.settings import get_settings


def entrypoint() -> None:
    settings = get_settings()
    configure_logging(settings)
    asyncio.run(run_bot(settings))


if __name__ == "__main__":
    entrypoint()
