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
