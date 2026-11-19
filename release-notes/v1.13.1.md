## Base Power 1.13.1

Fixes and clearer names, mostly from community testing of 1.13.0. Thanks to everyone who compared values with the Base app on GitHub and Reddit.

### Fixes
- **Grid Support** missed events while they were happening. It read only Base's event list, which refreshes every 15 minutes and lagged behind an event in progress. It now also turns on within a minute when the grid is up and the battery is powering the home (0.2 kW or more). New attributes: `detected_by` and `battery_output_kw`. Grid support never turns Grid Power off, so outage automations won't fire on it.
- **Bill Due Date** was always unknown. The older billing call it read has no due date. It now reads the energy account from Base's newer billing API. It shows the due date of the oldest unpaid bill and stays unknown when nothing is owed. Attributes show the amount due, past due, balance, next bill date, and next autopay date.
- **Total Home Energy** counted solar twice for homes with solar. It added solar and battery on top of home usage, which already includes them. It now equals home usage. **Self-Sufficiency** used the same inflated total and now reads correctly (capped at 100%). Homes without solar or battery discharge see no change.
- **Today's energy** showed yesterday's total for about an hour after midnight. Base posts the new day's first hour late, and the integration kept the last value until then. The daily energy sensors now start at 0 at local midnight. A failed refresh during the day still keeps the last value.

### Changes
- **Renamed** so the names match the data:
  - Grid to Home Energy → **Home Usage Today (All Sources)**. Base reports total home usage here, not grid draw.
  - Daily Total (Grid) → **Daily Total (Home Usage)**.
  - Battery Backup Time → **Battery Backup Time (Current Load)**: hours left if the house keeps using power at its current rate.
  - Battery Backup Time (750 W) → **Battery Backup Time (750 W Essentials)**: hours left if you cut down to essentials only.
- **Energy Cost Today** gets a `cost_last_24h` attribute. The sensor counts from midnight, while the Base app's daily overview shows the last 24 hours.
- Adds the **`GRO_DUAL`** SKU (two 25 kWh units, 50 kWh), confirmed by a user. Two-battery homes that never saved a capacity now get 50 kWh automatically. A saved capacity still wins.
- The README covers installing from the **Home Assistant Marketplace**, which replaced HACS in Home Assistant 2026.11.

Entity IDs don't change, so dashboards and automations keep working. Only display names change. If you use Home Usage Today as Grid consumption in the Energy dashboard, note that it is total home usage. It matches grid use only when solar and the battery supply nothing.

Restart Home Assistant after updating.

If Base bills you for energy, please compare Bill Due Date and `cost_last_24h` with the app and comment on #16.
