#!/usr/bin/env python3
"""Standalone helper — polls getUpdates and prints every chat_id found.

Usage:
    TELEGRAM_BOT_TOKEN=<token> python scripts/get_chat_id.py
    python scripts/get_chat_id.py --token <token>

Also honours SOCKS_PROXY for environments where direct outbound traffic is blocked.
This script has NO dependency on the tgbot_backup package.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import Any

try:
    import requests
except ImportError:
    print("ERROR: 'requests' is not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mask_proxy_url(url: str) -> str:
    return re.sub(r"(://)[^:@/]+:[^@/]+@", r"\1***:***@", url)


def _build_session(socks_proxy: str) -> requests.Session:
    session = requests.Session()
    if socks_proxy:
        try:
            import socks  # noqa: F401
        except ImportError:
            print(
                "ERROR: SOCKS_PROXY is set but PySocks is not installed.\n"
                "       Run: pip install 'requests[socks]'",
                file=sys.stderr,
            )
            sys.exit(1)
        session.proxies = {"http": socks_proxy, "https": socks_proxy}
        print(f"[proxy] Routing via: {_mask_proxy_url(socks_proxy)}")
    return session


def _get_updates(
    session: requests.Session,
    token: str,
    offset: int | None,
    timeout: int,
) -> list[dict[str, Any]]:
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    data: dict[str, Any] = {"timeout": timeout}
    if offset is not None:
        data["offset"] = offset
    try:
        resp = session.post(url, data=data, timeout=float(timeout) + 15)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        print(f"[error] getUpdates failed: {exc}", file=sys.stderr)
        return []
    if not body.get("ok"):
        print(f"[error] Telegram returned ok=false: {body.get('description')}", file=sys.stderr)
        return []
    return body.get("result", [])


def _extract_chats(update: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a list of chat dicts found anywhere in one update."""
    chats: list[dict[str, Any]] = []

    # message.chat
    chat = update.get("message", {}).get("chat")
    if chat:
        chats.append(chat)

    # channel_post.chat
    chat = update.get("channel_post", {}).get("chat")
    if chat:
        chats.append(chat)

    # callback_query.message.chat
    chat = (update.get("callback_query") or {}).get("message", {}).get("chat")
    if chat:
        chats.append(chat)

    # edited_message.chat
    chat = (update.get("edited_message") or {}).get("chat")
    if chat:
        chats.append(chat)

    return chats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Poll getUpdates and print every discovered chat_id.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        help="Bot token (falls back to TELEGRAM_BOT_TOKEN env var).",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=20,
        help="Long-poll timeout in seconds per request.",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Total seconds to keep polling before exiting.",
    )
    args = parser.parse_args()

    token = args.token.strip()
    if not token:
        print(
            "ERROR: No bot token provided.\n"
            "       Set TELEGRAM_BOT_TOKEN or pass --token <TOKEN>.",
            file=sys.stderr,
        )
        sys.exit(1)

    socks_proxy = os.environ.get("SOCKS_PROXY", "").strip()
    session = _build_session(socks_proxy)

    seen: dict[int, dict[str, Any]] = {}  # chat_id -> chat info
    offset: int | None = None
    deadline = time.monotonic() + args.duration

    print(
        f"Polling for {args.duration}s (poll_timeout={args.poll_timeout}s). "
        "Send a message to your bot in any target chat now …\n"
    )

    while time.monotonic() < deadline:
        updates = _get_updates(session, token, offset, args.poll_timeout)
        for update in updates:
            update_id: int = update["update_id"]
            offset = update_id + 1

            for chat in _extract_chats(update):
                cid: int = chat["id"]
                if cid not in seen:
                    seen[cid] = chat
                    title = chat.get("title") or chat.get("username") or chat.get("first_name", "?")
                    chat_type = chat.get("type", "?")
                    print(f"  Discovered chat  id={cid}  type={chat_type}  name={title!r}")

    if not seen:
        print("\nNo chats discovered. Make sure you have sent at least one message to the bot.")
    else:
        print("\n--- Summary ---")
        print("Add these to TELEGRAM_TARGET_CHAT_IDS in your .env (comma-separated):\n")
        print(",".join(str(cid) for cid in seen))

    print("\nDone.")


if __name__ == "__main__":
    main()
