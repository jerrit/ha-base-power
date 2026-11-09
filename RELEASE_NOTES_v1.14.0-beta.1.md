## Base Power 1.14.0 beta 1

This is a beta. To get it, open Base Power in the Marketplace and turn on pre-release updates. If something looks wrong, please open an issue.

### Fixes
- **Daily energy missed the last hour of each day.** Base posts the 11 PM hour a few minutes after midnight, after the daily sensors have reset, so that hour was never counted. That is about 5% of daily use missing from the Energy Dashboard. The new running-total sensors below count it.
- **Bill Amount** showed spend so far this cycle, the same as Bill to Date. It's now **Last Bill Amount** and shows your last bill. The entity ID doesn't change.

### New
- **Home Usage (Total)** and, on homes with solar, **Solar to Home (Total)**. These running totals never reset at midnight. Each hour is counted once, whenever Base posts it, and the count survives restarts. They start with the last 24 hours. Use them in the Energy Dashboard in place of the daily sensors.
- **Battery model.** When Base doesn't report a hardware SKU, System Capacity now uses the battery model Base lists for the site (`capacity_source: model`). Battery Hardware shows it in a `battery_models` attribute.
- **Picking a site during setup.** If your account has more than one site, setup lists them by street address and you pick one.
- **Solar Connected** also turns on when Base lists solar for your site.

### Changes
- **Battery-only homes** (no energy service from Base) no longer get billing sensors that are always unknown: Last Bill Amount, Bill Due Date, Energy Cost Today, Bill to Date, Projected Bill, and Solar Buyback Credit. Ones already set up are removed. A billing sensor that has a value stays.

### Please check
- If you use the Energy Dashboard, switch Grid consumption to **Home Usage (Total)** and Solar to **Solar to Home (Total)**.
- If you get energy from Base, compare **Last Bill Amount** with your last invoice.
- Open Battery Hardware and look at `battery_models`. If you have two batteries, a Wall, or Base Core, please share what it shows in an issue. Those models aren't confirmed yet.

Restart Home Assistant after updating.
