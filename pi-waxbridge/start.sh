#!/bin/sh
# Start the Waxwing bridge in the background (called by piCorePlayer's User command #1 at boot, as root).
# "restart" stops any running copy first. Safe to run twice.
if [ "$1" = "restart" ]; then
  for p in $(ps | grep 'python3.11 /home/tc/waxbridge/waxbridge.py' | grep -v grep | awk '{print $1}'); do kill "$p"; done
  sleep 2
fi
if ps | grep 'python3.11 /home/tc/waxbridge/waxbridge.py' | grep -v grep >/dev/null; then exit 0; fi
cd /home/tc/waxbridge || exit 1
nohup python3.11 /home/tc/waxbridge/waxbridge.py >>/tmp/waxbridge.log 2>&1 &
exit 0
