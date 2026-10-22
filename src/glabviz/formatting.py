from __future__ import annotations

import html
import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from glabviz.metrics import FailureRate

MISSING = "No data"

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")
_EXTENSIONS = (".png", ".svg")


def format_seconds(value: float | None, *, missing: str = MISSING) -> str:
    if value is None:
        return missing
    total = int(round(value))
    sign = "-" if total < 0 else ""
    total = abs(total)
    days, rest = divmod(total, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes, seconds = divmod(rest, 60)
    if days:
        return f"{sign}{days}d {hours:02d}:{minutes:02d}"
    if hours:
        return f"{sign}{hours}h {minutes:02d}m"
    if minutes:
        return f"{sign}{minutes}m {seconds:02d}s"
    return f"{sign}{seconds}s"


def format_signed_seconds(value: float | None) -> str:
    if value is None:
        return MISSING
    sign = "+" if value > 0 else ""
    return f"{sign}{format_seconds(value)}"


def format_percent(value: float | None, *, digits: int = 1) -> str:
    if value is None:
        return MISSING
    return f"{value * 100:.{digits}f}%"


def format_rate(rate: FailureRate) -> str:
    if rate.value is None:
        return MISSING
    return f"{format_percent(rate.value)} ({rate.red}/{rate.denominator})"


def format_percent_points(value: float | None, *, digits: int = 1) -> str:
    if value is None:
        return MISSING
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{digits}f} pp"


def format_datetime(value: datetime | None, zone: ZoneInfo) -> str:
    if value is None:
        return MISSING
    return value.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def safe_filename(value: str, fallback: str = "glabviz") -> str:
    text = value.strip()
    lowered = text.lower()
    for extension in _EXTENSIONS:
        if lowered.endswith(extension):
            text = text[: -len(extension)]
            break
    text = _UNSAFE_FILENAME.sub("-", text).strip(" .-_")
    text = re.sub(r"-{2,}", "-", text)
    return text[:120] or fallback


def escape_text(value: str) -> str:
    return html.escape(value, quote=True)


def nice_ticks(max_value: float, *, target: int = 6) -> tuple[list[float], list[str]]:
    if max_value <= 0:
        return [0.0], ["0s"]
    raw_step = max(max_value / target, 1.0)
    magnitude = 10 ** math.floor(math.log10(raw_step))
    for factor in (1, 2, 5, 10):
        step = factor * magnitude
        if step >= raw_step:
            break
    count = math.ceil(max_value / step)
    values = [index * step for index in range(count + 1)]
    return values, [format_seconds(value) for value in values]
