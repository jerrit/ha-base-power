## Base Power 1.14.0 beta 1

This is a beta. To get it, open Base Power in the Marketplace and turn on pre-release updates. If something looks wrong, please open an issue.

### Fixes
- **Daily energy missed the last hour of each day.** Base posts the 11 PM hour a few minutes after midnight, after the daily sensors have reset, so that hour was never counted. That is about 5% of daily use missing from the Energy Dashboard. The new running-total sensors below count it.
- **Bill Amount** showed spend so far this cycle, the same as Bill to Date. It's now **Last Bill Amount** and shows your last bill. The entity ID doesn't change.
- **Asked to sign in again for no reason.** A rate limit or server error from Base's sign-in service was treated as a rejected session. That asked for a new sign-in, made every entity unavailable, and stopped the 15-minute refresh. Now it's retried on the next refresh.
- **Battery Charging** read unknown most of the time below 100%. Base reports the charge as a whole percent, so two polls in a row rarely differ. It now holds the last direction it saw.
- **Battery Status** showed Offline during a firmware update. It now shows **Updating Firmware**, and **No Install Planned** where it applies.
- **Slow replies no longer pile up.** Requests time out after 20 seconds, and a 60-second poll is skipped while the one before it is still waiting.
- **Old values no longer look current.** Usage and energy sensors turn unavailable when Base has sent no new usage data for about an hour.
- A call that fails during the 6-hour refresh is retried after 15 minutes, not 6 hours later.

### New
- **Home Usage (Total)** and, on homes with solar, **Solar to Home (Total)**. These running totals never reset at midnight. Each hour is counted once, whenever Base posts it, and the count survives restarts. They start with the last 24 hours. Use them in the Energy Dashboard in place of the daily sensors.
- **Battery model.** When Base doesn't report a hardware SKU, System Capacity now uses the battery model Base lists for the site (`capacity_source: model`). Battery Hardware shows it in a `battery_models` attribute.
- **Picking a site during setup.** If your account has more than one site, setup lists them by street address and you pick one.
- **Grid Voltage** sensor. Voltage used to be only an attribute on the Grid Power binary sensor.
- **Power from Grid**, **Battery Power**, and, on homes with solar, **Solar Power**: live power in watts, updated every minute. Power from Grid is negative while exporting. Battery Power is positive while the battery discharges.
- **WiFi is detected for you.** Base reports which network the battery is on, so **WiFi SSID** and **WiFi Signal** follow it without any setup.
- **Battery Backup Time (750 W Essentials)** updates every minute. It was every 10 minutes.
- **Next Service Visit**: the next visit Base has scheduled, when there is one.
- **Solar Export Today**, on homes with solar.
- **Totals catch up after downtime.** If Home Assistant or Base is down for more than a day, the (Total) sensors add the missed energy from Base's daily totals once the day is over.
- **Download diagnostics.** The integration's menu has a diagnostics file to attach to bug reports. Tokens, your email, site and account ids, and WiFi names are removed.
- **Solar Connected** also turns on when Base lists solar for your site.

### Changes
- **Battery-only homes** (no energy service from Base) no longer get billing sensors that are always unknown: Last Bill Amount, Bill Due Date, Energy Cost Today, Bill to Date, Projected Bill, and Solar Buyback Credit. Ones already set up are removed. A billing sensor that has a value stays.

- The **WiFi Network** select is removed. The WiFi option stays as a fallback and can be left empty.
- **Asset ID** is renamed **Account ID**, which is what Base calls it. The entity ID doesn't change.
- Money sensors show **USD**, not **$**, and use Home Assistant's monetary type.
- **Daily Total (Home Usage)** and **Total Home Energy** repeat Home Usage Today. They are off by default on new installs. Existing installs keep them.
- Debug logs no longer include raw API replies, which could contain your address.
- Needs Home Assistant 2025.1 or newer.

### Please check
- If you use the Energy Dashboard, switch Grid consumption to **Home Usage (Total)** and Solar to **Solar to Home (Total)**.
- If you get energy from Base, compare **Last Bill Amount** with your last invoice.
- Watch **Battery Power** and **Battery Charging** while the battery recharges after a discharge, and tell us in an issue what they show. So far Battery Power has stayed at 0 while charging, not gone negative.
- Check that **WiFi SSID** matches the network in the Base app.
- If you have solar, check **Solar Export Today** against the Base app.
- Open Battery Hardware and look at `battery_models`. If you have two batteries, a Wall, or Base Core, please share what it shows in an issue. Those models aren't confirmed yet.

Restart Home Assistant after updating.
