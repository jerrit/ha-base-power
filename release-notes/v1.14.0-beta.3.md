## Base Power 1.14.0 beta 3

This is a beta. To get it: go to **Settings → Devices & services → Marketplace**, open the **Base Power** device, and enable the **Pre-release** switch, which is off and hidden by default. Turn it on, then install the update. If something looks wrong, please open an issue.

### Changes
- **System Capacity for batteries the integration doesn't recognize yet,** such as ones Base reports as `GEN3`. Until now these used the Battery Capacity setting, which most installs left at the pre-filled 25 kWh. The size is now estimated from Base's own "backup at 750 W" figure: usable energy left ÷ 0.75 kW, divided by the charge level. On a 25 kWh Single Battery that comes out at 22.5 kWh usable, so the integration divides by 0.9 and picks the nearest size Base sells. It only uses the estimate when it lands within 12% of a known size, and only from readings at 50% charge or higher. Otherwise your setting still applies. `capacity_source` reads `estimated` when the estimate is in use.
- **Base's own battery size is used when Base sends it.** Base's newer `GetHome` call can include the product and its nameplate size. Not every account gets it. When it's there, it's used ahead of the estimate (`capacity_source: nameplate`). This adds one call every 6 hours.
- **Battery Count** comes from the battery type Base reports (single, dual, or wall) when the model isn't mapped yet, so a `GEN3` dual counts as 2. Battery Count has a new `source` attribute.
- **System Capacity has new attributes:** `estimated_usable_kwh` (the raw reading, always shown), `estimated_capacity_kwh` (the size it points to), `nameplate_capacity_kwh`, and `product_name`. Diagnostics include a `capacity_estimate` summary.
- Known SKUs and battery models still win, so nothing changes if your capacity was already detected.

### Please check
- **Base Core and Wall owners:** after the battery has sat at 50% or higher for a few minutes, check that `estimated_capacity_kwh` on System Capacity matches your real size. Either way, please attach your diagnostics (Base Power's three-dot menu → Download diagnostics) to an issue. The 90% usable figure comes from one Single Battery so far, and your report confirms it or corrects it.
- After a restart, System Capacity can show your setting for a few minutes before the estimate takes over. That's expected.

Restart Home Assistant after updating.
