# Distributed Log Aggregation System (UDP + SDN)

SDN-Socket Programming Jackfruit mini project. Several hosts send application
logs over UDP to a central log server through a Mininet network controlled by
a Ryu (OpenFlow 1.3) controller. A bottleneck link is congested on purpose, and
the controller prioritizes critical logs. We compare three modes:
`baseline` (no priority), `static` (always-on priority) and `dynamic`
(priority switched on only when congestion is detected).

## Layout

```
topology/topo.py              Mininet topology + OVS queues on the bottleneck   (Person 1)
controller/log_controller.py  Ryu controller: baseline / static / dynamic modes (Person 1)
app/client.py                 UDP log client (timestamps, severities)           (Person 2)
app/server.py                 Concurrent UDP log server                         (Person 2)
app/analyze.py                Loss / delay per severity, summary.csv            (Person 2)
results/                      CSV outputs of experiments
```

## Topology

```
h1 --\
h2 ---\
h3 ----- s1 ==== bottleneck 2 Mbps ==== s2 --- h4 (log server, 10.0.0.4)
h5 ---/   (h5 = background congestion)
```

UDP ports: 5001 = critical logs (ERROR, CRITICAL), 5002 = normal logs
(INFO, WARNING), 5003 = background iperf traffic.

## Requirements

Ubuntu VM with Mininet, Open vSwitch, iperf, and Ryu 4.34 in a Python 3.9
virtual environment at `~/ryu39`. The app scripts use only the Python standard library.

## How to run one experiment

Always run from the repository root.

**Terminal 1: controller** (choose the mode)
```
source ~/ryu39/bin/activate
LOG_MODE=baseline ryu-manager controller/log_controller.py
```
(`LOG_MODE` can be `baseline`, `static` or `dynamic`.)

**Terminal 2: network**
```
sudo python3 topology/topo.py
```

**At the `mininet>` prompt**
```
pingall
h4 python3 app/server.py --duration 45 > results/server.out 2>&1 &
h4 iperf -u -s -p 5003 > /dev/null 2>&1 &
h5 iperf -u -c 10.0.0.4 -p 5003 -b 3M -t 30 > /dev/null 2>&1 &
h1 python3 app/client.py --server-ip 10.0.0.4 --name h1 --duration 30 &
h2 python3 app/client.py --server-ip 10.0.0.4 --name h2 --duration 30 &
h3 python3 app/client.py --server-ip 10.0.0.4 --name h3 --duration 30 &
```
Leave out the two `iperf` lines for a run without congestion.
Wait about 45 seconds, then `exit`.

**Analyze**
```
sudo chown -R $USER results
python3 app/analyze.py --label baseline_heavy
```

Between runs: `sudo mn -c`, delete `results/sent_*.csv` and `results/received.csv`,
and restart the controller with the next `LOG_MODE`.

## Checking the queues

```
sudo ovs-vsctl list qos
sudo tc -s class show dev s1-eth5
```

## Experiments

| Condition | Background traffic |
|---|---|
| none | no iperf |
| moderate | `iperf -b 1500K` |
| heavy | `iperf -b 3M` |

Each condition is run in `baseline`, `static` and `dynamic` mode.
Metrics: packet loss % and delivery delay per severity, plus bottleneck
utilization from `results/port_stats.csv`.
