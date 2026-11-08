"""Redact webhook tokens and URL credentials before they reach log output."""

import logging
import re

# Trailing characters that usually belong to the surrounding log text, not the secret.
_TAIL = ".,;:'\"”’)>]}"

_DISCORD_WEBHOOK = re.compile(
    r"(https?://(?:[^\s/@]+@)?"
    r"(?:(?:ptb|canary)\.)?"
    r"discord(?:app)?\.com"
    r"/api(?:/v\d+)?/webhooks/\d+/)"
    r"([^/\s?#]+)",
    re.IGNORECASE,
)
_SLACK_WEBHOOK = re.compile(
    r"(https?://(?:[^\s/@]+@)?"
    r"hooks\.slack\.com/(?:services|triggers|workflows)/)"
    r"(\S+)",
    re.IGNORECASE,
)
_USERINFO = re.compile(
    r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^\s/@:]+:[^\s/@]+@",
    re.IGNORECASE,
)
_QUERY_PARAM = re.compile(r"([?&])([^=&#\s]+)=([^&#\s]*)")
# A parameter is secret when one of its name parts (split on _ - . and case changes) is one of these.
_SECRET_PARTS = {"token", "key", "apikey", "secret", "password", "passwd", "signature", "sig"}


def _split_trailing(segment: str) -> tuple[str, str]:
    end = len(segment)
    while end > 0 and segment[end - 1] in _TAIL:
        end -= 1
    return segment[:end], segment[end:]


def _redact_discord(match: re.Match[str]) -> str:
    body, tail = _split_trailing(match.group(2))
    if not body:
        return match.group(0)
    return match.group(1) + "[redacted]" + tail


def _redact_slack(match: re.Match[str]) -> str:
    body, tail = _split_trailing(match.group(2))
    if not body:
        return match.group(0)
    return match.group(1) + "[redacted]" + tail


def _secret_name(name: str) -> bool:
    parts = re.split(r"[_.\-]+|(?<=[a-z0-9])(?=[A-Z])", name)
    return any(part.lower() in _SECRET_PARTS for part in parts)


def _redact_query(match: re.Match[str]) -> str:
    if not _secret_name(match.group(2)):
        return match.group(0)
    _body, tail = _split_trailing(match.group(3))
    return f"{match.group(1)}{match.group(2)}=[redacted]{tail}"


def redact(text: str) -> str:
    """Replace webhook tokens, URL userinfo, and sensitive query values."""
    text = _USERINFO.sub(r"\1[redacted]@", text)
    text = _DISCORD_WEBHOOK.sub(_redact_discord, text)
    text = _SLACK_WEBHOOK.sub(_redact_slack, text)
    text = _QUERY_PARAM.sub(_redact_query, text)
    return text


class RedactingFilter(logging.Filter):
    """Redact secrets on a log record. Always emits the record."""

    def filter(self, record: logging.LogRecord) -> bool:
        saved = (record.msg, record.args, record.exc_text, record.stack_info)
        try:
            record.msg = redact(record.getMessage())
            record.args = None
            if record.exc_info and not record.exc_text:
                rendered = logging.Formatter().formatException(record.exc_info)
                record.exc_text = redact(rendered)
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
        except Exception:
            record.msg, record.args, record.exc_text, record.stack_info = saved
        return True


def install(logger: logging.Logger | None = None) -> None:
    """Attach one RedactingFilter to each handler of logger (default: root)."""
    target = logging.getLogger() if logger is None else logger
    for handler in target.handlers:
        if any(isinstance(existing, RedactingFilter) for existing in handler.filters):
            continue
        handler.addFilter(RedactingFilter())
