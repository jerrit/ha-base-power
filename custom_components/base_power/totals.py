"""Running energy totals that never reset.

The daily sensors restart at local midnight, but Base posts each day's last
hour a few minutes after midnight. That hour never reaches the daily
sensors. These totals add every hour from the rolling 24-hour overview the
first time it appears, whichever day it belongs to, so nothing is lost.

The overview only reaches back 24 hours. If Home Assistant or Base is down
for longer, the hours in between never arrive. Base's daily totals fill
that hole: once a day is over, any energy Base reports for it beyond the
hours already counted is added.
"""

from __future__ import annotations

import itertools
from typing import Any

from .const import (
    COUNTED_HOURS_KEEP_SECONDS,
    DAY_REPAIR_MIN_KWH,
    DAY_SETTLE_SECONDS,
)

_DAY = 86400


class EnergyTotals:
    """Home usage and solar totals, built from hourly samples."""

    def __init__(self) -> None:
        self.home_kwh = 0.0
        self.solar_kwh = 0.0
        # Hour start (Unix seconds) → (home kWh, solar kWh) already added
        self.counted: dict[int, tuple[float, float]] = {}
        # Day start → (home kWh, solar kWh) added from Base's daily total
        self.repaired: dict[int, tuple[float, float]] = {}
        # When counting began. Days that started before this are never
        # repaired: they were only partly seen, on purpose.
        self.since: float | None = None

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
        if self.since is None and (hours or self.started):
            self.since = now
            changed = True
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
        for kept in (self.counted, self.repaired):
            for start in [start for start in kept if start < cutoff]:
                del kept[start]
                changed = True
        return changed

    def reconcile_days(self, days: list[dict[str, Any]], now: float) -> bool:
        """Add what Base's daily totals show the hourly samples missed.

        ``days`` is one entry per day, each with ``start`` (the site's local
        midnight), ``home_kwh`` and ``solar_kwh``. A day counts only when
        it began after counting did, ended at least DAY_SETTLE_SECONDS ago,
        and the next day's entry marks where it ends. The amount added is
        Base's total minus the hours already counted for that day, when
        that is more than rounding. Returns True when something changed.
        """
        if self.since is None:
            return False
        changed = False
        ordered = sorted(
            (day for day in days if day.get("start") is not None),
            key=lambda day: day["start"],
        )
        oldest_kept = now - COUNTED_HOURS_KEEP_SECONDS + _DAY
        for day, following in itertools.pairwise(ordered):
            start, end = day["start"], following["start"]
            # 23 to 25 hours allows for a daylight saving change. Anything
            # else means Base skipped a day, and the range cannot be trusted.
            if not 23 * 3600 <= end - start <= 25 * 3600:
                continue
            if start < self.since or start < oldest_kept:
                continue
            if end + DAY_SETTLE_SECONDS > now:
                continue
            counted_home = 0.0
            counted_solar = 0.0
            for hour_start, (home, solar) in self.counted.items():
                if start <= hour_start < end:
                    counted_home += home
                    counted_solar += solar
            done_home, done_solar = self.repaired.get(start, (0.0, 0.0))
            missing_home = float(day.get("home_kwh") or 0.0) - counted_home - done_home
            missing_solar = float(day.get("solar_kwh") or 0.0) - counted_solar - done_solar
            add_home = missing_home if missing_home > DAY_REPAIR_MIN_KWH else 0.0
            add_solar = missing_solar if missing_solar > DAY_REPAIR_MIN_KWH else 0.0
            if not add_home and not add_solar:
                continue
            self.home_kwh += add_home
            self.solar_kwh += add_solar
            self.repaired[start] = (done_home + add_home, done_solar + add_solar)
            changed = True
        return changed

    def as_dict(self) -> dict[str, Any]:
        """Data for the store."""
        return {
            "home_kwh": self.home_kwh,
            "solar_kwh": self.solar_kwh,
            "counted": [[start, home, solar] for start, (home, solar) in self.counted.items()],
            "repaired": [[start, home, solar] for start, (home, solar) in self.repaired.items()],
            "since": self.since,
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
            totals.repaired = {
                int(start): (float(home), float(solar))
                for start, home, solar in data.get("repaired") or []
            }
            since = data.get("since")
            totals.since = float(since) if since is not None else None
        except (TypeError, ValueError):
            return cls()
        return totals
