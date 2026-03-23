#!/usr/bin/env python3
"""
MeshCore Network Manager
========================
Unified text interface for mesh network topology discovery,
path optimization, and network management.

Workflows:
  1. Build topology    — add nodes, enter neighbors/traces
  2. Discover network  — progressive auto-discovery
  3. Find best path    — between any two repeaters
  4. Monitor network   — sweep traces, track link quality
  5. Manage config     — passwords, export, import

Requirements:
    pip install meshcore

Author: Stan (Gdańsk MeshCore Network)
License: MIT
"""

import asyncio
import os
import sys
import json
from datetime import datetime

from meshcore_optimizer.topology import (
    NetworkGraph, RepeaterNode, DirectedEdge, PathResult,
    widest_path, widest_path_alternatives, all_pairs_widest,
    print_topology_report, print_path_result, print_all_pairs_report,
)
from meshcore_optimizer.discovery import (
    RepeaterAccess, DEFAULT_GUEST_PASSWORDS,
    match_passwords, load_passwords, plan_discovery,
    Config, RadioConfig, load_config, save_config, connect_radio,
    progressive_discovery, DiscoveryState, state_file_for,
)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

CLEAR = "\033[2J\033[H"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
RESET = "\033[0m"
UNDERLINE = "\033[4m"


def c(text, color):
    return f"{color}{text}{RESET}"


def header(title, width=64):
    line = "═" * width
    print(f"\n  {c(line, DIM)}")
    print(f"  {c(title, BOLD + CYAN)}")
    print(f"  {c(line, DIM)}")


def subheader(title):
    print(f"\n  {c('─── ' + title + ' ───', DIM)}")


def status_icon(snr):
    if snr >= 5:
        return c("✅", GREEN)
    elif snr >= 0:
        return c("⚠️", YELLOW)
    else:
        return c("❌", RED)


def prompt(text=""):
    try:
        return input(f"  {c('›', CYAN)} {text}").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def menu_prompt(options, title=""):
    """Show numbered menu and return choice."""
    if title:
        print(f"\n  {c(title, BOLD)}")
    for i, (key, label) in enumerate(options):
        print(f"    {c(key, CYAN)}  {label}")
    print()
    return prompt()


def pause():
    input(f"  {c('Press Enter to continue...', DIM)}")


def confirm(question):
    resp = prompt(f"{question} (y/n): ")
    return resp.lower() in ('y', 'yes')


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class AppState:
    """Application state container."""

    def __init__(self):
        self.graph = NetworkGraph()
        self.passwords: list[RepeaterAccess] = []
        self.default_guest_pws: list[str] = list(DEFAULT_GUEST_PASSWORDS)
        self.companion_prefix: str = ""
        self.companion_name: str = ""
        self.topology_file: str = "topology.json"
        self.passwords_file: str = "passwords.json"
        self.config_file: str = "config.json"
        self.config: Config = Config()
        self.modified: bool = False

    @property
    def has_topology(self):
        return len(self.graph.nodes) > 0

    @property
    def has_companion(self):
        return bool(self.companion_prefix)

    def status_line(self):
        s = self.graph.stats()
        parts = [
            f"Nodes: {s['nodes']}",
            f"Edges: {s['edges']}",
        ]
        if self.companion_prefix:
            parts.append(f"Companion: {self.companion_name}")
        if self.modified:
            parts.append(c("(unsaved)", YELLOW))
        return "  │  ".join(parts)


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

def main_menu(state: AppState):
    """Top-level menu — flat, action-oriented."""
    while True:
        header("MESHCORE NETWORK MANAGER")
        print(f"  {state.status_line()}")

        # Build menu dynamically based on state
        options = []
        options.append(("d", "Discover network (auto-scan via radio)"))
        options.append(("f", "Find path between repeaters"))
        options.append(("w", "Web map (open in browser)"))
        options.append(("r", "Network report (topology, matrix, stats)"))
        options.append(("m", "Manual topology edit"))
        options.append(("s", "Settings (radio, companion, passwords)"))
        options.append(("q", "Exit"))

        choice = menu_prompt(options)

        if choice == "d":
            auto_discovery_menu(state)
        elif choice == "f":
            find_path_menu(state)
        elif choice == "w":
            launch_web_map(state)
        elif choice == "r":
            network_report_menu(state)
        elif choice == "m":
            build_topology_menu(state)
        elif choice == "s":
            settings_menu(state)
        elif choice in ("q", "quit", "exit"):
            if state.modified and confirm("Save before exit?"):
                save_topology(state)
            print(f"\n  {c('Bye! 73', GREEN)}\n")
            break


_map_server_url = None

def launch_web_map(state: AppState):
    global _map_server_url
    from meshcore_optimizer.web import start_map_server

    if _map_server_url:
        print(f"  Map already running: {c(_map_server_url, CYAN)}")
        pause()
        return

    # Save topology first so the map has fresh data
    if state.has_topology:
        state.graph.save(state.topology_file)

    _map_server_url = start_map_server(
        topology_file=state.topology_file,
        companion_prefix=state.companion_prefix,
        port=8080,
        config_file=state.config_file,
    )
    print(f"  Map started: {c(_map_server_url, CYAN)}")
    print(f"  Open this URL in a browser on any device on your network.")
    print(f"  Map auto-refreshes during discovery.")
    pause()


# ---------------------------------------------------------------------------
# 1. Build Topology
# ---------------------------------------------------------------------------

def build_topology_menu(state: AppState):
    while True:
        header("BUILD TOPOLOGY")
        print(f"  {state.status_line()}")

        choice = menu_prompt([
            ("a", "Add repeater node"),
            ("n", "Enter neighbors data (from login)"),
            ("t", "Enter single-hop trace (from mobile app)"),
            ("m", "Enter multi-hop trace"),
            ("e", "Add manual edge (known link)"),
            ("i", "Infer reverse edges"),
            ("l", "List all nodes"),
            ("b", "← Back"),
        ])

        if choice == "a":
            add_node_interactive(state)
        elif choice == "n":
            enter_neighbors(state)
        elif choice == "t":
            enter_trace(state)
        elif choice == "m":
            enter_multihop_trace(state)
        elif choice == "e":
            add_manual_edge(state)
        elif choice == "i":
            infer_edges(state)
        elif choice == "l":
            list_nodes(state)
        elif choice == "b":
            break


def add_node_interactive(state: AppState):
    subheader("Add Repeater Node")
    prefix = prompt("Prefix (8 hex chars): ").upper()
    if not prefix:
        return
    name = prompt("Name: ")
    if not name:
        return

    state.graph.add_node(RepeaterNode(prefix=prefix, name=name))
    state.modified = True
    print(f"  Added: [{c(prefix, CYAN)}] {name}")

    if confirm("Is this your companion's repeater?"):
        state.companion_prefix = prefix
        state.companion_name = name
        print(f"  Companion set to: {name}")


def enter_neighbors(state: AppState):
    subheader("Enter Neighbors Data")
    print(f"  Which repeater did you query?")
    list_nodes_short(state)
    target = prompt("Repeater name or prefix: ")
    node = state.graph.get_node(target)
    if not node:
        print(f"  {c('Node not found', RED)}")
        return

    # Show recommended path
    if state.has_companion:
        path_r = widest_path(state.graph, state.companion_prefix, node.prefix)
        if path_r.found:
            print(f"  Recommended path: {c(str(path_r), GREEN)}")

    print(f"\n  Paste neighbors output for {c(node.name, BOLD)}")
    print(f"  Format: PREFIX:timestamp:snr_x4")
    print(f"  {c('Empty line to finish', DIM)}")

    lines = []
    while True:
        line = prompt("│ ")
        if not line:
            break
        lines.append(line)

    if lines:
        text = "\n".join(lines)
        edges_before = state.graph.stats()['edges']
        state.graph.add_from_neighbors_output(node.prefix, text)
        edges_added = state.graph.stats()['edges'] - edges_before
        state.modified = True
        print(f"  +{c(str(edges_added), GREEN)} edges added")


def enter_trace(state: AppState):
    subheader("Enter Single-Hop Trace")

    if not state.has_companion:
        print(f"  {c('Set companion first (Settings menu)', RED)}")
        return

    print(f"  From: {c(state.companion_name, CYAN)}")
    list_nodes_short(state)
    target = prompt("Target repeater: ")
    node = state.graph.get_node(target)
    if not node:
        print(f"  {c('Node not found', RED)}")
        return

    print(f"\n  From trace result in mobile app:")
    print(f"    Upper value = forward SNR (how target heard us)")
    print(f"    Lower value = return SNR (how we heard target)")

    try:
        fwd = float(prompt("Forward SNR (upper, dB): "))
        ret = float(prompt("Return SNR (lower, dB): "))
    except ValueError:
        print(f"  {c('Invalid number', RED)}")
        return

    edges_before = state.graph.stats()['edges']
    state.graph.add_from_single_hop_trace(
        state.companion_prefix, node.prefix, fwd, ret)
    edges_added = state.graph.stats()['edges'] - edges_before
    state.modified = True

    print(f"\n  {state.companion_name} → {node.name}:")
    print(f"    Forward: {fwd:+.1f} dB {status_icon(fwd)}")
    print(f"    Return:  {ret:+.1f} dB {status_icon(ret)}")
    print(f"  +{c(str(edges_added), GREEN)} edges")


def enter_multihop_trace(state: AppState):
    subheader("Enter Multi-Hop Trace")

    if not state.has_companion:
        print(f"  {c('Set companion first', RED)}")
        return

    print(f"  From: {c(state.companion_name, CYAN)}")
    print(f"  Enter hop names comma-separated (e.g. MORENA,SUCHANINO)")
    list_nodes_short(state)

    path_input = prompt("Path: ")
    if not path_input:
        return

    names = [n.strip() for n in path_input.split(",")]
    nodes = []
    for name in names:
        node = state.graph.get_node(name)
        if not node:
            print(f"  {c(f'Node not found: {name}', RED)}")
            return
        nodes.append(node)

    print(f"\n  Path: {state.companion_name} → {' → '.join(n.name for n in nodes)}")
    print(f"  Enter forward SNR for each hop (comma-separated):")

    try:
        fwd_input = prompt("Forward SNRs: ")
        fwd_snrs = [float(x.strip()) for x in fwd_input.split(",")]

        ret_input = prompt("Return SNRs: ")
        ret_snrs = [float(x.strip()) for x in ret_input.split(",")]
    except ValueError:
        print(f"  {c('Invalid numbers', RED)}")
        return

    edges_before = state.graph.stats()['edges']
    state.graph.add_from_multihop_trace(
        state.companion_prefix,
        [n.prefix for n in nodes],
        fwd_snrs, ret_snrs,
    )
    edges_added = state.graph.stats()['edges'] - edges_before
    state.modified = True
    print(f"  +{c(str(edges_added), GREEN)} edges")


def add_manual_edge(state: AppState):
    subheader("Add Manual Edge")
    list_nodes_short(state)

    from_name = prompt("From node: ")
    to_name = prompt("To node: ")
    try:
        snr = float(prompt("SNR (dB): "))
    except ValueError:
        print(f"  {c('Invalid number', RED)}")
        return

    bidi = confirm("Bidirectional (same SNR both ways)?")
    state.graph.add_manual_edge(from_name, to_name, snr, bidirectional=bidi)
    state.modified = True
    direction = "↔" if bidi else "→"
    print(f"  Added: {from_name} {direction} {to_name} at {snr:+.1f} dB")


def infer_edges(state: AppState):
    subheader("Infer Reverse Edges")
    print(f"  For one-way links, estimate the reverse direction")
    print(f"  with a configurable SNR penalty.")
    try:
        penalty = float(prompt("Penalty (dB, default 5.0): ") or "5.0")
    except ValueError:
        penalty = 5.0

    state.graph.infer_reverse_edges(penalty)
    state.modified = True


def list_nodes(state: AppState):
    subheader("All Nodes")
    if not state.graph.nodes:
        print(f"  {c('No nodes. Add some first.', DIM)}")
        return

    for prefix in sorted(state.graph.nodes, key=lambda p: state.graph.nodes[p].name):
        node = state.graph.nodes[prefix]
        outgoing = len(state.graph.edges.get(prefix, []))
        incoming = len(state.graph.reverse_edges.get(prefix, []))
        comp = " ← COMPANION" if prefix == state.companion_prefix else ""
        print(f"    [{c(prefix, CYAN)}] {node.name:<25} "
              f"out:{outgoing} in:{incoming}{c(comp, GREEN)}")
    pause()


def list_nodes_short(state: AppState):
    if state.graph.nodes:
        names = [f"{n.name}" for n in
                 sorted(state.graph.nodes.values(), key=lambda n: n.name)]
        print(f"  {c('Known:', DIM)} {', '.join(names)}")


# ---------------------------------------------------------------------------
# 2. Find Path
# ---------------------------------------------------------------------------

def find_path_menu(state: AppState):
    if not state.has_topology:
        print(f"  {c('No topology data. Run discovery or add nodes first.', RED)}")
        pause()
        return

    while True:
        header("FIND PATH")

        choice = menu_prompt([
            ("p", "Path between two repeaters"),
            ("a", "All-pairs bottleneck matrix"),
            ("b", "Back"),
        ])

        if choice == "p":
            find_path_ab(state)
        elif choice == "a":
            results = all_pairs_widest(state.graph)
            print_all_pairs_report(results, state.graph)
            pause()
        elif choice == "b":
            break


def find_path_ab(state: AppState):
    subheader("Find Path: A → B")
    list_nodes_short(state)

    default_from = state.companion_name if state.has_companion else ""
    hint = f" (Enter={default_from})" if default_from else ""
    from_name = prompt(f"From{hint}: ")
    if not from_name and default_from:
        src = state.graph.get_node(state.companion_prefix)
    else:
        src = state.graph.get_node(from_name)
    if not src:
        print(f"  {c('Node not found', RED)}")
        return

    to_name = prompt("To: ")
    dst = state.graph.get_node(to_name)
    if not dst:
        print(f"  {c('Node not found', RED)}")
        return

    # Offer health-aware routing if any nodes have status data
    use_health = False
    health_nodes = [n for n in state.graph.nodes.values() if n.status]
    if health_nodes:
        use_health = confirm("Factor in node health?")

    alternatives = widest_path_alternatives(state.graph, src.prefix,
                                             dst.prefix, k=3,
                                             use_node_health=use_health)
    if not alternatives:
        print(f"\n  {c('No path found.', RED)}")
        pause()
        return

    result = alternatives[0]
    print_path_result(result, state.graph)

    if use_health:
        # Show if health changed the path vs SNR-only
        snr_only = widest_path_alternatives(state.graph, src.prefix,
                                             dst.prefix, k=1)
        if snr_only and snr_only[0].path != result.path:
            print(f"\n  {c('Note: Health penalties changed the optimal path', YELLOW)}")
            print(f"  SNR-only path: {' -> '.join(snr_only[0].path_names)}  "
                  f"({snr_only[0].bottleneck_snr:+.1f} dB)")

    # Show alternative forward paths
    for i, alt in enumerate(alternatives[1:], 2):
        print(f"\n  {c(f'Alternative {i}:', DIM)}")
        print_path_result(alt, state.graph)

    # Reverse paths (calculated independently — may use different intermediates)
    rev_alternatives = widest_path_alternatives(state.graph, dst.prefix,
                                                src.prefix, k=3,
                                                use_node_health=use_health)
    if rev_alternatives:
        print(f"\n  {c('Reverse path:', DIM)}")
        print_path_result(rev_alternatives[0], state.graph)

        for i, alt in enumerate(rev_alternatives[1:], 2):
            print(f"\n  {c(f'Reverse alternative {i}:', DIM)}")
            print_path_result(alt, state.graph)

        if rev_alternatives[0].bottleneck_snr != result.bottleneck_snr:
            print(f"\n  ⚠  Asymmetric! Forward bottleneck: "
                  f"{result.bottleneck_snr:+.1f} dB, "
                  f"Reverse: {rev_alternatives[0].bottleneck_snr:+.1f} dB")
    else:
        print(f"\n  {c('⚠  No reverse path found!', RED)}")

    # Show meshcore-cli command for best path
    if result.hop_count > 0:
        intermediate = result.path[1:-1]
        if intermediate:
            ids_str = " ".join(intermediate)
            print(f"\n  {c('meshcore-cli command:', BOLD)}")
            print(f"    to {dst.name}")
            print(f"    set_path {ids_str}")

    pause()


# ---------------------------------------------------------------------------
# 3. Network Overview
# ---------------------------------------------------------------------------

def network_report_menu(state: AppState):
    if not state.has_topology:
        print(f"  {c('No topology data.', RED)}")
        pause()
        return

    while True:
        header("NETWORK REPORT")

        choice = menu_prompt([
            ("t", "Full topology (nodes + edges)"),
            ("m", "All-pairs bottleneck matrix"),
            ("s", "Statistics summary"),
            ("w", "Weak links"),
            ("h", "Node health report"),
            ("b", "Back"),
        ])

        if choice == "t":
            print_topology_report(state.graph)
            pause()
        elif choice == "m":
            results = all_pairs_widest(state.graph)
            print_all_pairs_report(results, state.graph)
            pause()
        elif choice == "s":
            show_statistics(state)
        elif choice == "w":
            weak_links_report(state)
        elif choice == "h":
            health_report(state)
        elif choice == "b":
            break


def show_statistics(state: AppState):
    subheader("Network Statistics")
    s = state.graph.stats()

    print(f"  Nodes:           {s['nodes']}")
    print(f"  Directed edges:  {s['edges']}")
    print(f"  Edge pairs:      {s['edge_pairs']}")
    print()
    if s['edges'] > 0:
        print(f"  SNR range:       {s['min_snr']:+.1f} to {s['max_snr']:+.1f} dB")
        print(f"  Average SNR:     {s['avg_snr']:+.1f} dB")

    # Edge source breakdown
    sources = {}
    for edges in state.graph.edges.values():
        for e in edges:
            sources[e.source] = sources.get(e.source, 0) + 1
    if sources:
        print(f"\n  Edge data sources:")
        for src, count in sorted(sources.items(), key=lambda x: -x[1]):
            print(f"    {src:<15} {count} edges")

    # Connectivity
    if state.has_companion:
        reachable = 0
        unreachable = 0
        for prefix in state.graph.nodes:
            if prefix == state.companion_prefix:
                continue
            r = widest_path(state.graph, state.companion_prefix, prefix)
            if r.found:
                reachable += 1
            else:
                unreachable += 1
        print(f"\n  From companion ({state.companion_name}):")
        print(f"    Reachable:   {c(str(reachable), GREEN)}")
        print(f"    Unreachable: {c(str(unreachable), RED) if unreachable else '0'}")

    pause()


def weak_links_report(state: AppState):
    subheader("Weak Links Report")
    print(f"  Links below 0 dB SNR (working but fragile):")
    print()

    weak = []
    for from_p, edges in state.graph.edges.items():
        for e in edges:
            if e.snr_db < 0:
                from_name = state.graph.nodes[e.from_prefix].name if e.from_prefix in state.graph.nodes else e.from_prefix
                to_name = state.graph.nodes[e.to_prefix].name if e.to_prefix in state.graph.nodes else e.to_prefix
                weak.append((e.snr_db, from_name, to_name, e.source))

    weak.sort(key=lambda x: x[0])

    if not weak:
        print(f"  {c('All links are positive SNR!', GREEN)}")
    else:
        for snr, fn, tn, src in weak:
            icon = "🔴" if snr < -10 else "🟡"
            print(f"  {icon} {snr:>+6.1f} dB  {fn} → {tn}  [{src}]")

        critical = sum(1 for s, _, _, _ in weak if s < -10)
        marginal = sum(1 for s, _, _, _ in weak if -10 <= s < 0)
        print(f"\n  Critical (<-10 dB): {critical}")
        print(f"  Marginal (0 to -10 dB): {marginal}")

    pause()


def health_report(state: AppState):
    subheader("Node Health Report")

    nodes_with_status = [
        n for n in state.graph.nodes.values() if n.status
    ]

    if not nodes_with_status:
        print(f"  {c('No status data. Run discovery to collect.', DIM)}")
        pause()
        return

    # Worst health first
    nodes_with_status.sort(key=lambda n: n.health_penalty, reverse=True)

    print(f"  {'Node':<22} {'Bat':>6} {'TxQ':>4} {'Full':>5} "
          f"{'DupRate':>7} {'Uptime':>8} {'Penalty':>7}")
    print(f"  {'─' * 62}")

    for node in nodes_with_status:
        s = node.status
        bat = s.get('bat', 0)
        tx_q = s.get('tx_queue_len', 0)
        full = s.get('full_evts', 0)
        recv_flood = s.get('recv_flood', 0)
        flood_dups = s.get('flood_dups', 0)
        dup_rate = flood_dups / recv_flood if recv_flood > 0 else 0
        uptime_h = s.get('uptime', 0) / 3600
        penalty = node.health_penalty

        if penalty >= 4.0:
            icon = c("!!", RED)
        elif penalty > 0:
            icon = c("! ", YELLOW)
        else:
            icon = c("OK", GREEN)

        bat_str = f"{bat}mV"
        if bat < 3300:
            bat_str = c(bat_str, RED)
        elif bat < 3500:
            bat_str = c(bat_str, YELLOW)

        print(f"  {icon} {node.name:<20} {bat_str:>6} {tx_q:>4} {full:>5} "
              f"{dup_rate:>6.0%} {uptime_h:>7.1f}h "
              f"{penalty:>+6.1f}dB")

    print(f"\n  {len(nodes_with_status)} nodes with status data "
          f"(out of {len(state.graph.nodes)} total)")

    timestamps = [n.status_timestamp for n in nodes_with_status
                  if n.status_timestamp]
    if timestamps:
        print(f"  Oldest status: {min(timestamps)}")

    pause()


# ---------------------------------------------------------------------------
# 4. Auto-Discovery (placeholder for live radio)
# ---------------------------------------------------------------------------

def auto_discovery_menu(state: AppState):
    import asyncio

    while True:
        header("DISCOVERY")

        # Show current state
        radio = state.config.radio
        if radio.protocol == "tcp" and radio.host:
            conn_str = f"TCP {radio.host}:{radio.port}"
        elif radio.protocol == "serial" and radio.serial_port:
            conn_str = f"Serial {radio.serial_port} @ {radio.baudrate}"
        elif radio.protocol == "ble":
            conn_str = f"BLE {radio.ble_address or '(scan)'}"
        else:
            conn_str = c("NOT CONFIGURED", RED)

        print(f"  Radio:     {c(conn_str, CYAN)}")
        print(f"  Companion: {c(state.companion_name or 'not set', CYAN)}")
        disc = state.config
        print(f"  Rounds: {disc.discovery_max_rounds}  "
              f"Timeout: {disc.discovery_timeout}s  "
              f"Delay: {disc.discovery_delay}s")

        choice = menu_prompt([
            ("r", "Run discovery"),
            ("p", "Preview plan (dry run)"),
            ("t", "Test radio connection"),
            ("b", "Back"),
        ])

        if choice == "r":
            run_live_discovery(state)
        elif choice == "p":
            if not state.has_companion:
                print(f"  {c('Set companion first (Settings)', RED)}")
                pause()
                continue
            plan_discovery(state.graph, state.companion_prefix,
                           state.passwords, state.default_guest_pws)
            pause()
        elif choice == "t":
            test_radio_connection(state)
        elif choice == "b":
            break


def test_radio_connection(state: AppState):
    """Test that we can connect to the radio."""
    import asyncio

    radio = state.config.radio
    if not radio.host and not radio.serial_port and radio.protocol != "ble":
        print(f"  {c('No radio configured. Use Settings to configure.', RED)}")
        pause()
        return

    subheader("Testing Radio Connection")

    async def _test():
        try:
            mc = await connect_radio(radio)
            print(f"  {c('Connected!', GREEN)}")

            # Try to get self info / contacts
            try:
                result = await mc.commands.get_contacts()
                contacts = result.payload
                if isinstance(contacts, (dict, list)):
                    n = len(contacts)
                    print(f"  Contacts: {n}")

                    # Count repeaters
                    items = contacts.items() if isinstance(contacts, dict) else enumerate(contacts)
                    repeaters = []
                    for key, contact in items:
                        if not isinstance(contact, dict):
                            continue
                        ctype = contact.get('type', 0)
                        if ctype == 2:
                            name = contact.get('adv_name', '') or f"[{key}]"
                            pub_key = contact.get('public_key', '')
                            if isinstance(pub_key, bytes):
                                pub_key = pub_key.hex()
                            prefix = pub_key[:8].upper()
                            repeaters.append((prefix, name))

                    if repeaters:
                        print(f"  Repeaters found: {len(repeaters)}")
                        for prefix, name in repeaters:
                            comp = " ← COMPANION" if prefix == state.companion_prefix else ""
                            print(f"    [{c(prefix, CYAN)}] {name}{c(comp, GREEN)}")

                        # Offer to set companion if not set
                        if not state.has_companion and repeaters:
                            print()
                            if confirm("Set companion from these?"):
                                for i, (prefix, name) in enumerate(repeaters):
                                    print(f"    {i+1}. [{prefix}] {name}")
                                try:
                                    idx = int(prompt("Number: ")) - 1
                                    if 0 <= idx < len(repeaters):
                                        state.companion_prefix = repeaters[idx][0]
                                        state.companion_name = repeaters[idx][1]
                                        state.config.companion_prefix = repeaters[idx][0]
                                        print(f"  Companion set to: {c(repeaters[idx][1], GREEN)}")
                                except (ValueError, IndexError):
                                    pass
                    else:
                        print(f"  {c('No repeaters in contacts', YELLOW)}")
            except Exception as e:
                print(f"  Connected but could not query contacts: {e}")

            await mc.disconnect()
            print(f"  {c('Disconnected OK', GREEN)}")

        except Exception as e:
            print(f"  {c(f'Connection failed: {e}', RED)}")

    asyncio.run(_test())
    pause()


def run_live_discovery(state: AppState):
    """Run progressive discovery with live radio connection."""
    import asyncio

    # Validate prerequisites
    radio = state.config.radio
    if not radio.host and not radio.serial_port and radio.protocol != "ble":
        print(f"  {c('No radio configured. Use Settings → Radio.', RED)}")
        pause()
        return

    if not state.has_companion:
        print(f"  {c('Set companion first. Use Test Connection or Settings.', RED)}")
        pause()
        return

    subheader("Progressive Discovery")
    print(f"  Companion: {c(state.companion_name, CYAN)} [{state.companion_prefix}]")
    print(f"  Rounds: {state.config.discovery_max_rounds}  "
          f"Save to: {state.config.discovery_save_file}")

    # Check for resumable state
    sf = state_file_for(state.config.discovery_save_file)
    if os.path.exists(sf):
        try:
            prev = DiscoveryState.load(sf)
            if (prev.companion_prefix == state.companion_prefix
                    and not prev.completed):
                print(f"\n  {c('Previous discovery in progress:', YELLOW)}")
                print(f"    Round: {prev.current_round}  "
                      f"Traced: {len(prev.traced_set)}  "
                      f"Logged in: {len(prev.logged_in_set)}")
                choice = menu_prompt([
                    ("c", "Continue from where we left off"),
                    ("r", "Restart discovery from scratch"),
                    ("b", "Back"),
                ])
                if choice == "b":
                    return
                if choice == "r":
                    os.remove(sf)
                    print(f"  Starting fresh.")
                # choice == "c" — just proceed, progressive_discovery
                # will pick up the state file automatically
            else:
                os.remove(sf)
        except Exception:
            os.remove(sf)

    print(f"\n  Press Ctrl+C at any time to stop (progress is saved).\n")

    async def _run():
        try:
            mc = await connect_radio(radio)
        except Exception as e:
            print(f"  {c(f'Connection failed: {e}', RED)}")
            return

        try:
            await progressive_discovery(
                mc, state.graph,
                state.companion_prefix,
                state.passwords,
                max_rounds=state.config.discovery_max_rounds,
                timeout=state.config.discovery_timeout,
                delay=state.config.discovery_delay,
                infer_penalty=state.config.discovery_infer_penalty,
                save_file=state.config.discovery_save_file,
                default_guest_passwords=state.default_guest_pws,
                radio_config=state.config.radio,
                probe_distance_km=state.config.discovery_probe_distance_km,
                probe_min_snr=state.config.discovery_probe_min_snr,
                login_min_snr=state.config.discovery_login_min_snr,
                neighbor_max_age_h=state.config.discovery_neighbor_max_age_h,
            )
            state.modified = False  # saved by progressive_discovery
        except KeyboardInterrupt:
            print(f"\n  {c('Discovery interrupted — progress saved', YELLOW)}")
        except Exception as e:
            print(f"  {c(f'Discovery error: {e}', RED)}")
        finally:
            await mc.disconnect()
            print(f"  Radio disconnected.")

    asyncio.run(_run())
    pause()


def edit_discovery_params(state: AppState):
    """Inline editor for discovery parameters."""
    subheader("Discovery Parameters")
    disc = state.config
    print(f"  Current: {disc.discovery_max_rounds} rounds, "
          f"{disc.discovery_timeout}s timeout, "
          f"{disc.discovery_delay}s delay, "
          f"{disc.discovery_infer_penalty} dB penalty")
    print()
    try:
        v = prompt(f"  Max rounds ({disc.discovery_max_rounds}): ")
        if v:
            disc.discovery_max_rounds = int(v)
        v = prompt(f"  Timeout secs ({disc.discovery_timeout}): ")
        if v:
            disc.discovery_timeout = float(v)
        v = prompt(f"  Delay secs ({disc.discovery_delay}): ")
        if v:
            disc.discovery_delay = float(v)
        v = prompt(f"  Infer penalty dB ({disc.discovery_infer_penalty}): ")
        if v:
            disc.discovery_infer_penalty = float(v)
        v = prompt(f"  Login min SNR dB ({disc.discovery_login_min_snr}): ")
        if v:
            disc.discovery_login_min_snr = float(v)
        v = prompt(f"  Save file ({disc.discovery_save_file}): ")
        if v:
            disc.discovery_save_file = v
    except ValueError:
        print(f"  {c('Invalid input, keeping previous values', RED)}")
    print(f"  {c('Updated', GREEN)}")


def set_radio_connection(state: AppState):
    """Configure radio connection."""
    subheader("Radio Connection")

    choice = menu_prompt([
        ("t", f"TCP  {c('(current)' if state.config.radio.protocol == 'tcp' else '', DIM)}"),
        ("s", f"Serial  {c('(current)' if state.config.radio.protocol == 'serial' else '', DIM)}"),
        ("l", f"BLE  {c('(current)' if state.config.radio.protocol == 'ble' else '', DIM)}"),
    ])

    if choice == "t":
        state.config.radio.protocol = "tcp"
        host = prompt(f"Host ({state.config.radio.host or '192.168.1.24'}): ")
        if host:
            state.config.radio.host = host
        try:
            port = prompt(f"Port ({state.config.radio.port}): ")
            if port:
                state.config.radio.port = int(port)
        except ValueError:
            pass
        print(f"  Set: TCP {state.config.radio.host}:{state.config.radio.port}")

    elif choice == "s":
        state.config.radio.protocol = "serial"
        port = prompt(f"Serial port ({state.config.radio.serial_port or '/dev/ttyUSB0'}): ")
        if port:
            state.config.radio.serial_port = port
        try:
            baud = prompt(f"Baudrate ({state.config.radio.baudrate}): ")
            if baud:
                state.config.radio.baudrate = int(baud)
        except ValueError:
            pass
        print(f"  Set: Serial {state.config.radio.serial_port} @ {state.config.radio.baudrate}")

    elif choice == "l":
        state.config.radio.protocol = "ble"
        addr = prompt("BLE address (Enter to scan): ")
        state.config.radio.ble_address = addr
        print(f"  Set: BLE {addr or '(scan)'}")


def _save_config(state: AppState):
    """Save current config to file."""
    # Sync state back to config
    state.config.companion_prefix = state.companion_prefix
    state.config.passwords = state.passwords
    state.config.default_guest_passwords = state.default_guest_pws

    filename = prompt(f"Config file ({state.config_file}): ") or state.config_file
    try:
        save_config(state.config, filename)
        state.config_file = filename
        print(f"  {c(f'Saved config to {filename}', GREEN)}")
    except Exception as e:
        print(f"  {c(f'Error: {e}', RED)}")


# ---------------------------------------------------------------------------
# 5. Sweep Planner
# ---------------------------------------------------------------------------

def sweep_planner(state: AppState):
    header("TRACE SWEEP PLANNER")

    if not state.has_companion:
        print(f"  {c('Set companion first (Settings menu)', RED)}")
        pause()
        return

    if not state.has_topology:
        print(f"  {c('No topology data.', RED)}")
        pause()
        return

    all_nodes = list(state.graph.nodes.values())
    comp = state.companion_prefix

    # Single-hop coverage
    subheader("SINGLE-HOP TRACES (from mobile app: Trace → select repeater)")
    print(f"  Test each repeater from {c(state.companion_name, CYAN)}:")
    print()

    todo_single = []
    done_single = []

    for node in sorted(all_nodes, key=lambda n: n.name):
        if node.prefix == comp:
            continue

        fwd = state.graph.get_edge(comp, node.prefix)
        rev = state.graph.get_edge(node.prefix, comp)
        have_fwd = fwd and fwd.source == "trace"
        have_rev = rev and rev.source == "trace"

        if have_fwd and have_rev:
            done_single.append(node)
            print(f"    {c('✅', GREEN)} {node.name:<25} "
                  f"fwd:{fwd.snr_db:+.1f} ret:{rev.snr_db:+.1f}")
        elif fwd or rev:
            todo_single.append(node)
            existing = fwd or rev
            print(f"    {c('⚠️', YELLOW)}  {node.name:<25} "
                  f"partial ({existing.source}: {existing.snr_db:+.1f})")
        else:
            todo_single.append(node)
            print(f"    {c('❌', RED)} {node.name:<25} "
                  f"{c('NO DATA — trace this!', YELLOW)}")

    total = len(all_nodes) - 1
    done = len(done_single)
    print(f"\n  Coverage: {done}/{total} complete")

    # Two-hop: find missing inter-repeater links
    subheader("TWO-HOP TRACES (probe links between repeaters)")
    print(f"  These reveal links you can't see from companion:")
    print()

    interesting = []
    seen_pairs = set()

    for a in all_nodes:
        if a.prefix == comp:
            continue
        for b in all_nodes:
            if b.prefix == comp or b.prefix == a.prefix:
                continue

            pair = tuple(sorted([a.prefix, b.prefix]))
            if pair in seen_pairs:
                continue

            ab = state.graph.get_edge(a.prefix, b.prefix)
            ba = state.graph.get_edge(b.prefix, a.prefix)
            if not ab and not ba:
                seen_pairs.add(pair)
                path_a = widest_path(state.graph, comp, a.prefix)
                if path_a.found:
                    interesting.append((a, b))

    if interesting:
        for a, b in interesting[:10]:  # limit display
            print(f"    Trace: {state.companion_name} → "
                  f"{c(a.name, CYAN)} → {c(b.name, CYAN)}")
            print(f"      Would reveal: {a.name} ↔ {b.name}")
        if len(interesting) > 10:
            print(f"    ... and {len(interesting) - 10} more")
    else:
        print(f"    {c('No obvious gaps!', GREEN)}")

    # Quick-run suggestion
    if todo_single:
        subheader("SUGGESTED NEXT ACTIONS")
        print(f"  1. Open MeshCore mobile app → Tools → Trace Path")
        print(f"  2. Run single-hop trace to each of:")
        for node in todo_single[:5]:
            print(f"       → {c(node.name, CYAN)}")
        print(f"  3. Come back here, enter results:")
        print(f"       Menu 1 → option t (single-hop trace)")

    pause()


# ---------------------------------------------------------------------------
# 6. Settings
# ---------------------------------------------------------------------------

def settings_menu(state: AppState):
    while True:
        header("SETTINGS")

        # Show current state
        comp = state.companion_name or c("not set", RED)
        radio = state.config.radio
        if radio.protocol == "tcp" and radio.host:
            radio_str = f"TCP {radio.host}:{radio.port}"
        elif radio.protocol == "serial" and radio.serial_port:
            radio_str = f"Serial {radio.serial_port}"
        else:
            radio_str = c("not configured", RED)
        disc = state.config
        print(f"  Companion: {c(comp, CYAN)}")
        print(f"  Radio:     {radio_str}")
        print(f"  Passwords: {len(state.passwords)} entries, "
              f"defaults: {state.default_guest_pws}")
        print(f"  Discovery: {disc.discovery_max_rounds} rounds, "
              f"{disc.discovery_timeout}s timeout, "
              f"{disc.discovery_delay}s delay")
        print(f"  Topology:  {state.topology_file}")

        choice = menu_prompt([
            ("c", "Set companion"),
            ("r", "Radio connection"),
            ("p", "Passwords"),
            ("d", "Discovery parameters"),
            ("s", "Save topology"),
            ("l", "Load topology"),
            ("w", "Save config"),
            ("b", "Back"),
        ])

        if choice == "c":
            set_companion(state)
        elif choice == "r":
            set_radio_connection(state)
        elif choice == "p":
            manage_passwords(state)
        elif choice == "d":
            edit_discovery_params(state)
        elif choice == "s":
            save_topology(state)
        elif choice == "l":
            load_topology(state)
        elif choice == "w":
            _save_config(state)
        elif choice == "b":
            break


def set_companion(state: AppState):
    subheader("Set Companion's Repeater")
    _pick_companion(state)


def _set_companion_state(state: AppState, prefix, name):
    """Apply companion choice to state + config, save config."""
    state.companion_prefix = prefix
    state.companion_name = name
    state.config.companion_prefix = prefix
    if prefix not in state.graph.nodes:
        state.graph.add_node(RepeaterNode(
            prefix=prefix, name=name, access_level="admin"))
        state.modified = True
    save_config(state.config, state.config_file)
    print(f"  Companion set to: {c(name, GREEN)} [{prefix}]")


def _pick_companion(state: AppState):
    """Pick companion repeater — from radio list, graph, or manual entry."""
    # Try to fetch repeaters from radio
    radio = state.config.radio
    repeaters = []
    if radio.host or radio.serial_port or radio.protocol == "ble":
        print(f"\n  Connecting to radio to detect repeaters...")

        async def _fetch():
            try:
                mc = await connect_radio(radio)
                await mc.ensure_contacts(follow=True)
                for pub_key, contact in mc.contacts.items():
                    if not isinstance(contact, dict):
                        continue
                    if contact.get('type', 0) == 2:
                        prefix = pub_key[:8].upper()
                        name = contact.get('adv_name', '') or f"[{prefix}]"
                        repeaters.append((prefix, name))
                await mc.disconnect()
            except Exception as e:
                print(f"  {c(f'Could not connect: {e}', RED)}")

        asyncio.run(_fetch())

    if repeaters:
        repeaters.sort(key=lambda x: x[1])
        print(f"  Found {len(repeaters)} repeaters:")
        for i, (prefix, name) in enumerate(repeaters):
            print(f"    {i+1}. [{c(prefix, CYAN)}] {name}")
        print()
        entry = prompt("Enter number or prefix (e.g. 5364): ").strip()
        if not entry:
            return

        # Try as number first
        try:
            idx = int(entry) - 1
            if 0 <= idx < len(repeaters):
                _set_companion_state(state, *repeaters[idx])
                return
        except ValueError:
            pass

        # Try as prefix match
        entry_upper = entry.upper()
        matches = [(p, n) for p, n in repeaters if p.startswith(entry_upper)]
        if len(matches) == 1:
            _set_companion_state(state, *matches[0])
            return
        elif len(matches) > 1:
            print(f"  Multiple matches for '{entry}':")
            for i, (p, n) in enumerate(matches):
                print(f"    {i+1}. [{c(p, CYAN)}] {n}")
            try:
                idx = int(prompt("Select number: ")) - 1
                if 0 <= idx < len(matches):
                    _set_companion_state(state, *matches[idx])
                    return
            except (ValueError, IndexError):
                pass
            print(f"  {c('Invalid selection', RED)}")
            return

        msg = f'No repeater matches "{entry}"'
        print(f"  {c(msg, RED)}")
        return

    # No radio or no repeaters — check graph nodes
    if state.graph.nodes:
        list_nodes_short(state)
        entry = prompt("Enter name or prefix: ").strip()
        if not entry:
            return
        node = state.graph.get_node(entry)
        if not node:
            # Try prefix match
            entry_upper = entry.upper()
            for pfx, n in state.graph.nodes.items():
                if pfx.startswith(entry_upper):
                    node = n
                    break
        if node:
            _set_companion_state(state, node.prefix, node.name)
            return
        print(f"  {c(f'Node not found: {entry}', RED)}")
        return

    # Fully manual
    print(f"\n  No radio and no topology — enter prefix manually:")
    prefix = prompt("Repeater prefix (4+ hex chars): ").strip().upper()
    if prefix:
        _set_companion_state(state, prefix, f"[{prefix}]")


def manage_passwords(state: AppState):
    subheader("Password Manager")
    print(f"  Default guest passwords: "
          + ", ".join(f"'{p}'" if p else "(blank)" for p in state.default_guest_pws))
    print()

    if state.passwords:
        for i, pw in enumerate(state.passwords):
            pw_display = f"'{pw.password}'" if pw.password else "(blank)"
            target = pw.name or pw.prefix or "*"
            print(f"    {i+1}. {target:<25} {pw.level:<8} {pw_display}")
    else:
        print(f"  {c('No explicit passwords. Default guest passwords will be tried.', DIM)}")

    print()
    choice = menu_prompt([
        ("a", "Add password"),
        ("d", "Change default guest passwords"),
        ("r", "Remove password"),
        ("b", "← Back"),
    ])

    if choice == "a":
        list_nodes_short(state)
        target = prompt("Node name/prefix (or * for wildcard): ")
        level = prompt("Level (admin/guest): ") or "guest"
        password = prompt("Password (Enter for blank): ")

        prefix = ""
        name = target
        if target != "*":
            node = state.graph.get_node(target)
            if node:
                prefix = node.prefix
                name = node.name

        state.passwords.append(RepeaterAccess(
            prefix=prefix.upper(), level=level,
            password=password, name=name,
        ))
        print(f"  Added: {name} ({level})")

    elif choice == "d":
        print(f"  Enter passwords comma-separated (use 'blank' for empty):")
        pws_input = prompt("Passwords: ")
        if pws_input:
            pws = []
            for p in pws_input.split(","):
                p = p.strip()
                if p.lower() == "blank":
                    pws.append("")
                else:
                    pws.append(p)
            state.default_guest_pws = pws
            print(f"  Set: {pws}")

    elif choice == "r":
        try:
            idx = int(prompt("Number to remove: ")) - 1
            if 0 <= idx < len(state.passwords):
                removed = state.passwords.pop(idx)
                print(f"  Removed: {removed.name}")
        except (ValueError, IndexError):
            pass


def save_topology(state: AppState):
    filename = prompt(f"Filename ({state.topology_file}): ") or state.topology_file
    state.graph.save(filename)
    state.topology_file = filename
    state.modified = False
    print(f"  {c(f'Saved to {filename}', GREEN)}")


def load_topology(state: AppState):
    filename = prompt(f"Filename ({state.topology_file}): ") or state.topology_file
    try:
        state.graph = NetworkGraph.load(filename)
        state.topology_file = filename
        state.modified = False
        s = state.graph.stats()
        n_nodes, n_edges = s["nodes"], s["edges"]
        print(f"  {c(f'Loaded: {n_nodes} nodes, {n_edges} edges', GREEN)}")

        # Try to restore companion
        if not state.has_companion and state.graph.nodes:
            print(f"  Set companion? (or Enter to skip)")
            list_nodes_short(state)
            target = prompt("Companion: ")
            if target:
                node = state.graph.get_node(target)
                if node:
                    state.companion_prefix = node.prefix
                    state.companion_name = node.name
    except Exception as e:
        print(f"  {c(f'Error: {e}', RED)}")


# ---------------------------------------------------------------------------
# Quick-start wizard
# ---------------------------------------------------------------------------

def quick_start(state: AppState):
    """First-run wizard — config already loaded by main()."""
    header("WELCOME TO MESHCORE NETWORK MANAGER")
    print(f"  Let's set up your network.\n")

    # Load existing topology
    if os.path.exists(state.topology_file):
        if confirm(f"Found {state.topology_file}. Load it?"):
            try:
                state.graph = NetworkGraph.load(state.topology_file)
                s = state.graph.stats()
                n_nodes, n_edges = s['nodes'], s['edges']
                print(f"  {c(f'Loaded: {n_nodes} nodes, {n_edges} edges', GREEN)}")

                # Resolve companion from loaded graph
                if state.companion_prefix and state.companion_prefix not in state.graph.nodes:
                    for pfx, node in state.graph.nodes.items():
                        if pfx.startswith(state.companion_prefix):
                            state.companion_prefix = pfx
                            state.companion_name = node.name
                            state.config.companion_prefix = pfx
                            break
            except Exception:
                pass

    if os.path.exists("passwords.json") and not state.passwords:
        if confirm("Found passwords.json. Load it?"):
            try:
                state.passwords, state.default_guest_pws = load_passwords("passwords.json")
                print(f"  {c(f'Loaded {len(state.passwords)} passwords', GREEN)}")
            except Exception:
                pass

    if not state.has_companion:
        _pick_companion(state)

    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    state = AppState()

    # Parse arguments
    if len(sys.argv) > 1:
        for i, arg in enumerate(sys.argv[1:]):
            if arg in ("--config", "-C") and i + 1 < len(sys.argv) - 1:
                state.config_file = sys.argv[i + 2]
            elif arg in ("--load", "-l") and i + 1 < len(sys.argv) - 1:
                filename = sys.argv[i + 2]
                try:
                    state.graph = NetworkGraph.load(filename)
                    state.topology_file = filename
                    s = state.graph.stats()
                    print(f"Loaded: {s['nodes']} nodes, {s['edges']} edges")
                except Exception as e:
                    print(f"Error loading {filename}: {e}")
            elif arg in ("--passwords", "-p") and i + 1 < len(sys.argv) - 1:
                filename = sys.argv[i + 2]
                try:
                    state.passwords, state.default_guest_pws = load_passwords(filename)
                except Exception:
                    pass
            elif arg in ("--companion", "-c") and i + 1 < len(sys.argv) - 1:
                state.companion_prefix = sys.argv[i + 2].upper()
            elif arg in ("--help", "-h"):
                print("MeshCore Network Manager")
                print("Usage: python -m meshcore_optimizer.manager [options]")
                print("  --config FILE     Load config (default: config.json)")
                print("  --load FILE       Load topology")
                print("  --passwords FILE  Load passwords")
                print("  --companion PREFIX Set companion")
                return

    # Load config file
    if os.path.exists(state.config_file):
        try:
            state.config = load_config(state.config_file)
            # Apply config to state (CLI args override)
            if not state.companion_prefix and state.config.companion_prefix:
                state.companion_prefix = state.config.companion_prefix
            if not state.passwords and state.config.passwords:
                state.passwords = state.config.passwords
            if state.config.default_guest_passwords:
                state.default_guest_pws = state.config.default_guest_passwords
            if state.config.discovery_save_file:
                state.topology_file = state.config.discovery_save_file
        except Exception as e:
            print(f"Warning: could not load {state.config_file}: {e}")

    # Resolve companion prefix (short → full) and name from graph
    if state.companion_prefix:
        cp = state.companion_prefix
        if cp in state.graph.nodes:
            state.companion_name = state.graph.nodes[cp].name
        else:
            # Short prefix match (e.g. "5364" → "53649FDE")
            for pfx, node in state.graph.nodes.items():
                if pfx.startswith(cp):
                    state.companion_prefix = pfx
                    state.companion_name = node.name
                    break

    # Quick-start if no data
    if not state.has_topology:
        quick_start(state)

    main_menu(state)


if __name__ == "__main__":
    main()
