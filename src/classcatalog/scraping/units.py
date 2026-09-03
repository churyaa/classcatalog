from __future__ import annotations

import re
from dataclasses import dataclass

_UNITS_RE = re.compile(
    r"^\s*(?P<minimum>\d+(?:\.\d+)?)\s*(?:-\s*(?P<maximum>\d+(?:\.\d+)?))?\s*$"
)


@dataclass(frozen=True, slots=True)
class ParsedUnits:
    """Normalized fixed- or variable-unit value from PeopleSoft."""

    units: float | None
    units_min: float
    units_max: float
    units_text: str


def parse_units_text(value: str) -> ParsedUnits:
    """Parse values such as ``3.00`` and ``1.00 - 3.00`` without losing range data."""

    text = " ".join(value.split())
    match = _UNITS_RE.fullmatch(text)
    if match is None:
        raise ValueError(f"Invalid units value: {value!r}")

    minimum = float(match.group("minimum"))
    maximum_text = match.group("maximum")
    maximum = float(maximum_text) if maximum_text is not None else minimum
    if maximum < minimum:
        raise ValueError(f"Invalid units range: {value!r}")

    units = minimum if maximum_text is None or minimum == maximum else None
    return ParsedUnits(
        units=units,
        units_min=minimum,
        units_max=maximum,
        units_text=text,
    )
