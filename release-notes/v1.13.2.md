## Base Power 1.13.2

Battery models are now detected automatically, and the midnight reset no longer shows "unknown".

### Fixes
- **Total Home Energy** and **Self-Sufficiency** showed "unknown" for about an hour after midnight, until Base posted the new day's first hour. They now show 0 kWh and 0% after the reset, then climb as usual. "Unknown" now means Base sent no reading at all.

### Changes
- **System Capacity is detected from your battery model** when Base reports one:

  | Model | Size |
  |---|---|
  | Wall Battery (`GRO_WALL`) | 20 kWh |
  | Single Battery (`GRO_SINGLE`) | 25 kWh |
  | Double Battery (`GRO_DUAL`) | 50 kWh (2 units) |
  | Base Core (`BASE1_SINGLE`) | 39.2 kWh |
  | Dual Base Core (`BASE1_DUAL`) | 78.4 kWh (2 units) |

- **The detected model now wins over the Battery Capacity setting.** The settings form pre-fills a capacity, so many installs saved 25 kWh without choosing it. The setting is still used when Base doesn't report a model, or reports one this integration doesn't know yet. System Capacity's `capacity_source` attribute shows which one is in use (`sku` or `option`).
- Adds a **20 kWh (1 Wall)** choice to the Battery Capacity setting.

If your System Capacity changes after updating, the detected model is the reason. If it looks wrong, open an issue with your Battery Hardware value and system size.

### Known issues
- **Bill Amount** may not match the Base app. A fix is coming in a later release.

Restart Home Assistant after updating.
