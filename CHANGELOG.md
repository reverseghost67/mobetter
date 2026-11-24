# Changelog

## 2026-11-23: Kasa smart plugs
- New optional "Plugs" card for TP-Link Kasa smart plugs (python-kasa, all local, no account needed for older plugs such as the HS103): on/off per plug, "x of y on".
- Optional per-plug safety timer `auto_off_minutes` (meant for heaters): MoBetter turns the plug off after it has been on that long, however it was switched on; the card shows the minutes left. Nothing ever switches a plug on by itself.
- Plugs are listed in config.json by host (and MAC, so a changed address is found again by broadcast). Every change is written to the audit log.

## 2026-11-22: AiDot / Linkind Wi-Fi bulbs
- New optional lights card for AiDot (Linkind, made by Leedarson) Wi-Fi bulbs, using the python-aidot library: groups from config.json, scene buttons, color chips + any-color picker, group brightness and warmth, each bulb's own on/off. Local control on the home network after a one-time sign-in that saves only each bulb's key.
- New API: GET /api/aidot/status, POST /api/aidot/set (only bulbs listed in config; every change audited).

## 2026-11-21: more than one receiver
- New: **jump buttons between MoBetter pages.** Run one copy per receiver and list the others under `"peers"` in `config.json`; each page shows a small button per peer in the top bar. See *More than one receiver* in the README.
- `/api/config` now also returns `peers` (name plus port or http(s) URL only; anything else is ignored).
- Compatibility: a second model, **TSR-5830** (RX-V583 twin, firmware 2.87), runs the Home page and the Setup list (settings read from the receiver itself). Changing settings on it has not been tried yet.
- Tests: 13 of 13 pass.
