"""Small hand-rolled unit-conversion table for CalculatorHandler.

`pint` would be overkill and pulls a real dep tree. The voice handler
only needs the handful of household measurements people actually ask
about, so a flat dict + a 6-line conversion function does the job.

Conversion model: each unit has a `family` (weight/distance/volume/
temperature/time) and a `factor` that scales it to the family's
canonical base. Convert by going through the base:

    result = value * src.factor / dst.factor

Temperature is the one exception — F↔C is affine, not linear, so it
gets its own branch in `convert()`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Unit:
    family: str
    factor: float  # value-in-this-unit * factor = value-in-family-base


# Canonical bases per family:
#   weight = grams; distance = meters; volume = milliliters;
#   temperature = Celsius (with affine F<->C special-cased);
#   time = seconds.
UNITS: dict[str, Unit] = {
    # Weight (base: g).
    "g": Unit("weight", 1.0),
    "gram": Unit("weight", 1.0),
    "kg": Unit("weight", 1000.0),
    "kilogram": Unit("weight", 1000.0),
    "oz": Unit("weight", 28.349523125),
    "ounce": Unit("weight", 28.349523125),
    "lb": Unit("weight", 453.59237),
    "pound": Unit("weight", 453.59237),
    # Distance (base: m).
    "mm": Unit("distance", 0.001),
    "millimeter": Unit("distance", 0.001),
    "cm": Unit("distance", 0.01),
    "centimeter": Unit("distance", 0.01),
    "m": Unit("distance", 1.0),
    "meter": Unit("distance", 1.0),
    "metre": Unit("distance", 1.0),
    "km": Unit("distance", 1000.0),
    "kilometer": Unit("distance", 1000.0),
    "in": Unit("distance", 0.0254),
    "inch": Unit("distance", 0.0254),
    "ft": Unit("distance", 0.3048),
    "foot": Unit("distance", 0.3048),
    "feet": Unit("distance", 0.3048),
    "yd": Unit("distance", 0.9144),
    "yard": Unit("distance", 0.9144),
    "mi": Unit("distance", 1609.344),
    "mile": Unit("distance", 1609.344),
    # Volume (base: mL, US customary).
    "ml": Unit("volume", 1.0),
    "milliliter": Unit("volume", 1.0),
    "l": Unit("volume", 1000.0),
    "liter": Unit("volume", 1000.0),
    "litre": Unit("volume", 1000.0),
    "tsp": Unit("volume", 4.92892),
    "teaspoon": Unit("volume", 4.92892),
    "tbsp": Unit("volume", 14.7868),
    "tablespoon": Unit("volume", 14.7868),
    "cup": Unit("volume", 236.588),
    "pint": Unit("volume", 473.176),
    "pt": Unit("volume", 473.176),
    "qt": Unit("volume", 946.353),
    "quart": Unit("volume", 946.353),
    "gal": Unit("volume", 3785.41),
    "gallon": Unit("volume", 3785.41),
    # Temperature (factor ignored — convert() special-cases this family).
    "c": Unit("temperature", 1.0),
    "celsius": Unit("temperature", 1.0),
    "f": Unit("temperature", 1.0),
    "fahrenheit": Unit("temperature", 1.0),
    "k": Unit("temperature", 1.0),
    "kelvin": Unit("temperature", 1.0),
    # Time (base: seconds).
    "sec": Unit("time", 1.0),
    "second": Unit("time", 1.0),
    "min": Unit("time", 60.0),
    "minute": Unit("time", 60.0),
    "hr": Unit("time", 3600.0),
    "hour": Unit("time", 3600.0),
    "day": Unit("time", 86400.0),
    "week": Unit("time", 604800.0),
}


def _canonicalize(name: str) -> str:
    """Lowercase, strip a single trailing 's' for plurals. We avoid the
    naive everything-ending-in-s strip — "celsius" must NOT become
    "celsiu" — by checking explicit plural forms first."""
    n = name.strip().lower()
    if n in UNITS:
        return n
    # Common irregular plurals (feet/inches/etc handled by being in UNITS).
    if n.endswith("es") and n[:-2] in UNITS:
        return n[:-2]
    if n.endswith("s") and n[:-1] in UNITS:
        return n[:-1]
    return n


def lookup(name: str) -> Unit | None:
    """Resolve a spoken unit name to its Unit entry, or None if unknown."""
    return UNITS.get(_canonicalize(name))


def _temp_to_celsius(value: float, unit: str) -> float:
    if unit in ("f", "fahrenheit"):
        return (value - 32.0) * 5.0 / 9.0
    if unit in ("k", "kelvin"):
        return value - 273.15
    return value  # already celsius


def _celsius_to(value_c: float, unit: str) -> float:
    if unit in ("f", "fahrenheit"):
        return value_c * 9.0 / 5.0 + 32.0
    if unit in ("k", "kelvin"):
        return value_c + 273.15
    return value_c


def convert(value: float, src_name: str, dst_name: str) -> float:
    """Convert ``value`` from ``src_name`` to ``dst_name``.

    Raises ``ValueError`` for unknown units or cross-family attempts
    (e.g., grams → meters). Temperature uses the affine F/C/K mapping;
    everything else is a simple factor-through-base.
    """
    src = lookup(src_name)
    dst = lookup(dst_name)
    if src is None:
        raise ValueError(f"unknown unit: {src_name}")
    if dst is None:
        raise ValueError(f"unknown unit: {dst_name}")
    if src.family != dst.family:
        raise ValueError(
            f"can't convert {src.family} to {dst.family}"
        )
    if src.family == "temperature":
        return _celsius_to(_temp_to_celsius(value, _canonicalize(src_name)),
                           _canonicalize(dst_name))
    return value * src.factor / dst.factor
