# MoBetter

A modern, dark, minimal web page for controlling a Yamaha AV receiver from your phone, tablet or PC. It does everything the receiver's own Web Setup page does (every setting, in the same groups) and adds a clean Home screen.

> **Status: early. Built and tested on one receiver (RX-A3070).** Other Yamaha models should work, because the page builds itself from your own receiver's list of settings, but that is not proven yet. If you try it, a *Compatibility report* (Issues tab) is the most useful thing you can send.
> **Not affiliated with Yamaha.** "Yamaha" is a trademark of its owner, used here only to say which products this works with. **Use at your own risk:** the Setup screens can change things like speaker settings, and even factory-reset a receiver. Anything risky asks you to confirm first.

## What you get
- **Home:** power, volume (with a maximum you set), mute, every input with your own names, sound programs, Straight / Enhancer / Pure Direct, bass, treble, subwoofer, scenes.
- **Setup:** every group of the receiver's own settings, with the right control for each (choices, number ranges, text), live values, and a filter box.
- **Phone friendly,** installable to your home screen.
- **Private:** nothing leaves your home network. No accounts, no telemetry.

## Install (needs Python 3.9 or newer; nothing else to install)
1. Download this project (the green Code button, then Download ZIP) and unzip it.
2. In the folder, run (use your receiver's address; it is remembered):
   ```
   python server.py --receiver 192.168.0.50 --lan
   ```
   On Windows you can double-click `start.bat` instead, after the first run.
3. Open `http://localhost:8780` on the same computer, or `http://<this computer's address>:8780` on a phone or tablet on your home Wi-Fi.

The first start downloads the settings list from your own receiver and saves it in the local `data/` folder. Nothing from Yamaha is included in this project.

## Safety, please read
- **Receivers have no password.** Anyone on your Wi-Fi (guests too) can use this page, just as they could use the receiver's own pages. **Never expose it to the internet.** Do not forward a port to it.
- Only private-network devices are served, only the receiver's own control paths are forwarded, and every change is written to `data/audit.log`.
- The volume maximum starts at -20 dB. Raise it in **Setup > Preferences**.
- `python tools/backup_settings.py` saves a read-only backup of your receiver's settings to `data/` first. It is a good habit before you change settings.

## Tests
`python -m unittest discover -s tests` (uses a tiny fake receiver; no hardware needed).

## About this project
MoBetter is a personal project, shared as-is under the [MIT license](LICENSE). It is **read-only**: pull requests and feature requests are not accepted (see [CONTRIBUTING](CONTRIBUTING.md)). You are free to fork it.
