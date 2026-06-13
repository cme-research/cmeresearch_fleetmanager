#!/usr/bin/env python3
"""Minimal AGV-side heartbeat client.

Run this on the AGV in a loop (systemd timer, cron, or just a long-running
script) to push periodic heartbeats to the fleetmanager.

Usage:
    export FLEETMANAGER_URL=https://fleetmanager.example.com
    export AGV_UUID=01234567-89ab-cdef-0123-456789abcdef
    python3 agv_heartbeat.py

The UUID is the one assigned by the fleetmanager when the AGV was added.
Keep it private — anyone with the UUID can post heartbeats on this AGV's
behalf. Run the fleetmanager behind a VPN and use HTTPS in production.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def post_heartbeat(url: str, agv_uuid: str, rtt_ms: float | None = None,
                   note: str = "", timeout: float = 5.0) -> int:
    body = json.dumps({"uuid": agv_uuid, "rtt_ms": rtt_ms, "note": note}).encode()
    req = urllib.request.Request(
        url=url.rstrip("/") + "/api/heartbeat/",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=os.environ.get("FLEETMANAGER_URL"),
                    help="Base URL of the fleetmanager (or FLEETMANAGER_URL env).")
    ap.add_argument("--uuid", default=os.environ.get("AGV_UUID"),
                    help="This AGV's UUID (or AGV_UUID env).")
    ap.add_argument("--interval", type=float, default=60.0,
                    help="Seconds between heartbeats.")
    ap.add_argument("--once", action="store_true",
                    help="Send a single heartbeat and exit.")
    args = ap.parse_args()

    if not args.url or not args.uuid:
        print("error: both --url and --uuid (or FLEETMANAGER_URL / AGV_UUID env) "
              "must be set", file=sys.stderr)
        return 2

    while True:
        t0 = time.monotonic()
        try:
            status = post_heartbeat(args.url, args.uuid)
            rtt_ms = (time.monotonic() - t0) * 1000.0
            print(f"heartbeat status={status} rtt={rtt_ms:.1f}ms", flush=True)
        except urllib.error.HTTPError as exc:
            print(f"heartbeat http {exc.code}: {exc.reason}", flush=True)
        except urllib.error.URLError as exc:
            print(f"heartbeat failed: {exc.reason}", flush=True)
        except Exception as exc:
            print(f"heartbeat error: {exc!r}", flush=True)

        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
