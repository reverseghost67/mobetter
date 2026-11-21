# Changelog

## 2026-11-21: more than one receiver
- New: **jump buttons between MoBetter pages.** Run one copy per receiver and list the others under `"peers"` in `config.json`; each page shows a small button per peer in the top bar. See *More than one receiver* in the README.
- `/api/config` now also returns `peers` (name plus port or http(s) URL only; anything else is ignored).
- Compatibility: a second model, **TSR-5830** (RX-V583 twin, firmware 2.87), runs the Home page and the Setup list (settings read from the receiver itself). Changing settings on it has not been tried yet.
- Tests: 13 of 13 pass.
