"""One-time AiDot sign-in for the AiDot / Linkind lights card (you type your AiDot email + password; the password is hidden).

It logs in to the AiDot cloud ONCE, reads the list of bulbs and each bulb's own local key, and saves ONLY what
local control needs to data/aidot-devices.json (git-ignored, never published). The account password and cloud tokens are NOT saved.
Nothing is changed on the bulbs or in the AiDot account. Run it again after re-pairing or renaming bulbs in the app.
Needs: pip install python-aidot   (country is set to US below; change country_code if you are elsewhere)

Run (from the MoBetter folder):  python tools/aidot_login.py
"""
import asyncio, getpass, json, os, sys
import aiohttp
from aidot.client import AidotClient

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "aidot-devices.json")
KEEP = ("id", "name", "mac", "aesKey", "password", "productId", "type", "modelId", "hardwareVersion", "firmwareVersion")


async def main():
    print("AiDot one-time sign-in (local control setup). Nothing on the bulbs is changed.\n")
    email = input("AiDot email: ").strip()
    pw = getpass.getpass("AiDot password (hidden, nothing shows as you type): ")
    async with aiohttp.ClientSession() as session:
        client = AidotClient(session, country_code="US", username=email, password=pw)
        try:
            info = await client.async_post_login()
        except Exception as e:
            print("\nSign-in failed:", type(e).__name__, "- check the email/password and try again.")
            return 1
        data = await client.async_get_all_device()
        devices = data.get("device_list", [])
        slim = []
        for d in devices:
            row = {k: d.get(k) for k in KEEP if k in d}
            prod = d.get("product") or {}
            row["productName"] = prod.get("name")
            slim.append(row)
        out = {"user_id": info.get("id"), "country": "US", "devices": slim}
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", encoding="utf8") as f:
            json.dump(out, f, indent=1)
        await client.async_cleanup() if hasattr(client, "async_cleanup") else None
    pw = None
    print(f"\nDone. Found {len(slim)} device(s):")
    for d in slim:
        has_key = "local key OK" if (d.get("aesKey") and d["aesKey"][0]) else "NO local key"
        print(f"  - {d.get('name')}  ({d.get('productName') or d.get('type')})  [{has_key}]")
    print(f"\nSaved to {OUT} (private: bulb keys only, not your password). You can close this window.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
