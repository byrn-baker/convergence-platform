"""Redact webhook tokens and URL credentials before they reach log output."""

import logging
import re

# Trailing characters that usually belong to the surrounding log text, not the secret.
_TAIL = ".,;:'\"”’)>]}"

_DISCORD_WEBHOOK = re.compile(
    r"(https?://(?:[^\s/@]+@)?"
    r"(?:(?:ptb|canary)\.)?"
    r"discord(?:app)?\.com(?::\d+)?"
    r"/api(?:/v\d+)?/webhooks/\d+/)"
    r"([^/\s?#\"'<>]+)",
    re.IGNORECASE,
)
_SLACK_WEBHOOK = re.compile(
    r"(https?://(?:[^\s/@]+@)?"
    r"hooks\.slack(?:-gov)?\.com(?::\d+)?/(?:services|triggers|workflows)/)"
    r"([^\s\"'<>]+)",
    re.IGNORECASE,
)
_USERINFO = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^\s/@]*@")
_QUERY_PARAM = re.compile(r"([?&#])([^=&#\s]+)=([^&#\s]*)")
# A parameter is secret when one of its name parts (split on _ - . and case changes) is one of these.
_SECRET_PARTS = {
    "token", "apitoken", "key", "apikey", "secret", "password", "passwd", "pwd",
    "signature", "sig", "auth", "credential", "credentials",
}


def _split_trailing(segment: str) -> tuple[str, str]:
    end = len(segment)
    while end > 0 and segment[end - 1] in _TAIL:
        end -= 1
    return segment[:end], segment[end:]


def _redact_tail(match: re.Match[str]) -> str:
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
    text = _DISCORD_WEBHOOK.sub(_redact_tail, text)
    text = _SLACK_WEBHOOK.sub(_redact_tail, text)
    text = _QUERY_PARAM.sub(_redact_query, text)
    return text


def _redact_arg(value):
    """Redact one format argument; keep its type unless it rendered a secret."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    clean = redact(text)
    return value if clean == text else clean


class RedactingFilter(logging.Filter):
    """Redact secrets on a log record. Always emits the record.

    Arguments are redacted one by one rather than merged into the message, so
    formatters that read ``record.args`` (uvicorn's access log) keep working.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(_redact_arg(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: _redact_arg(v) for k, v in record.args.items()}
            record.getMessage()
        except Exception:
            # The record cannot be formatted; logging would print msg and args raw to stderr.
            try:
                record.msg = redact(f"{record.msg!r} args={record.args!r}")
            except Exception:
                record.msg = "[unformattable log record]"
            record.args = None
        try:
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
        except Exception:
            record.exc_text = "[traceback unavailable]"
        try:
            if record.stack_info:
                record.stack_info = redact(record.stack_info)
        except Exception:
            record.stack_info = None
        return True


def install(logger: logging.Logger | None = None) -> None:
    """Attach one RedactingFilter to each handler of logger (default: root)."""
    target = logging.getLogger() if logger is None else logger
    for handler in target.handlers:
        if any(isinstance(existing, RedactingFilter) for existing in handler.filters):
            continue
        handler.addFilter(RedactingFilter())


def install_uvicorn() -> None:
    """Cover uvicorn's own loggers; uvicorn adds their handlers when it starts and they do not propagate."""
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        install(logging.getLogger(name))
