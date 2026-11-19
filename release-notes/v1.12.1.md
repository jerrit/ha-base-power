## Base Power 1.12.1

- Energy and daily usage sensors update every 15 minutes again. In 1.12.0 the 60-second grid poll kept restarting the 15-minute refresh timer, so usage only loaded at startup and the daily sensors stayed at 0 after midnight (#13).
- The amps sensor is renamed from Grid Power to Grid Current. The outage sensor keeps the name Grid Power. Entity IDs and history do not change.

Restart Home Assistant after updating. The integration keeps the already-loaded code until then.

Thanks to @colforbin13 for finding the cause and the fix.
