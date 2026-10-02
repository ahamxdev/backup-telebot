from app_logging.setup import redact_secrets


def test_secret_redaction() -> None:
    result = redact_secrets(
        None,
        "info",
        {"event": "x", "bot_token": "secret", "api_hash": "secret", "safe": "ok"},
    )
    assert result["bot_token"] == "[REDACTED]"
    assert result["api_hash"] == "[REDACTED]"
    assert result["safe"] == "ok"


def test_nested_secret_keys_and_private_key_text_are_redacted() -> None:
    result = redact_secrets(
        None,
        "error",
        {
            "details": {"private_key": "sensitive", "encryption_key": "sensitive"},
            "items": [{"password": "sensitive"}],
            "message": (
                "failed: -----BEGIN OPENSSH PRIVATE KEY-----\nsensitive\n"
                "-----END OPENSSH PRIVATE KEY-----"
            ),
        },
    )
    assert "sensitive" not in str(result)


def test_structured_traceback_contains_locations_but_no_exception_values() -> None:
    import sys

    from app_logging.setup import safe_exception_info

    try:
        raise RuntimeError("confidential-password")
    except RuntimeError:
        event = safe_exception_info(None, "error", {"event": "failed", "exc_info": sys.exc_info()})
    assert "RuntimeError" in event["exception"]
    assert "test_redaction.py" in event["exception"]
    assert "confidential-password" not in str(event)


def test_library_traceback_does_not_render_exception_messages() -> None:
    import logging
    import sys

    from app_logging.setup import SafeFormatter

    try:
        raise RuntimeError("confidential-password")
    except RuntimeError:
        record = logging.LogRecord(
            "telethon", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )
    rendered = SafeFormatter().format(record)
    assert "RuntimeError" in rendered and "confidential-password" not in rendered
