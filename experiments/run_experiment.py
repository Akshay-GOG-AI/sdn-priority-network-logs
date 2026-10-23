#!/usr/bin/env python3
"""
Run ONE experiment without the interactive Mininet prompt (used by run_all.sh).

    sudo python3 experiments/run_experiment.py --rate 3M --duration 30 --wait 65

--rate 0 means no background congestion. The Ryu controller must already be
running. Run from the repository root.
"""
import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "topology"))

from mininet.log import setLogLevel  # noqa: E402
from topo import build_network, setup_bottleneck_queues  # noqa: E402

SERVER_IP = "10.0.0.4"


def main():
    ap = argparse.ArgumentParser(description="Run one log experiment")
    ap.add_argument("--rate", default="0",
                    help="background iperf UDP rate, e.g. 1500K or 3M (0 = none)")
    ap.add_argument("--duration", type=int, default=30,
                    help="seconds the clients send")
    ap.add_argument("--wait", type=int, default=65,
                    help="total run time; the server stays up this long")
    ap.add_argument("--controller-ip", default="127.0.0.1")
    ap.add_argument("--controller-port", type=int, default=6653)
    args = ap.parse_args()

    os.chdir(ROOT)
    os.makedirs("results", exist_ok=True)
    setLogLevel("info")

    net, bottleneck = build_network(args.controller_ip, args.controller_port)
    net.start()
    procs = []
    try:
        setup_bottleneck_queues(bottleneck)
        time.sleep(3)   # let the switches connect to the controller

        h1, h2, h3, h4, h5 = (net.get(n) for n in ("h1", "h2", "h3", "h4", "h5"))
        devnull = open(os.devnull, "w")
        server_out = open("results/server.out", "w")

        procs.append(h4.popen(
            ["python3", "app/server.py", "--duration", str(args.wait)],
            stdout=server_out, stderr=subprocess.STDOUT))
        time.sleep(1)

        if args.rate != "0":
            procs.append(h4.popen(["iperf", "-u", "-s", "-p", "5003"],
                                  stdout=devnull, stderr=devnull))
            time.sleep(1)
            procs.append(h5.popen(
                ["iperf", "-u", "-c", SERVER_IP, "-p", "5003",
                 "-b", args.rate, "-t", str(args.duration)],
                stdout=devnull, stderr=devnull))

        for name, host in (("h1", h1), ("h2", h2), ("h3", h3)):
            procs.append(host.popen(
                ["python3", "app/client.py", "--server-ip", SERVER_IP,
                 "--name", name, "--duration", str(args.duration)],
                stdout=devnull, stderr=subprocess.STDOUT))

        time.sleep(args.wait + 2)
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        net.stop()


if __name__ == "__main__":
    main()
