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
    RESERVE_AVERAGE_WINDOW,
    RESERVE_MAX_PERCENT,
    RESERVE_MAX_SOC_PERCENT,
    RESERVE_MIN_PERCENT,
    RESERVE_MIN_SAMPLES,
    RESERVE_MIN_SOC_PERCENT,
)

# Every size Base sells, as (units, nameplate kWh)
KNOWN_SYSTEMS: tuple[tuple[int, float], ...] = tuple(sorted(set(BATTERY_CAPACITY_OPTIONS.values())))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def estimate_battery_percent(
    backup_750w: Any, capacity_kwh: Any, reserve_percent: float = BACKUP_RESERVE_PERCENT
) -> float | None:
    """Battery percent from Base's 750 W backup time and the nameplate size.

    hours x 0.75 kW is the energy left above Base's reserve. None when
    either number is missing. Kept to 0-100, so a wrong capacity setting
    cannot show more than full.
    """
    hours = _number(backup_750w)
    capacity = _number(capacity_kwh)
    if hours is None or capacity is None or hours < 0 or capacity <= 0:
        return None
    percent = reserve_percent + hours * ESTIMATE_LOAD_KW / capacity * 100
    return round(min(100.0, max(0.0, percent)), 1)


class ReserveEstimate:
    """The battery reserve for one install, learned while off grid.

    Off grid, Base sends the real percent next to the 750 W backup time,
    so reserve = percent - hours x 0.75 / capacity x 100. Readings are
    averaged and saved. Until there are enough, the default is used. A
    change of capacity starts over, since the readings depend on it.
    """

    def __init__(
        self, percent: float | None = None, samples: int = 0, capacity_kwh: float | None = None
    ) -> None:
        self.percent = percent
        self.samples = samples
        self.capacity_kwh = capacity_kwh

    @property
    def value(self) -> float:
        """The learned reserve, or the default until it has enough readings."""
        if self.percent is None or self.samples < RESERVE_MIN_SAMPLES:
            return BACKUP_RESERVE_PERCENT
        return round(self.percent, 2)

    @property
    def learned(self) -> bool:
        return self.percent is not None and self.samples >= RESERVE_MIN_SAMPLES

    def add(self, soc_percent: Any, hours_750w: Any, capacity_kwh: Any) -> bool:
        """Record one off-grid reading. False when it was skipped."""
        soc = _number(soc_percent)
        hours = _number(hours_750w)
        capacity = _number(capacity_kwh)
        if soc is None or hours is None or capacity is None or hours < 0 or capacity <= 0:
            return False
        if not RESERVE_MIN_SOC_PERCENT <= soc <= RESERVE_MAX_SOC_PERCENT:
            return False
        reserve = soc - hours * ESTIMATE_LOAD_KW / capacity * 100
        if not RESERVE_MIN_PERCENT <= reserve <= RESERVE_MAX_PERCENT:
            return False
        if self.capacity_kwh != capacity:
            self.percent, self.samples, self.capacity_kwh = None, 0, capacity
        self.samples += 1
        if self.percent is None:
            self.percent = reserve
        else:
            self.percent += (reserve - self.percent) / min(self.samples, RESERVE_AVERAGE_WINDOW)
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "percent": self.percent,
            "samples": self.samples,
            "capacity_kwh": self.capacity_kwh,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ReserveEstimate:
        """Load saved readings. Bad or missing data starts over."""
        if not isinstance(data, dict):
            return cls()
        percent = _number(data.get("percent"))
        capacity = _number(data.get("capacity_kwh"))
        samples = data.get("samples")
        if (
            percent is None
            or capacity is None
            or not isinstance(samples, int)
            or isinstance(samples, bool)
            or samples < 0
            or not RESERVE_MIN_PERCENT <= percent <= RESERVE_MAX_PERCENT
        ):
            return cls()
        return cls(percent, samples, capacity)

    def as_diagnostics(self) -> dict[str, Any]:
        return {
            "reserve_percent": self.value,
            "learned": self.learned,
            "samples": self.samples,
            "learned_percent": round(self.percent, 2) if self.percent is not None else None,
            "capacity_kwh": self.capacity_kwh,
        }


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
