"""Read-only probe: find the Parks Audio Waxwing over Bluetooth LE and read one status packet.
Sends only the documented 'start status stream' request [99, 1, 0]; changes no setting.
API: https://www.parksaudiollc.com/bluetoothapi.html"""
import asyncio, sys
from bleak import BleakScanner, BleakClient

SERVICE = "4dddd48d-cf30-492e-850b-76d57dd76900"
WRITE = "4dddd48d-cf30-492e-850b-76d57dd76901"
NOTIFY = "4dddd48d-cf30-492e-850b-76d57dd76902"


async def main():
    print("scanning 12 s ...")
    found = await BleakScanner.discover(timeout=12, return_adv=True)
    cand = []
    for addr, (dev, adv) in found.items():
        uu = [u.lower() for u in (adv.service_uuids or [])]
        name = dev.name or adv.local_name or ""
        if SERVICE in uu or "wax" in name.lower() or "parks" in name.lower():
            cand.append((dev, adv))
        print(f"  {addr}  rssi {adv.rssi:>4}  {name}")
    if not cand:
        print("NO WAXWING FOUND (is it on, and is the phone app closed?)")
        return 2
    dev, adv = cand[0]
    print(f"Waxwing candidate: {dev.address} {dev.name} rssi {adv.rssi}")
    got = asyncio.Event()
    packets = []

    def on(_, data: bytearray):
        packets.append(bytes(data))
        got.set()

    async with BleakClient(dev, timeout=20) as c:
        print("connected:", c.is_connected)
        await c.start_notify(NOTIFY, on)
        await c.write_gatt_char(WRITE, bytes([99, 1, 0]), response=True)
        try:
            await asyncio.wait_for(got.wait(), 5)
        except asyncio.TimeoutError:
            print("connected but no status packet in 5 s")
        await asyncio.sleep(1.2)
        await c.write_gatt_char(WRITE, bytes([99, 0, 0]), response=True)  # stop the stream again
        await c.stop_notify(NOTIFY)
    if packets:
        p = packets[-1]
        print(f"status packets: {len(packets)}, last length {len(p)}")
        print("first 40 bytes:", list(p[:40]))
    return 0


sys.exit(asyncio.run(main()))
