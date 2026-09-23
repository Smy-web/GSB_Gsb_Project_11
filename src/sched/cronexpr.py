"""Cron expression parsing and validation.

Supports standard 5-field cron: minute hour day-of-month month day-of-week.
Each field supports: ``*``, lists (``,``), ranges (``-``), steps (``/``),
and English name abbreviations for months (JAN-DEC) and weekdays (SUN-SAT).
Aliases: @hourly @daily @midnight @weekly @monthly @yearly @annually.

All validation failures raise :class:`CronError`, which carries the field
index, the offending token and a human readable reason.  No raw exceptions
leak to callers.
"""
from __future__ import annotations

from dataclasses import dataclass

FIELD_NAMES = ("minute", "hour", "day_of_month", "month", "day_of_week")
FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))

MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}

ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}


class CronError(ValueError):
    """Validation failure with precise location information."""

    def __init__(self, field_index: int, token: str, reason: str):
        self.field_index = field_index  # 0-based; -1 means whole-expression error
        self.token = token
        self.reason = reason
        if field_index >= 0:
            msg = (
                f"field {field_index + 1} ({FIELD_NAMES[field_index]}): "
                f"invalid token {token!r}: {reason}"
            )
        else:
            msg = f"invalid expression: {reason}"
        super().__init__(msg)


@dataclass(frozen=True)
class CronSpec:
    minutes: frozenset
    hours: frozenset
    dom: frozenset
    months: frozenset
    dow: frozenset
    dom_any: bool
    dow_any: bool
    source: str


def _name_to_value(text: str, names: dict) -> int | None:
    return names.get(text.lower())


def _parse_field(text: str, field_index: int) -> tuple[frozenset, bool]:
    lo, hi = FIELD_RANGES[field_index]
    names = MONTH_NAMES if field_index == 3 else DOW_NAMES if field_index == 4 else {}
    values: set[int] = set()
    if text == "":
        raise CronError(field_index, text, "empty field")
    for part in text.split(","):
        if part == "":
            raise CronError(field_index, part, "empty list item")
        values |= _parse_part(part, field_index, lo, hi, names)
    # Normalise day-of-week: 7 is an alias for Sunday (0).
    if field_index == 4 and 7 in values:
        values.discard(7)
        values.add(0)
    is_any = values == set(range(lo, hi + 1)) or (
        field_index == 4 and values == set(range(0, 7))
    )
    return frozenset(values), is_any


def _atom(token: str, field_index: int, names: dict) -> int:
    named = _name_to_value(token, names)
    if named is not None:
        return named
    try:
        return int(token)
    except ValueError:
        raise CronError(field_index, token, "not a number or known name") from None


def _parse_part(part: str, field_index: int, lo: int, hi: int, names: dict) -> set[int]:
    if part.count("/") > 1:
        raise CronError(field_index, part, "too many '/' separators")
    base, _, step_text = part.partition("/")
    if step_text:
        try:
            step = int(step_text)
        except ValueError:
            raise CronError(
                field_index, part, f"step {step_text!r} is not an integer"
            ) from None
        if step < 1:
            raise CronError(field_index, part, f"step must be >= 1, got {step}")
    else:
        step = 1

    if base == "" or base == "*":
        if base == "" and step_text:
            raise CronError(field_index, part, "missing range before '/'")
        start, end = lo, hi
    elif "-" in base:
        if base.count("-") > 1:
            raise CronError(field_index, part, "too many '-' separators")
        a_text, b_text = base.split("-", 1)
        start = _atom(a_text, field_index, names)
        end = _atom(b_text, field_index, names)
        if start > end:
            raise CronError(
                field_index, part, f"range start {start} > range end {end}"
            )
    else:
        start = _atom(base, field_index, names)
        # "a/n" means a-max with step n (standard cron); bare "a" is just a.
        end = hi if step_text else start

    lo_b, hi_b = FIELD_RANGES[field_index]
    if not (lo_b <= start <= hi_b):
        raise CronError(
            field_index, part, f"value {start} out of range [{lo_b}, {hi_b}]"
        )
    if not (lo_b <= end <= hi_b):
        raise CronError(
            field_index, part, f"value {end} out of range [{lo_b}, {hi_b}]"
        )
    return set(range(start, end + 1, step))


def parse(expr: str) -> CronSpec:
    """Parse a cron expression into a :class:`CronSpec`.

    Raises :class:`CronError` on any validation failure.
    """
    if not isinstance(expr, str):
        raise CronError(-1, str(expr), "expression must be a string")
    text = expr.strip()
    if not text:
        raise CronError(-1, expr, "empty expression")
    lowered = text.lower()
    if lowered.startswith("@"):
        if lowered not in ALIASES:
            raise CronError(-1, text, f"unknown alias {text!r}")
        text = ALIASES[lowered]
    fields = text.split()
    if len(fields) != 5:
        raise CronError(
            -1, expr, f"expected 5 fields, got {len(fields)}"
        )
    parsed = []
    any_flags = []
    for i, field_text in enumerate(fields):
        values, is_any = _parse_field(field_text, i)
        parsed.append(values)
        any_flags.append(is_any)
    return CronSpec(
        minutes=parsed[0],
        hours=parsed[1],
        dom=parsed[2],
        months=parsed[3],
        dow=parsed[4],
        dom_any=any_flags[2],
        dow_any=any_flags[4],
        source=expr,
    )
