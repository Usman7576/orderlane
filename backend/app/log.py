import logging
import re

import structlog

_PHONE = re.compile(r"\+?\d{7,15}")


def mask_phone(value: str) -> str:
    """Keep only the last 4 digits of a phone number, for logs."""
    return _PHONE.sub(lambda m: "*" * (len(m.group()) - 4) + m.group()[-4:], value)


def _mask_phones(
    _logger: object, _method: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    for key in ("phone", "wa_id", "from_"):
        if isinstance(event_dict.get(key), str):
            event_dict[key] = mask_phone(event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO", json: bool = False) -> None:
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _mask_phones,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
    )
