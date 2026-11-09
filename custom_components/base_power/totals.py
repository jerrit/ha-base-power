"""Running energy totals that never reset.

The daily sensors restart at local midnight, but Base posts each day's last
hour a few minutes after midnight. That hour never reaches the daily
sensors. These totals add every hour from the rolling 24-hour overview the
first time it appears, whichever day it belongs to, so nothing is lost.
"""

from __future__ import annotations

from typing import Any

from .const import COUNTED_HOURS_KEEP_SECONDS


class EnergyTotals:
    """Home usage and solar totals, built from hourly samples."""

    def __init__(self) -> None:
        self.home_kwh = 0.0
        self.solar_kwh = 0.0
        # Hour start (Unix seconds) → (home kWh, solar kWh) already added
        self.counted: dict[int, tuple[float, float]] = {}

    @property
    def started(self) -> bool:
        """False until the first hour is counted. The sensors stay unknown until then."""
        return bool(self.counted) or self.home_kwh > 0 or self.solar_kwh > 0

    def add_hours(self, hours: list[dict[str, Any]], now: float) -> bool:
        """Add new hours, and any growth in an hour already counted.

        A lower value for a counted hour is ignored, so the totals never go
        down. Returns True when something changed.
        """
        changed = False
        for hour in hours:
            start = hour.get("start")
            if start is None:
                continue
            home = max(float(hour.get("home_kwh") or 0.0), 0.0)
            solar = max(float(hour.get("solar_kwh") or 0.0), 0.0)
            old_home, old_solar = self.counted.get(start, (0.0, 0.0))
            if start in self.counted and home <= old_home and solar <= old_solar:
                continue
            self.home_kwh += max(home - old_home, 0.0)
            self.solar_kwh += max(solar - old_solar, 0.0)
            self.counted[start] = (max(home, old_home), max(solar, old_solar))
            changed = True
        cutoff = now - COUNTED_HOURS_KEEP_SECONDS
        for start in [start for start in self.counted if start < cutoff]:
            del self.counted[start]
            changed = True
        return changed

    def as_dict(self) -> dict[str, Any]:
        """Data for the store."""
        return {
            "home_kwh": self.home_kwh,
            "solar_kwh": self.solar_kwh,
            "counted": [[start, home, solar] for start, (home, solar) in self.counted.items()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> EnergyTotals:
        """Rebuild from the store. Bad or missing data starts fresh."""
        totals = cls()
        if not isinstance(data, dict):
            return totals
        try:
            totals.home_kwh = float(data.get("home_kwh") or 0.0)
            totals.solar_kwh = float(data.get("solar_kwh") or 0.0)
            totals.counted = {
                int(start): (float(home), float(solar))
                for start, home, solar in data.get("counted") or []
            }
        except (TypeError, ValueError):
            return cls()
        return totals
