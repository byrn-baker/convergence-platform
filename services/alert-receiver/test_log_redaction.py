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
    assert record.args is None and record.msg.startswith("'bad %s' args=")
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


def test_unformattable_record_does_not_leak_args_to_stderr(capsys):
    log, buf = _logger("test-log-redaction-badargs")
    log.info("post %s %s", _DISCORD)  # one argument short: formatting fails
    out = buf.getvalue() + capsys.readouterr().err
    assert "SuperSecretTokenValue" not in out
    assert "[redacted]" in out


def test_more_credential_forms():
    assert redact("https://ghp_abc@github.com/x") == "https://[redacted]@github.com/x"
    assert redact("https://:pw@host/") == "https://[redacted]@host/"
    assert redact("https://discord.com:443/api/webhooks/1/TOK") == "https://discord.com:443/api/webhooks/1/[redacted]"
    assert redact("https://hooks.slack-gov.com/services/T/B/X") == "https://hooks.slack-gov.com/services/[redacted]"
    assert redact("https://h/cb#access_token=abc&state=ok") == "https://h/cb#access_token=[redacted]&state=ok"
    assert redact("https://h/?APIToken=a&auth=b&pwd=c&X-Amz-Credential=d") == (
        "https://h/?APIToken=[redacted]&auth=[redacted]&pwd=[redacted]&X-Amz-Credential=[redacted]")
    assert redact('{"url":"https://hooks.slack.com/services/T/B/X","ok":true}') == (
        '{"url":"https://hooks.slack.com/services/[redacted]","ok":true}')


def test_prefilled_exc_text_is_redacted():
    log, _buf = _logger("test-log-redaction-exctext")
    try:
        raise RuntimeError(_DISCORD)
    except RuntimeError:
        import sys
        record = log.makeRecord(log.name, logging.ERROR, __file__, 1, "x", (), sys.exc_info())
    record.exc_text = logging.Formatter().formatException(record.exc_info)
    RedactingFilter().filter(record)
    assert "SuperSecretTokenValue" not in record.exc_text


def test_args_keep_their_types_for_formatters():
    log, buf = _logger("test-log-redaction-types")
    log.info("%s %d %.1f", "ok", 7, 1.5)
    assert buf.getvalue().strip() == "ok 7 1.5"


def test_uvicorn_access_formatter_still_works():
    from uvicorn.logging import AccessFormatter
    log = logging.getLogger("test-log-redaction-access")
    log.setLevel(logging.INFO)
    log.propagate = False
    log.handlers.clear()
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False))
    log.addHandler(handler)
    install(log)
    log.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/cb?token=abc&site=home", "1.1", 200)
    out = buf.getvalue()
    assert "GET /cb?token=[redacted]&site=home" in out and "200" in out and "abc" not in out
