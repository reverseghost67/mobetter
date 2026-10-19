#!/bin/sh
# First time:  ./start.sh --receiver 192.168.0.50     (use your receiver's address; it is remembered)
cd "$(dirname "$0")" && python3 server.py --lan "$@"
