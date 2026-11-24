"""Battery size from Base's own numbers, for hardware the SKU tables don't know.

Base's "backup hours at 750 W" estimate is the usable energy left divided by
0.75 kW. Dividing out the charge level gives the battery's usable size. On a
25 kWh GRO_SINGLE that comes to 22.5 kWh, so Base treats about 90% of the
nameplate as usable. Known sizes are at least 20% apart, so the reading is
divided by that fraction and matched to the nearest one.

Only one battery confirms the 90% so far. A reading that isn't close to a
known size is shown but not used, and capacity falls back to the saved
option.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from typing import Any

from .const import (
    BACKUP_RESERVE_PERCENT,
    BATTERY_CAPACITY_OPTIONS,
    ESTIMATE_LOAD_KW,
    ESTIMATE_MIN_SAMPLES,
    ESTIMATE_MIN_SOC_PERCENT,
    ESTIMATE_SAMPLES,
    ESTIMATE_SNAP_TOLERANCE,
    ESTIMATE_USABLE_FRACTION,
)

# Every size Base sells, as (units, nameplate kWh)
KNOWN_SYSTEMS: tuple[tuple[int, float], ...] = tuple(sorted(set(BATTERY_CAPACITY_OPTIONS.values())))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def estimate_battery_percent(backup_750w: Any, capacity_kwh: Any) -> float | None:
    """Battery percent from Base's 750 W backup time and the nameplate size.

    hours x 0.75 kW is the energy left above Base's 10% reserve. None when
    either number is missing. Kept to 0-100, so a wrong capacity setting
    cannot show more than full.
    """
    hours = _number(backup_750w)
    capacity = _number(capacity_kwh)
    if hours is None or capacity is None or hours < 0 or capacity <= 0:
        return None
    percent = BACKUP_RESERVE_PERCENT + hours * ESTIMATE_LOAD_KW / capacity * 100
    return round(min(100.0, max(0.0, percent)), 1)


def nearest_system(
    nameplate_kwh: float, units: int | None = None, tolerance: float = ESTIMATE_SNAP_TOLERANCE
) -> tuple[int, float] | None:
    """The known size within ``tolerance`` of ``nameplate_kwh``, or None.

    With ``units`` known, only sizes with that many units count.
    """
    candidates = [s for s in KNOWN_SYSTEMS if units is None or s[0] == units]
    if not candidates or nameplate_kwh <= 0:
        return None
    best = min(candidates, key=lambda s: abs(nameplate_kwh - s[1]) / s[1])
    if abs(nameplate_kwh - best[1]) / best[1] > tolerance:
        return None
    return best


class CapacityEstimate:
    """Recent usable-size readings from the 750 W backup estimate."""

    def __init__(self) -> None:
        self._readings: deque[float] = deque(maxlen=ESTIMATE_SAMPLES)

    def add(self, soc_percent: Any, hours_750w: Any) -> bool:
        """Record one reading. Skipped below the minimum charge or when a value is missing."""
        soc = _number(soc_percent)
        hours = _number(hours_750w)
        if soc is None or hours is None or soc < ESTIMATE_MIN_SOC_PERCENT or soc > 100 or hours <= 0:
            return False
        self._readings.append(hours * ESTIMATE_LOAD_KW / (soc / 100))
        return True

    @property
    def samples(self) -> int:
        return len(self._readings)

    @property
    def usable_kwh(self) -> float | None:
        """Median usable size, or None until there are enough readings."""
        if len(self._readings) < ESTIMATE_MIN_SAMPLES:
            return None
        return round(statistics.median(self._readings), 2)

    def system(self, units: int | None = None) -> tuple[int, float] | None:
        """The known size the readings point to, or None when none is close."""
        usable = self.usable_kwh
        if usable is None:
            return None
        return nearest_system(usable / ESTIMATE_USABLE_FRACTION, units)

    def as_diagnostics(self) -> dict[str, Any]:
        readings = list(self._readings)
        return {
            "samples": len(readings),
            "usable_kwh": self.usable_kwh,
            "min_kwh": round(min(readings), 2) if readings else None,
            "max_kwh": round(max(readings), 2) if readings else None,
        }
