#!/usr/bin/env python3
"""
Mininet topology for the Distributed Log Aggregation project.

    h1 --\
    h2 ---\
    h3 ----- s1 ==== (bottleneck, 2 Mbps) ==== s2 --- h4 (log server)
    h5 ---/   (h5 = congestion generator)

Run from the repo root (so relative paths like app/server.py work):
    sudo python3 topology/topo.py

The controller (Ryu) must already be running on 127.0.0.1:6653.

NOTE: the port numbers and the bottleneck rate below are also used in
controller/log_controller.py. If you change one, change the other.
"""
import argparse
import subprocess
from functools import partial

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController

BOTTLENECK_MBPS = 2          # bottleneck capacity (s1 -> s2)
S1_BOTTLENECK_PORT = 5       # s1 port facing s2
S2_BOTTLENECK_PORT = 2       # s2 port facing s1
HOST_LINK_MBPS = 100


def build_network(controller_ip, controller_port):
    net = Mininet(
        controller=None,
        switch=partial(OVSSwitch, protocols="OpenFlow13"),
        link=TCLink,
        autoSetMacs=True,
    )

    net.addController("c0", controller=RemoteController,
                      ip=controller_ip, port=controller_port)

    # Hosts: h1-h3 log clients, h4 log server, h5 congestion generator
    h1 = net.addHost("h1", ip="10.0.0.1/24")
    h2 = net.addHost("h2", ip="10.0.0.2/24")
    h3 = net.addHost("h3", ip="10.0.0.3/24")
    h4 = net.addHost("h4", ip="10.0.0.4/24")
    h5 = net.addHost("h5", ip="10.0.0.5/24")

    # Fixed DPIDs so the controller can recognise the bottleneck switch
    s1 = net.addSwitch("s1", dpid="0000000000000001")
    s2 = net.addSwitch("s2", dpid="0000000000000002")

    # Host links (fast). Explicit port numbers keep the controller config stable.
    net.addLink(h1, s1, port2=1, bw=HOST_LINK_MBPS)
    net.addLink(h2, s1, port2=2, bw=HOST_LINK_MBPS)
    net.addLink(h3, s1, port2=3, bw=HOST_LINK_MBPS)
    net.addLink(h5, s1, port2=4, bw=HOST_LINK_MBPS)
    net.addLink(h4, s2, port2=1, bw=HOST_LINK_MBPS)

    # Bottleneck link. No tc parameters here on purpose: the rate limit is
    # applied by the OVS QoS on s1's port (see setup_bottleneck_queues),
    # because mixing Mininet's tc and OVS QoS on one port causes conflicts.
    bottleneck = net.addLink(s1, s2,
                             port1=S1_BOTTLENECK_PORT, port2=S2_BOTTLENECK_PORT)
    return net, bottleneck


def setup_bottleneck_queues(bottleneck):
    """Create the rate limit + 3 queues on the s1 -> s2 port.

    queue 1 = high priority (critical logs)
    queue 2 = low priority  (normal logs)
    queue 0 = default       (everything else, e.g. background iperf traffic)

    Both modes (baseline and priority) use this SAME bottleneck. The baseline
    simply never tells packets to use queue 1/2, so everything shares queue 0.
    """
    port = bottleneck.intf1.name            # e.g. s1-eth5
    rate = BOTTLENECK_MBPS * 1_000_000      # bits per second
    q0_min, q1_min, q2_min = 100_000, 1_000_000, 100_000

    cmd = (
        f"ovs-vsctl -- set port {port} qos=@newqos "
        f"-- --id=@newqos create qos type=linux-htb "
        f"other-config:max-rate={rate} "
        f"queues:0=@q0 queues:1=@q1 queues:2=@q2 "
        f"-- --id=@q0 create queue other-config:min-rate={q0_min} "
        f"other-config:max-rate={rate} other-config:priority=1 "
        f"-- --id=@q1 create queue other-config:min-rate={q1_min} "
        f"other-config:max-rate={rate} other-config:priority=0 "
        f"-- --id=@q2 create queue other-config:min-rate={q2_min} "
        f"other-config:max-rate={rate} other-config:priority=1"
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        info(f"*** WARNING: queue setup failed:\n{result.stderr}\n")
    else:
        info(f"*** Bottleneck queues configured on {port} "
             f"({BOTTLENECK_MBPS} Mbps)\n")


def main():
    parser = argparse.ArgumentParser(description="Log aggregation topology")
    parser.add_argument("--controller-ip", default="127.0.0.1")
    parser.add_argument("--controller-port", type=int, default=6653)
    parser.add_argument("--script", default=None)
    args = parser.parse_args()

    setLogLevel("info")
    net, bottleneck = build_network(args.controller_ip, args.controller_port)
    net.start()
    setup_bottleneck_queues(bottleneck)

    info("*** Network ready. Useful checks:\n"
         "    mininet> pingall\n"
         "    (other terminal) sudo ovs-vsctl list qos\n"
         "    (other terminal) sudo tc -s class show dev s1-eth5\n")
    CLI(net, script=args.script) if args.script else CLI(net)
    net.stop()


if __name__ == "__main__":
    main()
