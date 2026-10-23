"""
Ryu controller for the Distributed Log Aggregation project (OpenFlow 1.3).

It always behaves as a learning switch. On top of that, the mode decides
how critical logs are treated at the bottleneck (s1 port 5 -> s2):

  LOG_MODE=baseline  plain learning switch, no prioritisation (the comparison baseline)
  LOG_MODE=static    priority rules are installed once, always active
  LOG_MODE=dynamic   the controller polls the bottleneck port, detects congestion,
                     installs the priority rules, and removes them when it clears

Run from the repo root:
    source ~/ryu39/bin/activate
    LOG_MODE=dynamic ryu-manager controller/log_controller.py

Port statistics on the bottleneck are logged to results/port_stats.csv
(this is the "network-flow statistics" deliverable).

NOTE: constants below must match topology/topo.py.
"""
import csv
import os
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (CONFIG_DISPATCHER, DEAD_DISPATCHER,
                                    MAIN_DISPATCHER, set_ev_cls)
from ryu.lib import hub
from ryu.lib.packet import ether_types, ethernet, packet
from ryu.ofproto import ofproto_v1_3

# ---- must match topology/topo.py and the app ----
BOTTLENECK_DPID = 1          # s1
BOTTLENECK_PORT = 5          # s1 port facing s2
SERVER_IP = "10.0.0.4"
CRITICAL_UDP_PORT = 5001     # ERROR / CRITICAL logs
NORMAL_UDP_PORT = 5002       # INFO / WARNING logs
QUEUE_HIGH = 1
QUEUE_LOW = 2
LINK_MBPS = 2.0

# ---- dynamic mode tuning ----
POLL_INTERVAL = 0.5                  # seconds between port-stat requests
HIGH_MBPS = 0.7 * LINK_MBPS          # at/above this = congested
LOW_MBPS = 0.5 * LINK_MBPS           # at/below this = congestion over
HITS_TO_TRIGGER = 1                  # consecutive congested polls before reacting
CLEAR_AFTER = 3                      # consecutive calm polls before removing rules
PRIORITY_RULE_PRIO = 100             # above the learning-switch flows (priority 1)

VALID_MODES = ("baseline", "static", "dynamic")
STATS_FILE = os.environ.get("STATS_FILE", "results/port_stats.csv")


class LogController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        mode = os.environ.get("LOG_MODE", "baseline").lower()
        if mode not in VALID_MODES:
            self.logger.warning("Unknown LOG_MODE '%s', using baseline", mode)
            mode = "baseline"
        self.mode = mode
        self.mac_to_port = {}
        self.datapaths = {}
        self.prio_active = False
        self.hi_count = 0
        self.lo_count = 0
        self.last_tx = None          # (timestamp, tx_bytes)
        self.logger.info("LogController started in %s mode", self.mode)

        os.makedirs(os.path.dirname(STATS_FILE) or ".", exist_ok=True)
        if not os.path.exists(STATS_FILE):
            with open(STATS_FILE, "w", newline="") as f:
                csv.writer(f).writerow(
                    ["time", "mode", "tx_mbps", "congested", "priority_active"])
        self.monitor_thread = hub.spawn(self._monitor)

    # ------------------------------------------------------------------
    # Switch connection handling
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(dp.id, None)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _switch_features_handler(self, ev):
        dp = ev.msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser

        # table-miss: send unknown packets to the controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self._add_flow(dp, 0, match, actions)

        if self.mode == "static" and dp.id == BOTTLENECK_DPID:
            self._install_priority_rules(dp)
            self.prio_active = True

    # ------------------------------------------------------------------
    # Learning switch
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst, src = eth.dst, eth.src
        dpid = dp.id
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        out_port = self.mac_to_port[dpid].get(dst, ofp.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        if out_port != ofp.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            if msg.buffer_id != ofp.OFP_NO_BUFFER:
                self._add_flow(dp, 1, match, actions, msg.buffer_id)
                return
            self._add_flow(dp, 1, match, actions)

        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        dp.send_msg(out)

    def _add_flow(self, dp, priority, match, actions, buffer_id=None):
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=dp, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=dp, priority=priority,
                                    match=match, instructions=inst)
        dp.send_msg(mod)

    # ------------------------------------------------------------------
    # Priority rules (critical logs -> high queue, normal logs -> low queue)
    # ------------------------------------------------------------------
    def _priority_matches(self, parser):
        for udp_port, queue in ((CRITICAL_UDP_PORT, QUEUE_HIGH),
                                (NORMAL_UDP_PORT, QUEUE_LOW)):
            match = parser.OFPMatch(eth_type=0x0800, ipv4_dst=SERVER_IP,
                                    ip_proto=17, udp_dst=udp_port)
            yield match, queue

    def _install_priority_rules(self, dp):
        parser = dp.ofproto_parser
        for match, queue in self._priority_matches(parser):
            actions = [parser.OFPActionSetQueue(queue),
                       parser.OFPActionOutput(BOTTLENECK_PORT)]
            self._add_flow(dp, PRIORITY_RULE_PRIO, match, actions)
        self.logger.info("Priority rules INSTALLED (critical->queue %d, normal->queue %d)",
                         QUEUE_HIGH, QUEUE_LOW)

    def _remove_priority_rules(self, dp):
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        for match, _ in self._priority_matches(parser):
            mod = parser.OFPFlowMod(datapath=dp, command=ofp.OFPFC_DELETE_STRICT,
                                    priority=PRIORITY_RULE_PRIO, match=match,
                                    out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY)
            dp.send_msg(mod)
        self.logger.info("Priority rules REMOVED")

    # ------------------------------------------------------------------
    # Monitoring + dynamic reaction
    # ------------------------------------------------------------------
    def _monitor(self):
        while True:
            dp = self.datapaths.get(BOTTLENECK_DPID)
            if dp is not None:
                req = dp.ofproto_parser.OFPPortStatsRequest(dp, 0, BOTTLENECK_PORT)
                dp.send_msg(req)
            hub.sleep(POLL_INTERVAL)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        dp = ev.msg.datapath
        if dp.id != BOTTLENECK_DPID:
            return
        for stat in ev.msg.body:
            if stat.port_no != BOTTLENECK_PORT:
                continue
            now = time.time()
            if self.last_tx is not None:
                dt = now - self.last_tx[0]
                if dt > 0:
                    mbps = (stat.tx_bytes - self.last_tx[1]) * 8 / dt / 1e6
                    self._evaluate(dp, mbps)
            self.last_tx = (now, stat.tx_bytes)

    def _evaluate(self, dp, mbps):
        if self.mode == "dynamic":
            if not self.prio_active:
                self.hi_count = self.hi_count + 1 if mbps >= HIGH_MBPS else 0
                if self.hi_count >= HITS_TO_TRIGGER:
                    self.logger.info("Congestion detected (%.2f Mbps)", mbps)
                    self._install_priority_rules(dp)
                    self.prio_active = True
                    self.hi_count = 0
            else:
                self.lo_count = self.lo_count + 1 if mbps <= LOW_MBPS else 0
                if self.lo_count >= CLEAR_AFTER:
                    self.logger.info("Congestion cleared (%.2f Mbps)", mbps)
                    self._remove_priority_rules(dp)
                    self.prio_active = False
                    self.lo_count = 0

        with open(STATS_FILE, "a", newline="") as f:
            csv.writer(f).writerow([
                f"{time.time():.3f}", self.mode, f"{mbps:.3f}",
                int(mbps >= HIGH_MBPS), int(self.prio_active)])
