#!/usr/bin/env python3
"""
Compute packet loss and delivery delay per severity for one experiment run.

Reads  results/sent_*.csv   (written by every client)
       results/received.csv (written by the server)
Prints a table, and with --label also appends the rows to results/summary.csv
so baseline / static / dynamic runs can be compared and graphed later.

Example:
  python3 app/analyze.py --label baseline_heavy
"""
import argparse
import csv
import glob
import os

SEVERITIES = ["CRITICAL", "ERROR", "WARNING", "INFO"]


def percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(pct / 100.0 * (len(ordered) - 1))))
    return ordered[idx]


def main():
    ap = argparse.ArgumentParser(description="Log experiment analysis")
    ap.add_argument("--dir", default="results")
    ap.add_argument("--label", default=None,
                    help="name of this run, e.g. baseline_heavy; appends to summary.csv")
    args = ap.parse_args()

    # what was sent: (host, seq) -> severity
    sent = {}
    for path in glob.glob(os.path.join(args.dir, "sent_*.csv")):
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                sent[(row["host"], int(row["seq"]))] = row["severity"]
    if not sent:
        print("No sent_*.csv files found. Run the clients first.")
        return

    # what arrived: only count datagrams that match something we sent
    delays = {s: [] for s in SEVERITIES}
    received_keys = set()
    recv_path = os.path.join(args.dir, "received.csv")
    if os.path.exists(recv_path):
        with open(recv_path, newline="") as f:
            for row in csv.DictReader(f):
                key = (row["host"], int(row["seq"]))
                if key in sent and key not in received_keys:
                    received_keys.add(key)
                    delays[sent[key]].append(float(row["delay_ms"]))

    header = f"{'severity':<10}{'sent':>7}{'recv':>7}{'loss %':>9}{'avg ms':>10}{'p95 ms':>10}{'max ms':>10}"
    print(header)
    print("-" * len(header))
    rows = []
    for sev in SEVERITIES:
        n_sent = sum(1 for v in sent.values() if v == sev)
        d = delays[sev]
        n_recv = len(d)
        loss = 100.0 * (n_sent - n_recv) / n_sent if n_sent else 0.0
        avg = sum(d) / n_recv if n_recv else 0.0
        p95 = percentile(d, 95)
        mx = max(d) if d else 0.0
        print(f"{sev:<10}{n_sent:>7}{n_recv:>7}{loss:>9.2f}{avg:>10.2f}{p95:>10.2f}{mx:>10.2f}")
        rows.append([args.label, sev, n_sent, n_recv,
                     f"{loss:.2f}", f"{avg:.2f}", f"{p95:.2f}", f"{mx:.2f}"])

    if args.label:
        summary = os.path.join(args.dir, "summary.csv")
        new_file = not os.path.exists(summary)
        with open(summary, "a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["label", "severity", "sent", "received",
                            "loss_pct", "avg_delay_ms", "p95_delay_ms", "max_delay_ms"])
            w.writerows(rows)
        print(f"\nAppended to {summary}")


if __name__ == "__main__":
    main()
