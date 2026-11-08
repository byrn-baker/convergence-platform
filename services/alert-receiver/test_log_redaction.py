"""Log redaction keeps webhook tokens and URL credentials out of handler output."""

import io
import logging

import log_redaction
from log_redaction import RedactingFilter, install, redact

_DISCORD = "https://discord.com/api/webhooks/123456789012345678/SuperSecretTokenValue"
_PLAIN = "http://192.0.2.10:30081/api/events/x?site=home"


def _logger(name: str) -> tuple[logging.Logger, io.StringIO]:
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    log.propagate = False
    log.handlers.clear()
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    install(log)
    return log, buf


def test_discord_webhook_keeps_id_and_redacts_token():
    forms = [
        _DISCORD,
        "https://discordapp.com/api/webhooks/42/appTokenValue",
        "https://ptb.discord.com/api/webhooks/42/ptbTokenValue",
        "https://canary.discord.com/api/webhooks/42/canaryTokenValue",
        "https://ptb.discordapp.com/api/webhooks/42/ptbAppTokenValue",
        "https://canary.discordapp.com/api/v10/webhooks/42/versionTokenValue",
        "http://discord.com/api/v9/webhooks/42/httpTokenValue",
    ]
    secrets = [
        "SuperSecretTokenValue",
        "appTokenValue",
        "ptbTokenValue",
        "canaryTokenValue",
        "ptbAppTokenValue",
        "versionTokenValue",
        "httpTokenValue",
    ]
    rendered = redact(" | ".join(forms))
    for secret in secrets:
        assert secret not in rendered
    assert "https://discord.com/api/webhooks/123456789012345678/[redacted]" in rendered
    assert "https://discordapp.com/api/webhooks/42/[redacted]" in rendered
    assert "https://ptb.discord.com/api/webhooks/42/[redacted]" in rendered
    assert "https://canary.discord.com/api/webhooks/42/[redacted]" in rendered
    assert "https://ptb.discordapp.com/api/webhooks/42/[redacted]" in rendered
    assert "https://canary.discordapp.com/api/v10/webhooks/42/[redacted]" in rendered
    assert "http://discord.com/api/v9/webhooks/42/[redacted]" in rendered


def test_slack_webhook_keeps_prefix_and_redacts_the_rest():
    services = "https://hooks.slack.com/services/T000/B000/slackSecretValue"
    triggers = "https://hooks.slack.com/triggers/T000/B000/triggerSecretValue"
    workflows = "https://hooks.slack.com/workflows/T000/flowSecretValue"
    rendered = redact(" ".join((services, triggers, workflows)))
    assert "slackSecretValue" not in rendered
    assert "triggerSecretValue" not in rendered
    assert "flowSecretValue" not in rendered
    assert "https://hooks.slack.com/services/[redacted]" in rendered
    assert "https://hooks.slack.com/triggers/[redacted]" in rendered
    assert "https://hooks.slack.com/workflows/[redacted]" in rendered


def test_url_userinfo_is_redacted():
    rendered = redact("connect https://user:pass@example.com/hook")
    assert "user:pass" not in rendered
    assert rendered == "connect https://[redacted]@example.com/hook"


def test_sensitive_query_values_are_redacted_and_other_params_stay():
    raw = (
        "http://example.com/cb?site=home&token=tok&API_KEY=k&client_secret=s"
        "&db_password=p&signature=sg&sig=g&name=kept"
    )
    rendered = redact(raw)
    assert rendered == (
        "http://example.com/cb?site=home&token=[redacted]&API_KEY=[redacted]"
        "&client_secret=[redacted]&db_password=[redacted]&signature=[redacted]"
        "&sig=[redacted]&name=kept"
    )


def test_non_secret_url_is_unchanged():
    assert redact(_PLAIN) == _PLAIN
    mixed = f"ok {_PLAIN} bad {_DISCORD}"
    rendered = redact(mixed)
    assert _PLAIN in rendered
    assert "SuperSecretTokenValue" not in rendered


def test_percent_style_args_are_redacted():
    log, buf = _logger("test-log-redaction-args")
    log.info("HTTP Request: POST %s \"HTTP/1.1 204 No Content\"", _DISCORD)
    output = buf.getvalue()
    assert "SuperSecretTokenValue" not in output
    assert "https://discord.com/api/webhooks/123456789012345678/[redacted]" in output
    assert "204 No Content" in output


def test_exception_traceback_redacts_webhook_url():
    log, buf = _logger("test-log-redaction-exc")
    try:
        raise RuntimeError(f"Discord post failed: {_DISCORD}")
    except RuntimeError:
        log.exception("delivery failed")
    output = buf.getvalue()
    assert "SuperSecretTokenValue" not in output
    assert "https://discord.com/api/webhooks/123456789012345678/[redacted]" in output
    assert "RuntimeError" in output


def test_install_is_idempotent():
    log, _buf = _logger("test-log-redaction-install")
    handler = log.handlers[0]
    install(log)
    install(log)
    redactors = [item for item in handler.filters if isinstance(item, RedactingFilter)]
    assert len(redactors) == 1


def test_getmessage_failure_does_not_break_logging():
    class Boom:
        def __str__(self):
            raise RuntimeError("format failed")

    log, buf = _logger("test-log-redaction-boom")
    record = log.makeRecord(log.name, logging.INFO, __file__, 1, "bad %s", (Boom(),), None)
    assert RedactingFilter().filter(record) is True
    assert record.msg == "bad %s"
    log.info("bad %s", Boom())
    log.info("after ok")
    assert "after ok" in buf.getvalue()


def test_stack_info_is_redacted():
    log, _buf = _logger("test-log-redaction-stack")
    record = log.makeRecord(log.name, logging.INFO, __file__, 1, "hello", (), None)
    record.stack_info = f"stack {_DISCORD}"
    assert RedactingFilter().filter(record) is True
    assert record.stack_info is not None
    assert "SuperSecretTokenValue" not in record.stack_info
    assert "[redacted]" in record.stack_info


def test_query_names_match_by_part_not_substring():
    url = ("https://example.test/x?access_token=a&X-Amz-Signature=b&apiKey=c&api_key=d"
           "&design=keep&monkey=keep&signal=keep&keyword=keep&site=home")
    assert redact(url) == (
        "https://example.test/x?access_token=[redacted]&X-Amz-Signature=[redacted]"
        "&apiKey=[redacted]&api_key=[redacted]"
        "&design=keep&monkey=keep&signal=keep&keyword=keep&site=home"
    )
