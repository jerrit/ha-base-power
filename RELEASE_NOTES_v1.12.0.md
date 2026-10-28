## Base Power 1.12.0

- Usage energy is parsed from the hourly series. Grid to Home, Solar to Home, Total Home Energy, Self-Sufficiency, and Daily Total (Grid) use today's samples. Battery to Home stays unknown.
- Only `MobileGetUsageEnergy` sends the numeric service location id. Recent usage, billing, usage cycles, and Wi-Fi keep the address string.
- Recent usage values are average kW, not kWh. Watts are that value times 1000, interval energy is the value divided by 4, and the daily total counts only today's intervals. The old math was about four times too high.
- Daily Average Grid Interval is today's usage total divided by the number of 15-minute intervals. Grid Power (amps) is grid kilowatts times 1000 divided by voltage. A negative value is export.
- The current billing cycle start is parsed. Its end, the server clock at request time, is not used to decide which cycle is current.
- gRPC status is read from the trailer frame in the response body as well as from the headers.
- The Clerk session id is no longer written to the log in full.
- Polling is tiered: service context and grid status every 60 seconds, recent usage and usage energy every 15 minutes, and dashboard, Wi-Fi, billing, and usage cycles every 6 hours. About 129 calls an hour instead of about 216.
- Bill amount and bill due date stay empty on plans that have no billing info. That empty response is expected.

Restart Home Assistant after updating. The integration keeps the already-loaded code until then.
