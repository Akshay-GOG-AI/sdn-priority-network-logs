#!/usr/bin/env python3
"""
UDP log client.

Sends log messages with a timestamp and severity to the log server.
  ERROR / CRITICAL -> UDP port 5001 (critical logs)
  INFO  / WARNING  -> UDP port 5002 (normal logs)

Message format (pipe separated, padded with '#' to --size bytes):
  host|seq|severity|send_ts|message

Every message sent is also written to results/sent_<name>.csv so the
analysis script can compute packet loss.

Example (inside Mininet):
  h1 python3 app/client.py --server-ip 10.0.0.4 --name h1 --duration 30
"""
import argparse
import csv
import os
import random
import socket
import time

CRITICAL_PORT = 5001
NORMAL_PORT = 5002

SEVERITIES = ["INFO", "WARNING", "ERROR", "CRITICAL"]
WEIGHTS = [50, 25, 15, 10]          # share of messages per severity (%)

MESSAGES = {
    "INFO": ["user login ok", "request served", "cache refreshed"],
    "WARNING": ["disk usage 85%", "slow response", "retrying connection"],
    "ERROR": ["db query failed", "request timeout", "service unreachable"],
    "CRITICAL": ["disk full", "service crashed", "out of memory"],
}


def port_for(severity):
    return CRITICAL_PORT if severity in ("ERROR", "CRITICAL") else NORMAL_PORT


def main():
    ap = argparse.ArgumentParser(description="UDP log client")
    ap.add_argument("--server-ip", required=True)
    ap.add_argument("--name", default=socket.gethostname(),
                    help="host label, e.g. h1 (must be unique per client)")
    ap.add_argument("--rate", type=float, default=50.0, help="logs per second")
    ap.add_argument("--duration", type=float, default=30.0, help="seconds")
    ap.add_argument("--size", type=int, default=500, help="datagram size in bytes")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)
    os.makedirs(args.out_dir, exist_ok=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / args.rate
    sent = {s: 0 for s in SEVERITIES}

    path = os.path.join(args.out_dir, f"sent_{args.name}.csv")
    start = time.time()
    next_send = start
    seq = 0
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["host", "seq", "severity", "send_ts", "port"])
        while time.time() - start < args.duration:
            sev = random.choices(SEVERITIES, WEIGHTS)[0]
            port = port_for(sev)
            send_ts = time.time()
            body = f"{args.name}|{seq}|{sev}|{send_ts:.6f}|{random.choice(MESSAGES[sev])}"
            payload = body.ljust(args.size, "#").encode()
            try:
                sock.sendto(payload, (args.server_ip, port))
            except OSError as exc:
                print(f"[{args.name}] send error: {exc}")
            writer.writerow([args.name, seq, sev, f"{send_ts:.6f}", port])
            sent[sev] += 1
            seq += 1

            next_send += interval
            pause = next_send - time.time()
            if pause > 0:
                time.sleep(pause)

    print(f"[{args.name}] done: sent {seq} logs {sent}")


if __name__ == "__main__":
    main()
