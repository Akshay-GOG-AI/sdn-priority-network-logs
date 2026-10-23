#!/usr/bin/env python3
"""
Centralized UDP log server.

Listens on two UDP ports at the same time (one thread per port):
  5001 critical logs, 5002 normal logs
For every datagram it records the receive time, parses the log, computes the
delivery delay (receive time - send timestamp) and appends a row to
results/received.csv. Malformed datagrams are counted and ignored.

All Mininet hosts share one machine clock, so send/receive timestamps are
directly comparable.

Example (inside Mininet):
  h4 python3 app/server.py --duration 45
"""
import argparse
import csv
import os
import socket
import threading
import time

PORTS = [5001, 5002]


class Collector:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.file = open(path, "w", newline="")
        self.writer = csv.writer(self.file)
        self.writer.writerow(
            ["recv_ts", "port", "host", "seq", "severity", "send_ts", "delay_ms"])
        self.lock = threading.Lock()
        self.counts = {}
        self.malformed = 0

    def record(self, port, recv_ts, data):
        try:
            text = data.decode(errors="strict")
            host, seq, severity, send_ts, _msg = text.split("|", 4)
            seq = int(seq)
            send_ts = float(send_ts)
        except (ValueError, UnicodeDecodeError):
            with self.lock:
                self.malformed += 1
            return
        delay_ms = (recv_ts - send_ts) * 1000.0
        with self.lock:
            self.writer.writerow([f"{recv_ts:.6f}", port, host, seq, severity,
                                  f"{send_ts:.6f}", f"{delay_ms:.3f}"])
            self.counts[severity] = self.counts.get(severity, 0) + 1

    def close(self):
        with self.lock:
            self.file.close()


def listen(port, bind_ip, collector, stop):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    sock.bind((bind_ip, port))
    sock.settimeout(0.5)
    while not stop.is_set():
        try:
            data, _addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        collector.record(port, time.time(), data)
    sock.close()


def main():
    ap = argparse.ArgumentParser(description="UDP log server")
    ap.add_argument("--bind-ip", default="0.0.0.0")
    ap.add_argument("--duration", type=float, default=0,
                    help="seconds to run (0 = until Ctrl+C)")
    ap.add_argument("--out", default="results/received.csv")
    args = ap.parse_args()

    collector = Collector(args.out)
    stop = threading.Event()
    threads = [threading.Thread(target=listen,
                                args=(p, args.bind_ip, collector, stop),
                                daemon=True) for p in PORTS]
    for t in threads:
        t.start()
    print(f"[server] listening on UDP ports {PORTS}")

    try:
        if args.duration > 0:
            time.sleep(args.duration)
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass

    stop.set()
    for t in threads:
        t.join()
    collector.close()
    print(f"[server] done: received {collector.counts}, malformed {collector.malformed}")


if __name__ == "__main__":
    main()
