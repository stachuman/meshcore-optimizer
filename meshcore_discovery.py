#!/usr/bin/env python3
"""
MeshCore Progressive Topology Discovery
========================================
Discovers mesh network topology starting from the companion repeater
(the base repeater our client device is connected to):

  Round 0: Login to companion repeater, fetch its neighbor table (seeds graph)
  Rounds 1+:
    Phase 1 — Trace sweep: trace all reachable nodes (primary method)
    Phase 2 — Login bonus: login for full neighbor tables (richer data)
  Finally: Compute optimal routes via widest-path algorithm

Data collection methods (in order of data quality):
  - neighbors: guest login + fetch_all_neighbours (full neighbor table with SNR)
  - trace: send_trace through repeater (bidirectional SNR, no login needed)
  - inferred: reverse edges estimated with SNR penalty

Usage:
    python meshcore_discovery.py --config config.json
    python meshcore_discovery.py --interactive
    python meshcore_discovery.py --topology network.json --plan

Author: Stan (Gdańsk MeshCore Network)
License: MIT
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from meshcore_topology import (
    NetworkGraph, RepeaterNode, DirectedEdge, PathResult,
    widest_path, widest_path_alternatives,
    print_topology_report, print_path_result,
    print_all_pairs_report, all_pairs_widest,
)


# ---------------------------------------------------------------------------
# Password / access configuration
# ---------------------------------------------------------------------------

@dataclass
class RepeaterAccess:
    """Access credentials for a repeater."""
    prefix: str           # node prefix (or name for matching)
    level: str            # "admin", "guest"
    password: str         # password (empty string = blank password)
    name: str = ""        # optional name for matching


DEFAULT_GUEST_PASSWORDS = ["", "hello", "password"]


def load_passwords(filename: str) -> tuple[list[RepeaterAccess], list[str]]:
    """Load passwords from JSON file."""
    with open(filename) as f:
        data = json.load(f)

    entries = []
    for item in data.get("passwords", []):
        entries.append(RepeaterAccess(
            prefix=item.get("prefix", "").upper(),
            level=item.get("level", "guest"),
            password=item.get("password", ""),
            name=item.get("name", ""),
        ))

    guest_pws = data.get("default_guest_passwords", DEFAULT_GUEST_PASSWORDS)
    return entries, guest_pws


def match_passwords(node: RepeaterNode,
                    passwords: list[RepeaterAccess],
                    default_guest_passwords: list[str] = None
                    ) -> list[RepeaterAccess]:
    """
    Find matching password entries for a node, ordered by priority:
      1. Exact prefix match
      2. Name match
      3. Wildcard match
      4. Default guest passwords
    """
    if default_guest_passwords is None:
        default_guest_passwords = DEFAULT_GUEST_PASSWORDS

    results = []
    seen = set()

    for pw in passwords:
        if pw.prefix and pw.prefix == node.prefix and pw.password not in seen:
            results.append(pw)
            seen.add(pw.password)

    for pw in passwords:
        if pw.name and pw.name != "*" and pw.name.lower() in node.name.lower():
            if pw.password not in seen:
                results.append(pw)
                seen.add(pw.password)

    for pw in passwords:
        if pw.name == "*" and pw.password not in seen:
            results.append(pw)
            seen.add(pw.password)

    for gpw in default_guest_passwords:
        if gpw not in seen:
            results.append(RepeaterAccess(
                prefix=node.prefix, level="guest", password=gpw,
                name=f"default({'blank' if gpw == '' else gpw})",
            ))
            seen.add(gpw)

    return results


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------

@dataclass
class RadioConfig:
    """Radio connection configuration."""
    protocol: str = "tcp"
    host: str = ""
    port: int = 5000
    serial_port: str = ""
    baudrate: int = 115200
    ble_address: str = ""
    meshcore_cli: str = ""


@dataclass
class Config:
    """Full application configuration."""
    radio: RadioConfig = None
    companion_prefix: str = ""
    discovery_max_rounds: int = 5
    discovery_timeout: float = 30.0
    discovery_delay: float = 5.0
    discovery_infer_penalty: float = 5.0
    discovery_save_file: str = "topology.json"
    discovery_hop_penalty: float = 1.0
    discovery_probe_distance_km: float = 2.0
    discovery_probe_min_snr: float = -5.0
    passwords: list = None
    default_guest_passwords: list = None
    health_penalties: dict = None

    def __post_init__(self):
        if self.radio is None:
            self.radio = RadioConfig()
        if self.passwords is None:
            self.passwords = []
        if self.default_guest_passwords is None:
            self.default_guest_passwords = list(DEFAULT_GUEST_PASSWORDS)


def load_config(filename: str) -> Config:
    """Load configuration from JSON file."""
    with open(filename) as f:
        data = json.load(f)

    radio_data = data.get("radio", {})
    radio = RadioConfig(
        protocol=radio_data.get("protocol", "tcp"),
        host=radio_data.get("host", ""),
        port=radio_data.get("port", 5000),
        serial_port=radio_data.get("serial_port", ""),
        baudrate=radio_data.get("baudrate", 115200),
        ble_address=radio_data.get("ble_address", ""),
        meshcore_cli=radio_data.get("meshcore_cli", ""),
    )

    disc = data.get("discovery", {})
    pw_entries = []
    for item in data.get("passwords", []):
        pw_entries.append(RepeaterAccess(
            prefix=item.get("prefix", "").upper(),
            level=item.get("level", "guest"),
            password=item.get("password", ""),
            name=item.get("name", ""),
        ))

    health_penalties = data.get("health_penalties", None)

    # Apply health weights globally so RepeaterNode.health_penalty uses them
    from meshcore_topology import set_health_weights
    set_health_weights(health_penalties)

    return Config(
        radio=radio,
        companion_prefix=data.get("companion_prefix", "").upper(),
        discovery_max_rounds=disc.get("max_rounds", 5),
        discovery_timeout=disc.get("timeout", 30.0),
        discovery_delay=disc.get("delay", 5.0),
        discovery_infer_penalty=disc.get("infer_penalty", 5.0),
        discovery_save_file=disc.get("save_file", "topology.json"),
        discovery_hop_penalty=disc.get("hop_penalty", 1.0),
        discovery_probe_distance_km=disc.get("probe_distance_km", 2.0),
        discovery_probe_min_snr=disc.get("probe_min_snr", -5.0),
        passwords=pw_entries,
        default_guest_passwords=data.get("default_guest_passwords",
                                         DEFAULT_GUEST_PASSWORDS),
        health_penalties=health_penalties,
    )


def save_config(config: Config, filename: str):
    """Save configuration to JSON file."""
    radio = {"protocol": config.radio.protocol}
    if config.radio.host:
        radio["host"] = config.radio.host
        radio["port"] = config.radio.port
    if config.radio.serial_port:
        radio["serial_port"] = config.radio.serial_port
        radio["baudrate"] = config.radio.baudrate
    if config.radio.ble_address:
        radio["ble_address"] = config.radio.ble_address
    if config.radio.meshcore_cli:
        radio["meshcore_cli"] = config.radio.meshcore_cli

    data = {
        "radio": radio,
        "companion_prefix": config.companion_prefix,
        "discovery": {
            "max_rounds": config.discovery_max_rounds,
            "timeout": config.discovery_timeout,
            "delay": config.discovery_delay,
            "infer_penalty": config.discovery_infer_penalty,
            "save_file": config.discovery_save_file,
        },
        "passwords": [
            {"name": pw.name, "prefix": pw.prefix,
             "level": pw.level, "password": pw.password}
            for pw in config.passwords
        ],
        "default_guest_passwords": config.default_guest_passwords,
    }

    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)


async def connect_radio(config: RadioConfig):
    """Create a MeshCore connection from radio config."""
    from meshcore import MeshCore

    if config.protocol == "tcp":
        if not config.host:
            raise ValueError("TCP host not configured")
        print(f"  Connecting via TCP to {config.host}:{config.port}...")
        mc = await MeshCore.create_tcp(host=config.host, port=config.port)
    elif config.protocol == "serial":
        if not config.serial_port:
            raise ValueError("Serial port not configured")
        print(f"  Connecting via serial {config.serial_port}...")
        mc = await MeshCore.create_serial(
            port=config.serial_port, baudrate=config.baudrate)
    elif config.protocol == "ble":
        addr = config.ble_address or None
        print(f"  Connecting via BLE{' to ' + addr if addr else ''}...")
        mc = await MeshCore.create_ble(address=addr)
    else:
        raise ValueError(f"Unknown protocol: {config.protocol}")

    if mc is None:
        raise ConnectionError(f"Failed to connect via {config.protocol}")
    return mc


# ---------------------------------------------------------------------------
# Discovery state persistence
# ---------------------------------------------------------------------------

@dataclass
class DiscoveryState:
    """Persisted discovery progress — allows resume after stop/restart."""
    companion_prefix: str = ""
    traced_set: set = field(default_factory=set)
    logged_in_set: set = field(default_factory=set)
    current_round: int = 0
    completed: bool = False

    def save(self, filename: str):
        data = {
            "companion_prefix": self.companion_prefix,
            "traced": sorted(self.traced_set),
            "logged_in": sorted(self.logged_in_set),
            "current_round": self.current_round,
            "completed": self.completed,
            "timestamp": datetime.now().isoformat(timespec='seconds'),
        }
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, filename: str) -> 'DiscoveryState':
        with open(filename) as f:
            data = json.load(f)
        return cls(
            companion_prefix=data.get("companion_prefix", ""),
            traced_set=set(data.get("traced", [])),
            logged_in_set=set(data.get("logged_in", [])),
            current_round=data.get("current_round", 0),
            completed=data.get("completed", False),
        )


def state_file_for(save_file: str) -> str:
    """Derive discovery state filename from topology filename."""
    base, ext = os.path.splitext(save_file or "topology.json")
    return f"{base}_discovery_state{ext}"


# ---------------------------------------------------------------------------
# Shared radio helpers — used by discovery, web UI, and node commands
# ---------------------------------------------------------------------------

PATH_HASH_MODE = 1
HOP_HEX_LEN = 4  # (PATH_HASH_MODE + 1) * 2


def find_contact(mc, prefix):
    """Find a contact dict by node prefix in mc.contacts."""
    prefix = prefix.upper()
    for pub_key, ct in mc.contacts.items():
        if not isinstance(ct, dict):
            continue
        if pub_key[:8].upper() == prefix:
            return ct
    return None


async def set_contact_path(mc, contact, path_result):
    """Set routing path on a contact from a PathResult."""
    if not path_result.found:
        print(f"    Route: no path found")
        return

    # Log route with names and prefixes
    hops_display = " -> ".join(
        f"{n} [{p[:4]}]"
        for n, p in zip(path_result.path_names, path_result.path))
    print(f"    Route: {hops_display} "
          f"({path_result.bottleneck_snr:+.1f} dB, "
          f"{path_result.hop_count} hops)")

    if not contact or path_result.hop_count == 0:
        return

    hops = path_result.path[:-1]
    path_hex = "".join(p[:HOP_HEX_LEN].lower() for p in hops)
    try:
        await mc.commands.change_contact_path(
            contact, path_hex, path_hash_mode=PATH_HASH_MODE)
    except Exception as e:
        print(f"    Could not set path: {e}")


async def login_to_node(mc, contact, node_name, password, timeout,
                        max_wait=None):
    """
    Login to a repeater. Returns (success, error_msg).
    Handles subscribe-before-send pattern to avoid race conditions.
    max_wait caps the login response wait time (useful for interactive
    single-node commands where you don't want to wait 60s+).
    """
    from meshcore import EventType

    pw_display = f"'{password}'" if password else "(blank)"

    login_future = asyncio.Future()

    def _on_login(event):
        if not login_future.done():
            login_future.set_result(event)

    login_sub = mc.subscribe(EventType.LOGIN_SUCCESS, _on_login)

    try:
        print(f"      TX: Sending login ({pw_display}) to {node_name}...")
        login_result = await asyncio.wait_for(
            mc.commands.send_login(contact, password), timeout=timeout)
    except Exception as e:
        login_sub.unsubscribe()
        return False, f"login send error: {e}"

    if login_result.type == EventType.ERROR:
        login_sub.unsubscribe()
        reason = login_result.payload.get("reason", "")
        if reason == "no_event_received":
            return False, "CONNECTION_LOST"
        return False, f"login rejected ({pw_display})"

    # Wait for LOGIN_SUCCESS over the air
    # Divide by 800 (not 1000) to give ~25% extra margin over the
    # suggested timeout (which is in milliseconds).
    login_timeout = login_result.payload.get("suggested_timeout", 0) / 800
    if isinstance(contact, dict) and contact.get("timeout", 0) != 0:
        login_timeout = contact["timeout"]
    login_timeout = max(login_timeout, 10.0)
    if max_wait:
        login_timeout = min(login_timeout, max_wait)

    print(f"      ... waiting for login response "
          f"(timeout={login_timeout:.0f}s)...")
    try:
        login_event = await asyncio.wait_for(
            login_future, timeout=login_timeout)
    except asyncio.TimeoutError:
        login_event = None
    finally:
        login_sub.unsubscribe()

    if login_event is None:
        return False, f"login timeout ({pw_display})"
    if login_event.type == EventType.LOGIN_SUCCESS:
        print(f"      RX: Login SUCCESS with {pw_display}")
        return True, ""
    return False, f"login failed ({pw_display})"


async def fetch_status(mc, contact, node, timeout):
    """
    Fetch status from a logged-in node with one retry.
    Updates node.status and node.status_timestamp on success.
    Returns the status dict or None.
    """
    status_data = None
    for attempt in (1, 2):
        try:
            print(f"      TX: Requesting status from {node.name}"
                  f" (attempt {attempt}/2)...")
            status_data = await mc.commands.req_status_sync(
                contact, min_timeout=timeout)
            if status_data:
                break
            print(f"      RX: No status data")
        except Exception as e:
            print(f"      RX: Status error: {e}")
        if attempt < 2:
            await asyncio.sleep(2)

    if status_data:
        node.status = status_data
        node.status_timestamp = datetime.now().isoformat(timespec='seconds')
        bat = status_data.get('bat', 0)
        tx_q = status_data.get('tx_queue_len', 0)
        full = status_data.get('full_evts', 0)
        uptime_h = status_data.get('uptime', 0) / 3600
        print(f"      RX: Status — bat:{bat}mV  txq:{tx_q}  "
              f"full_evts:{full}  uptime:{uptime_h:.1f}h")

    return status_data


# ---------------------------------------------------------------------------
# Data collection helpers
# ---------------------------------------------------------------------------

@dataclass
class DiscoveryResult:
    """Result of one discovery round."""
    round_num: int
    attempted: int = 0
    new_edges: int = 0
    duration_secs: float = 0.0


async def _trace_repeater(mc, contact, companion_prefix, target_prefix,
                          graph, timeout, forced_trace_path=None):
    """
    Trace a repeater to get bidirectional SNR. No login needed.

    Builds round-trip trace using the graph's known route (NOT contact out_path,
    which may have been overwritten by change_contact_path).

    If forced_trace_path is provided (comma-separated hex hops), it's used
    instead of computing the path from the graph. Used by proximity probes
    to force a trace through specific intermediates.

    Returns (success, edges_added, error_msg).
    """
    from meshcore import EventType
    from meshcore_topology import widest_path

    ADDR_HEX = 4  # 2 bytes = 4 hex chars

    if forced_trace_path:
        trace_path = forced_trace_path
        print(f"      Forced path: {trace_path}")
        # Build a dummy path_result for hash resolution
        path_result = widest_path(graph, companion_prefix, target_prefix)
    else:
        # Get route from graph (widest path)
        path_result = widest_path(graph, companion_prefix, target_prefix)

        if path_result.found and len(path_result.path) >= 2:
            hops = [p[:ADDR_HEX].lower() for p in path_result.path]
        else:
            hops = [companion_prefix[:ADDR_HEX].lower(),
                    target_prefix[:ADDR_HEX].lower()]

        # Build round-trip
        trace_addrs = list(hops) + list(reversed(hops[:-1]))
        trace_path = ",".join(trace_addrs)

        if path_result.found:
            route = " -> ".join(path_result.path_names)
            print(f"      Route: {route}")

    try:
        start = time.monotonic()

        # Subscribe to TRACE_DATA BEFORE sending, to avoid race condition
        # where the response arrives before we start waiting.
        trace_queue = asyncio.Queue()

        def _on_trace(event):
            trace_queue.put_nowait(event)

        sub = mc.subscribe(EventType.TRACE_DATA, _on_trace)

        print(f"      TX: Sending trace (path={trace_path})...")
        try:
            res = await asyncio.wait_for(
                mc.commands.send_trace(path=trace_path),
                timeout=timeout
            )
        except Exception as e:
            sub.unsubscribe()
            raise

        if res is None or res.type == EventType.ERROR:
            sub.unsubscribe()
            err = res.payload if res else "None"
            print(f"      TX: Trace send failed: {err}")
            return False, 0, f"trace send failed: {err}"

        # Get expected tag to match response
        tag = res.payload.get("expected_ack")
        if isinstance(tag, (bytes, bytearray)):
            tag = int.from_bytes(tag, byteorder="little")

        suggested = res.payload.get("suggested_timeout", 10000)
        trace_timeout = min(suggested / 1000 * 1.2, timeout)
        print(f"      TX: Sent (tag={tag}, suggested={suggested}ms), "
              f"waiting for trace response (timeout={trace_timeout:.0f}s)...")

        # Wait for trace response, filtering by tag
        ev = None
        deadline = time.monotonic() + trace_timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                candidate = await asyncio.wait_for(
                    trace_queue.get(), timeout=remaining)
                if candidate.attributes.get("tag") == tag:
                    ev = candidate
                    break
            except asyncio.TimeoutError:
                break

        sub.unsubscribe()

        elapsed_ms = (time.monotonic() - start) * 1000

        if ev is None:
            print(f"      RX: No trace response (timeout)")
            return False, 0, "trace timeout"
        if ev.type == EventType.ERROR:
            print(f"      RX: Trace error: {ev.payload}")
            return False, 0, f"trace error: {ev.payload}"

        path_nodes = ev.payload.get("path", [])
        if not path_nodes:
            print(f"      RX: Trace OK (RTT {elapsed_ms:.0f}ms) but no path data")
            return True, 0, f"trace OK (RTT {elapsed_ms:.0f}ms) but no path data"

        snrs = [n["snr"] for n in path_nodes]
        print(f"      RX: Trace response — SNR: "
              f"{' -> '.join(f'{s:+.1f}' for s in snrs)}  "
              f"RTT: {elapsed_ms:.0f}ms")

        # Trace response structure:
        #   Each entry has {"hash": "XXXX", "snr": float} except the
        #   final entry (back at client) which has only {"snr": float}.
        #   SNR = how well THIS node heard the PREVIOUS sender.
        #
        # The trace travels: client → rpt0 → rpt1 → ... → rptN → client
        # For trace 5364,bbc9,5364 (companion_rpt=5364, target=bbc9):
        #   [0] hash=5364 snr=+12.2  → base_rpt heard client (skip: not mesh)
        #   [1] hash=bbc9 snr=-1.8   → bbc9 heard 5364 → edge: 5364→bbc9
        #   [2] hash=5364 snr=+11.2  → 5364 heard bbc9 → edge: bbc9→5364
        #   [3]           snr=+12.2  → client heard 5364 (skip: not mesh)
        #
        # Skip [0] (client→base_rpt) and [-1] (base_rpt→client) — these
        # are the local connection, not mesh radio links.
        # Only [1:-1] contain real mesh hop data.

        # Build deterministic hash→prefix map from the trace path we sent.
        # This avoids ambiguous resolution when multiple nodes share a
        # short prefix (e.g. two nodes both starting with "5364").
        hash_to_prefix = {}
        if path_result.found:
            for p in path_result.path:
                h = p[:ADDR_HEX].lower()
                hash_to_prefix[h] = p.upper()
        # Always map companion hash explicitly
        hash_to_prefix[companion_prefix[:ADDR_HEX].lower()] = companion_prefix

        def _resolve_hash(h):
            h_lower = h.lower()
            if h_lower in hash_to_prefix:
                return hash_to_prefix[h_lower]
            # Fallback: search graph nodes
            for pfx in graph.nodes:
                if pfx[:len(h)].lower() == h_lower:
                    return pfx
            return h.upper()

        edges_before = graph.stats()['edges']
        ts = datetime.now().isoformat(timespec='seconds')

        # Extract mesh edges from path_nodes[1:-1] (skip client↔rpt links)
        mesh_nodes = path_nodes[1:-1] if len(path_nodes) > 2 else []
        prev_node = companion_prefix  # after [0], we're at companion_rpt
        for node_data in mesh_nodes:
            snr = node_data["snr"]
            h = node_data.get("hash", "")
            receiver = _resolve_hash(h) if h else companion_prefix

            if prev_node and receiver and prev_node != receiver:
                graph.add_edge(DirectedEdge(
                    from_prefix=prev_node.upper(),
                    to_prefix=receiver.upper(),
                    snr_db=snr,
                    source="trace",
                    timestamp=ts,
                    confidence=1.0,
                ))
                from_node = graph.get_node(prev_node)
                to_node = graph.get_node(receiver)
                from_name = from_node.name if from_node else prev_node[:8]
                to_name = to_node.name if to_node else receiver[:8]
                print(f"        edge: {from_name} → {to_name}  "
                      f"{snr:+.1f} dB")

            prev_node = receiver

        return True, graph.stats()['edges'] - edges_before, ""

    except asyncio.TimeoutError:
        return False, 0, "trace timeout"
    except Exception as e:
        return False, 0, f"trace error: {e}"


async def _login_and_neighbors(mc, contact, node, password_entry,
                               graph, timeout, name_map=None,
                               contact_map=None,
                               max_login_wait=None):
    """
    Try guest login + fetch neighbor table via binary API.
    Returns (success, edges_added, error_msg, password_used).
    """
    pw = password_entry.password

    try:
        ok, err = await login_to_node(
            mc, contact, node.name, pw, timeout,
            max_wait=max_login_wait)
        if not ok:
            return False, 0, err, ""

        await fetch_status(mc, contact, node, timeout)

        # Fetch neighbors via binary API
        # Use min_timeout to ensure enough time for multi-hop round trips.
        # Retry up to 4 times — login already succeeded so retries are cheap.
        max_attempts = 4
        try:
            neighbours = None
            for attempt in range(1, max_attempts + 1):
                print(f"      TX: Requesting neighbor table from {node.name}"
                      f" (attempt {attempt}/{max_attempts})...")
                res = await mc.commands.fetch_all_neighbours(
                    contact, min_timeout=timeout)

                if res is not None:
                    neighbours = res.get("neighbours", [])
                    if neighbours:
                        break
                print(f"      RX: No neighbors (attempt {attempt}/{max_attempts})")
                if attempt < max_attempts:
                    await asyncio.sleep(3)

            if not neighbours:
                print(f"      RX: No neighbors after {max_attempts} attempts")
                return True, 0, f"no neighbors after {max_attempts} attempts", pw

            total = res.get("neighbours_count", len(neighbours))
            got = res.get("results_count", len(neighbours))
            print(f"      RX: Got {got}/{total} neighbors:")
            for n in neighbours:
                prefix = n["pubkey"][:8].upper()
                snr = n["snr"]
                secs = n.get("secs_ago", 0)

                # Try to resolve unknown prefixes via contact lookup
                if name_map is not None and prefix not in name_map:
                    ct = mc.get_contact_by_key_prefix(n["pubkey"])
                    if ct is not None:
                        name_map[prefix] = ct.get("adv_name", f"[{prefix}]")
                        if contact_map is not None and prefix not in contact_map:
                            contact_map[prefix] = ct

                existing = graph.get_node(prefix)
                name = (existing.name if existing and not existing.name.startswith("[")
                        else (name_map or {}).get(prefix, f"[{prefix}]"))
                print(f"        {name:<25} {snr:>+6.1f} dB  ({secs}s ago)")

            edges_before = graph.stats()['edges']
            graph.add_from_neighbors_api(
                node.prefix, neighbours,
                timestamp=datetime.now().isoformat(timespec='seconds')
            )
            n_edges = graph.stats()['edges'] - edges_before

            # Logout to clean up session on repeater
            try:
                await mc.commands.send_logout(contact)
            except Exception:
                pass

            return True, n_edges, "", pw

        except Exception as e:
            print(f"      RX: Neighbor request error: {e}")
            try:
                await mc.commands.send_logout(contact)
            except Exception:
                pass
            return True, 0, f"neighbors error: {e}", pw

    except asyncio.TimeoutError:
        return False, 0, f"login timeout ({pw_display})", ""


# ---------------------------------------------------------------------------
# Progressive discovery
# ---------------------------------------------------------------------------

async def _ensure_connected(mc, radio_config):
    """Check connection and reconnect if needed. Returns (mc, reconnected)."""
    if mc.is_connected:
        return mc, False

    print(f"\n  ⚠  Radio connection lost, reconnecting...")
    try:
        mc = await connect_radio(radio_config)
        await mc.ensure_contacts(follow=True)
        print(f"  ✓  Reconnected")
        return mc, True
    except Exception as e:
        print(f"  ✗  Reconnect failed: {e}")
        raise


class _DiscoveryCtx:
    """Shared context for discovery phases."""

    def __init__(self, mc, graph, companion_prefix, contact_map, name_map,
                 ds, passwords, default_guest_passwords, timeout, delay,
                 infer_penalty, radio_config, save_file, state_file,
                 alt_snr_gap=10.0, probe_distance_km=2.0,
                 probe_min_snr=-5.0):
        self.mc = mc
        self.graph = graph
        self.companion_prefix = companion_prefix
        self.contact_map = contact_map
        self.name_map = name_map
        self.ds = ds
        self.passwords = passwords
        self.default_guest_passwords = default_guest_passwords
        self.timeout = timeout
        self.delay = delay
        self.infer_penalty = infer_penalty
        self.radio_config = radio_config
        self.save_file = save_file
        self.state_file = state_file
        self.alt_snr_gap = alt_snr_gap
        self.probe_distance_km = probe_distance_km
        self.probe_min_snr = probe_min_snr

    def fix_names(self):
        """Update node names and locations from contact data."""
        for pfx, node_obj in self.graph.nodes.items():
            if pfx in self.name_map and node_obj.name.startswith("["):
                node_obj.name = self.name_map[pfx]
            ct = self.contact_map.get(pfx)
            if ct:
                lat = ct.get('adv_lat', 0.0)
                lon = ct.get('adv_lon', 0.0)
                if lat and lon and not (node_obj.lat and node_obj.lon):
                    node_obj.lat = lat
                    node_obj.lon = lon

    def save(self):
        if self.save_file:
            self.graph.save(self.save_file)
            self.ds.save(self.state_file)

    def save_and_report(self, reason=""):
        self.save()
        s = self.graph.stats()
        print(f"\n  Saved: {s['nodes']} nodes, {s['edges']} edges"
              + (f"  ({reason})" if reason else ""))

    async def ensure_connected(self):
        self.mc, _ = await _ensure_connected(self.mc, self.radio_config)

    async def set_contact_path(self, contact, path_result):
        """Set contact routing path. Requires contact dict."""
        await set_contact_path(self.mc, contact, path_result)

    def filter_alternatives(self, alt_paths):
        """Drop alternatives whose bottleneck is too far below primary."""
        if not alt_paths or self.alt_snr_gap <= 0:
            return alt_paths
        primary_snr = alt_paths[0].bottleneck_snr
        kept = []
        for i, p in enumerate(alt_paths):
            if i == 0 or p.bottleneck_snr >= primary_snr - self.alt_snr_gap:
                kept.append(p)
            else:
                print(f"    Skipping Alt {i}: "
                      f"{p.bottleneck_snr:+.1f} dB "
                      f"(too weak vs primary {primary_snr:+.1f} dB)")
        return kept

    async def refresh_contacts(self):
        """Re-fetch contacts — radio may have heard new advertisements."""
        try:
            await self.mc.ensure_contacts(follow=True)
            for pub_key, ct in self.mc.contacts.items():
                if not isinstance(ct, dict):
                    continue
                pfx = pub_key[:8].upper()
                if pfx and pfx not in self.contact_map:
                    self.contact_map[pfx] = ct
                    self.name_map[pfx] = ct.get('adv_name', '') or f"[{pfx}]"
                    print(f"  New contact: {self.name_map[pfx]} [{pfx}]")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Discovery phases
# ---------------------------------------------------------------------------

async def _run_round0(ctx: _DiscoveryCtx):
    """Round 0: Login to companion repeater, fetch neighbor table to seed graph."""
    print(f"\n  {'='*30}")
    print(f"  ROUND 0: Querying companion repeater [{ctx.companion_prefix}]")
    print(f"  {'='*30}")

    companion_contact = ctx.contact_map.get(ctx.companion_prefix)
    if not companion_contact:
        print(f"  ERROR: Companion repeater [{ctx.companion_prefix}] "
              f"not found in contacts!")
        print(f"  Discovery cannot proceed.")
        return 0

    comp_node = ctx.graph.get_node(ctx.companion_prefix)
    pw_list = match_passwords(
        comp_node, ctx.passwords, ctx.default_guest_passwords)

    round0_edges = 0
    for pw in pw_list:
        if ctx.radio_config:
            try:
                await ctx.ensure_connected()
            except Exception:
                break

        ok, n_edges, err, used_pw = await _login_and_neighbors(
            ctx.mc, companion_contact, comp_node, pw, ctx.graph,
            ctx.timeout, name_map=ctx.name_map,
            contact_map=ctx.contact_map)

        if err == "CONNECTION_LOST":
            if ctx.radio_config:
                try:
                    await ctx.ensure_connected()
                except Exception:
                    break
            continue

        if ok:
            round0_edges = n_edges
            if n_edges > 0:
                print(f"    +{n_edges} neighbor edges "
                      f"from companion repeater")
            elif err:
                print(f"    Login OK but: {err}")
            break
        else:
            print(f"    {err}")

        await asyncio.sleep(ctx.delay)

    if round0_edges > 0:
        ctx.fix_names()
    ctx.graph.infer_reverse_edges(ctx.infer_penalty)

    print(f"\n  Round 0: companion [{ctx.companion_prefix}] → "
          f"+{round0_edges} edges")
    return round0_edges


async def _run_trace_phase(ctx: _DiscoveryCtx):
    """Phase 1: Trace all reachable nodes, best-first with alternative paths.

    Deprioritizes (but does not exclude) paths through repeatedly-failing
    intermediates so we try other routes first.
    """
    print(f"\n  Phase 1: Trace sweep")
    trace_count = 0
    first_trace = True
    intermediate_fails = {}  # prefix -> consecutive fail count

    def _effective_snr(pr):
        penalty = sum(intermediate_fails.get(p, 0) * 10
                      for p in pr.path[1:-1])
        return pr.bottleneck_snr - penalty

    while True:
        best_prefix = None
        best_score = -999
        best_path = None
        unreachable = 0

        for prefix in list(ctx.graph.nodes.keys()):
            if prefix in ctx.ds.traced_set:
                continue
            if prefix == ctx.companion_prefix:
                continue
            pr = widest_path(ctx.graph, ctx.companion_prefix, prefix)
            if not pr.found:
                unreachable += 1
                continue
            score = _effective_snr(pr)
            if score > best_score:
                best_prefix = prefix
                best_score = score
                best_path = pr

        if best_prefix is None:
            if first_trace:
                print(f"  No reachable nodes to trace "
                      f"({unreachable} unreachable)")
            break

        if first_trace:
            targets = []
            for pfx in ctx.graph.nodes:
                if pfx in ctx.ds.traced_set or pfx == ctx.companion_prefix:
                    continue
                pr = widest_path(ctx.graph, ctx.companion_prefix, pfx)
                if pr.found:
                    targets.append((ctx.graph.nodes[pfx], pr))
            targets.sort(
                key=lambda x: x[1].bottleneck_snr, reverse=True)
            print(f"  Targets: {len(targets)} reachable, "
                  f"{unreachable} unreachable")
            for n, pr in targets[:10]:
                print(f"    {n.name:<25} "
                      f"(bottleneck: {pr.bottleneck_snr:+.1f} dB)")
            if len(targets) > 10:
                print(f"    ... and {len(targets) - 10} more")
            first_trace = False

        node = ctx.graph.nodes[best_prefix]
        contact = ctx.contact_map.get(best_prefix)

        print(f"\n    --- {node.name} [{best_prefix}] ---")

        alt_paths = widest_path_alternatives(
            ctx.graph, ctx.companion_prefix, best_prefix, k=3)
        if not alt_paths:
            alt_paths = [best_path]
        alt_paths = ctx.filter_alternatives(alt_paths)

        for pi, path_result in enumerate(alt_paths):
            label = "Primary" if pi == 0 else f"Alt {pi}"
            print(f"    {label} path: "
                  f"{' -> '.join(path_result.path_names)}  "
                  f"(bottleneck: "
                  f"{path_result.bottleneck_snr:+.1f} dB)")

            if ctx.radio_config:
                try:
                    await ctx.ensure_connected()
                except Exception:
                    break

            await ctx.set_contact_path(contact, path_result)

            # Build forced trace from this specific path so alternatives
            # actually trace through different intermediates
            ADDR_HEX = HOP_HEX_LEN
            hops = [p[:ADDR_HEX].lower() for p in path_result.path]
            trace_addrs = hops + list(reversed(hops[:-1]))
            forced = ",".join(trace_addrs)

            ok, t_edges, err = await _trace_repeater(
                ctx.mc, contact, ctx.companion_prefix, best_prefix,
                ctx.graph, ctx.timeout, forced_trace_path=forced)

            if ok:
                if t_edges > 0:
                    print(f"    +{t_edges} trace edges")
                    ctx.graph.infer_reverse_edges(ctx.infer_penalty)
                    ctx.fix_names()
                    ctx.save()
                else:
                    print(f"    Trace OK (no new edges)")
                for p in path_result.path[1:-1]:
                    intermediate_fails.pop(p, None)
                break

            if err:
                print(f"    {err}")
                for p in path_result.path[1:-1]:
                    fails = intermediate_fails.get(p, 0) + 1
                    intermediate_fails[p] = fails
                    if fails == 3:
                        iname = (ctx.graph.nodes[p].name
                                 if p in ctx.graph.nodes else p)
                        print(f"    ⚠ {iname}: {fails} consecutive"
                              f" fails — deprioritizing")

            if pi < len(alt_paths) - 1:
                print(f"    Trying alternative path...")
                await asyncio.sleep(ctx.delay)

        ctx.ds.traced_set.add(best_prefix)
        trace_count += 1
        await asyncio.sleep(ctx.delay)

    return trace_count


async def _run_login_phase(ctx: _DiscoveryCtx):
    """Phase 2: Login to nodes for full neighbor tables with alternative paths."""
    login_candidates = []
    for prefix in list(ctx.graph.nodes.keys()):
        if prefix in ctx.ds.logged_in_set:
            continue
        contact = ctx.contact_map.get(prefix)
        if not contact:
            continue
        node = ctx.graph.nodes[prefix]
        pw_list = match_passwords(
            node, ctx.passwords, ctx.default_guest_passwords)
        if not pw_list:
            continue
        pr = widest_path(ctx.graph, ctx.companion_prefix, prefix)
        if not pr.found or pr.bottleneck_snr < -10.0:
            continue
        login_candidates.append((prefix, pw_list, pr))

    if not login_candidates:
        return 0

    login_candidates.sort(
        key=lambda x: x[2].bottleneck_snr, reverse=True)
    print(f"\n  Phase 2: Login ({len(login_candidates)} candidates)")

    login_count = 0
    for prefix, pw_list, _ in login_candidates:
        node = ctx.graph.nodes[prefix]
        contact = ctx.contact_map[prefix]

        alt_paths = widest_path_alternatives(
            ctx.graph, ctx.companion_prefix, prefix, k=3)
        if not alt_paths:
            continue
        alt_paths = ctx.filter_alternatives(alt_paths)

        print(f"\n    --- {node.name} [{prefix}] ---")

        got_data = False
        for pi, path_result in enumerate(alt_paths):
            label = "Primary" if pi == 0 else f"Alt {pi}"
            print(f"    {label} path: "
                  f"{' -> '.join(path_result.path_names)}  "
                  f"(bottleneck: "
                  f"{path_result.bottleneck_snr:+.1f} dB)")

            if path_result.hop_count > 0:
                await ctx.set_contact_path(contact, path_result)

            for pw in pw_list:
                if ctx.radio_config:
                    try:
                        await ctx.ensure_connected()
                    except Exception:
                        break

                ok, n_edges, err, used_pw = \
                    await _login_and_neighbors(
                        ctx.mc, contact, node, pw,
                        ctx.graph, ctx.timeout,
                        name_map=ctx.name_map,
                        contact_map=ctx.contact_map)

                if err == "CONNECTION_LOST":
                    if ctx.radio_config:
                        try:
                            await ctx.ensure_connected()
                        except Exception:
                            break
                    continue

                if ok:
                    if n_edges > 0:
                        print(f"    +{n_edges} neighbor edges")
                        ctx.graph.infer_reverse_edges(ctx.infer_penalty)
                        ctx.fix_names()
                        ctx.save()
                    elif err:
                        print(f"    Login OK but: {err}")
                    got_data = True
                    break
                else:
                    print(f"    {err}")

                await asyncio.sleep(ctx.delay)

            if got_data:
                break

            if pi < len(alt_paths) - 1:
                print(f"    Trying alternative path...")
                await asyncio.sleep(ctx.delay)

        if got_data:
            ctx.ds.logged_in_set.add(prefix)
        login_count += 1
        await asyncio.sleep(ctx.delay)

    return login_count


async def _run_proximity_probe(ctx: _DiscoveryCtx):
    """Phase 3: Probe close node pairs that have no known edge.
    Uses trace through A→B to test if they can hear each other.
    No login needed."""
    from meshcore_topology import find_proximity_gaps, widest_path

    gaps = find_proximity_gaps(ctx.graph, ctx.probe_distance_km)
    if not gaps:
        return 0

    # Compute paths and sort by best reachable path (best first)
    # Only probe gaps where at least one node has a poor path
    # (below probe_min_snr) — a new edge could improve routing
    scored_gaps = []
    skipped = 0
    for node_a, node_b, dist_km in gaps:
        path_a = widest_path(ctx.graph, ctx.companion_prefix, node_a.prefix)
        path_b = widest_path(ctx.graph, ctx.companion_prefix, node_b.prefix)

        if not path_a.found and not path_b.found:
            continue

        # Skip if both nodes already have good paths
        snr_a = path_a.bottleneck_snr if path_a.found else -999
        snr_b = path_b.bottleneck_snr if path_b.found else -999
        if snr_a > ctx.probe_min_snr and snr_b > ctx.probe_min_snr:
            skipped += 1
            continue

        if path_a.found and (not path_b.found or
                path_a.bottleneck_snr >= path_b.bottleneck_snr):
            via_node, target_node = node_a, node_b
            via_path = path_a
        else:
            via_node, target_node = node_b, node_a
            via_path = path_b

        scored_gaps.append((via_path.bottleneck_snr, dist_km,
                            via_node, target_node, via_path,
                            path_a, path_b))

    scored_gaps.sort(key=lambda x: -x[0])  # best path SNR first

    if not scored_gaps:
        if skipped:
            print(f"\n  Phase 2: Proximity probe — "
                  f"{skipped} gaps skipped (paths above "
                  f"{ctx.probe_min_snr:+.1f} dB)")
        return 0

    print(f"\n  Phase 2: Proximity probe "
          f"({len(scored_gaps)} gaps, {skipped} skipped, "
          f"threshold {ctx.probe_min_snr:+.1f} dB)")

    probe_count = 0
    for (best_snr, dist_km, via_node, target_node, via_path,
         path_a, path_b) in scored_gaps:

        print(f"\n    Probing: {via_node.name} [{via_node.prefix[:4]}] "
              f"↔ {target_node.name} [{target_node.prefix[:4]}] "
              f"({dist_km:.1f} km)")

        # Build trace: companion → ... → via → target → via → ... → companion
        ADDR_HEX = 4
        via_hops = [p[:ADDR_HEX].lower() for p in via_path.path]
        target_hop = target_node.prefix[:ADDR_HEX].lower()

        # Forward: companion_path_to_via + target
        fwd = via_hops + [target_hop]
        # Round-trip: fwd + reverse back
        trace_addrs = fwd + list(reversed(fwd[:-1]))
        trace_path = ",".join(trace_addrs)

        contact = ctx.contact_map.get(target_node.prefix)
        if not contact:
            # Try via_node's contact instead
            contact = ctx.contact_map.get(via_node.prefix)
        if not contact:
            print(f"    No contact for either node, skipping")
            continue

        if ctx.radio_config:
            try:
                await ctx.ensure_connected()
            except Exception:
                break

        # Try up to 2 attempts from the primary direction,
        # then try the reverse direction if both fail
        MAX_ATTEMPTS = 2
        found = False

        for attempt in range(1, MAX_ATTEMPTS + 1):
            print(f"    Attempt {attempt}/{MAX_ATTEMPTS}: "
                  f"via {via_node.name} [{via_node.prefix[:4]}]")
            print(f"    Trace: {trace_path}")
            ok, t_edges, err = await _trace_repeater(
                ctx.mc, contact, ctx.companion_prefix,
                target_node.prefix, ctx.graph, ctx.timeout,
                forced_trace_path=trace_path)

            if ok and t_edges > 0:
                print(f"    +{t_edges} edges from proximity probe")
                ctx.graph.infer_reverse_edges(ctx.infer_penalty)
                ctx.fix_names()
                ctx.save()
                found = True
                break
            elif err:
                print(f"    {err}")
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(ctx.delay)

        # If primary direction failed, try reverse (B → A instead of A → B)
        # Route through target_node to reach via_node
        path_to_target = widest_path(
            ctx.graph, ctx.companion_prefix, target_node.prefix)
        if not found and path_to_target.found:
            rev_via = target_node
            rev_target = via_node

            rev_hops = [p[:ADDR_HEX].lower() for p in path_to_target.path]
            rev_target_hop = rev_target.prefix[:ADDR_HEX].lower()
            rev_fwd = rev_hops + [rev_target_hop]
            rev_addrs = rev_fwd + list(reversed(rev_fwd[:-1]))
            rev_trace = ",".join(rev_addrs)

            rev_contact = (ctx.contact_map.get(rev_target.prefix) or
                           ctx.contact_map.get(rev_via.prefix))
            if rev_contact:
                print(f"    Reverse: via {rev_via.name} "
                      f"[{rev_via.prefix[:4]}]")
                print(f"    Trace: {rev_trace}")
                ok, t_edges, err = await _trace_repeater(
                    ctx.mc, rev_contact, ctx.companion_prefix,
                    rev_target.prefix, ctx.graph, ctx.timeout,
                    forced_trace_path=rev_trace)

                if ok and t_edges > 0:
                    print(f"    +{t_edges} edges from reverse probe")
                    ctx.graph.infer_reverse_edges(ctx.infer_penalty)
                    ctx.fix_names()
                    ctx.save()
                    found = True
                elif err:
                    print(f"    {err}")

        # If all trace attempts failed, try flood discovery as fallback
        # Try both nodes — firmware may know routes we don't
        if not found:
            print(f"    Traces failed — trying flood discovery")
            for flood_node in [target_node, via_node]:
                if ctx.contact_map.get(flood_node.prefix):
                    edges = await _flood_probe_node(
                        ctx, flood_node.prefix, label="fallback")
                    if edges > 0:
                        found = True
                        break
                    await asyncio.sleep(ctx.delay)

        probe_count += 1
        await asyncio.sleep(ctx.delay)

    return probe_count


async def _flood_probe_node(ctx, target_prefix, label=""):
    """Send disc_path flood for a target node, probe any missing edges found.
    Returns number of new edges discovered."""
    from meshcore import EventType
    from meshcore_topology import widest_path

    node = ctx.graph.nodes.get(target_prefix)
    contact = ctx.contact_map.get(target_prefix)
    if not node or not contact:
        return 0

    if label:
        print(f"    Flood {label}: {node.name} [{target_prefix[:4]}]")
    else:
        print(f"    Flood: {node.name} [{target_prefix[:4]}]")

    path_queue = asyncio.Queue()
    def _on_path(event):
        path_queue.put_nowait(event)
    sub = ctx.mc.subscribe(EventType.PATH_RESPONSE, _on_path)

    new_edges = 0
    try:
        print(f"      TX: Path discovery...")
        res = await asyncio.wait_for(
            ctx.mc.commands.send_path_discovery(contact),
            timeout=ctx.timeout)

        if res is None or res.type == EventType.ERROR:
            sub.unsubscribe()
            print(f"      TX: Failed")
            return 0

        try:
            ev = await asyncio.wait_for(
                path_queue.get(), timeout=ctx.timeout)
        except asyncio.TimeoutError:
            print(f"      RX: No response (timeout)")
            return 0
        finally:
            sub.unsubscribe()

        out_path = ev.payload.get("out_path", "")
        in_path = ev.payload.get("in_path", "")
        out_hlen = ev.payload.get("out_path_hash_len", 1)
        in_hlen = ev.payload.get("in_path_hash_len", 1)

        out_hops = _decode_path_hops(out_path, out_hlen)
        in_hops = _decode_path_hops(in_path, in_hlen)
        out_resolved = [_resolve_hop(h, ctx.graph) for h in out_hops]
        in_resolved = [_resolve_hop(h, ctx.graph) for h in in_hops]

        def _hop_name(h, p):
            if p and p in ctx.graph.nodes:
                return f"{ctx.graph.nodes[p].name} [{h}]"
            return f"[{h}]"

        out_display = ("direct" if not out_hops else
            " -> ".join(_hop_name(h, p)
                        for h, p in zip(out_hops, out_resolved)))
        in_display = ("direct" if not in_hops else
            " -> ".join(_hop_name(h, p)
                        for h, p in zip(in_hops, in_resolved)))

        print(f"      RX: out: {out_display}, in: {in_display}")

        # Find missing edges in firmware's paths
        unknown_pairs = set()
        for hop_list in [out_resolved, in_resolved]:
            full = ([ctx.companion_prefix] +
                    [h for h in hop_list if h] + [target_prefix])
            for j in range(len(full) - 1):
                a, b = full[j], full[j + 1]
                if a and b and a != b:
                    if (not ctx.graph.get_edge(a, b) and
                            not ctx.graph.get_edge(b, a)):
                        unknown_pairs.add(tuple(sorted([a, b])))

        if unknown_pairs:
            print(f"      Missing edges: {len(unknown_pairs)}")
            for pa, pb in unknown_pairs:
                na = ctx.graph.nodes.get(pa)
                nb = ctx.graph.nodes.get(pb)
                if not na or not nb:
                    continue
                print(f"      Probing: {na.name} [{pa[:4]}] "
                      f"↔ {nb.name} [{pb[:4]}]")

                path_to_a = widest_path(
                    ctx.graph, ctx.companion_prefix, pa)
                if not path_to_a.found:
                    path_to_b = widest_path(
                        ctx.graph, ctx.companion_prefix, pb)
                    if not path_to_b.found:
                        print(f"        Neither reachable")
                        continue
                    path_to_a = path_to_b
                    pa, pb = pb, pa

                ADDR_HEX = HOP_HEX_LEN
                via_hops = [p[:ADDR_HEX].lower()
                            for p in path_to_a.path]
                target_hop = pb[:ADDR_HEX].lower()
                fwd = via_hops + [target_hop]
                trace_addrs = fwd + list(reversed(fwd[:-1]))
                forced = ",".join(trace_addrs)

                probe_contact = (ctx.contact_map.get(pb) or
                                 ctx.contact_map.get(pa))
                if not probe_contact:
                    continue

                ok, t_edges, err = await _trace_repeater(
                    ctx.mc, probe_contact,
                    ctx.companion_prefix, pb,
                    ctx.graph, ctx.timeout,
                    forced_trace_path=forced)

                if ok and t_edges > 0:
                    print(f"        +{t_edges} edges!")
                    new_edges += t_edges
                    ctx.graph.infer_reverse_edges(ctx.infer_penalty)
                    ctx.fix_names()
                    ctx.save()
                elif err:
                    print(f"        {err}")

                await asyncio.sleep(ctx.delay)

    except asyncio.TimeoutError:
        print(f"      Timeout")
    except Exception as e:
        print(f"      Error: {e}")

    return new_edges


async def _run_flood_discovery(ctx: _DiscoveryCtx):
    """Phase 4: Use firmware flood-based path discovery to learn routes
    the firmware knows but we don't.  Targets nodes where our path is
    long or has many inferred edges."""
    from meshcore_topology import widest_path

    candidates = []
    for prefix in list(ctx.graph.nodes.keys()):
        if prefix == ctx.companion_prefix:
            continue
        node = ctx.graph.nodes[prefix]
        contact = ctx.contact_map.get(prefix)
        if not contact:
            continue

        pr = widest_path(ctx.graph, ctx.companion_prefix, prefix)
        if not pr.found:
            continue

        # Only flood nodes with poor paths (below threshold)
        if pr.bottleneck_snr > ctx.probe_min_snr:
            continue

        inferred_count = sum(1 for e in pr.edges
                             if e.source == "inferred")
        if pr.hop_count >= 3 or inferred_count >= 1:
            score = pr.hop_count + inferred_count * 2
            candidates.append((score, prefix, node, contact, pr))

    if not candidates:
        return 0

    candidates.sort(key=lambda x: -x[0])
    print(f"\n  Phase 4: Flood discovery "
          f"({len(candidates)} candidates)")

    flood_count = 0
    total_edges = 0

    for score, prefix, node, contact, our_path in candidates:
        print(f"\n    {node.name} [{prefix[:4]}] — "
              f"our: {our_path.hop_count}h "
              f"{our_path.bottleneck_snr:+.1f} dB")

        if ctx.radio_config:
            try:
                await ctx.ensure_connected()
            except Exception:
                break

        edges = await _flood_probe_node(ctx, prefix)
        total_edges += edges
        flood_count += 1
        await asyncio.sleep(ctx.delay)

    if total_edges:
        print(f"\n  Phase 4: +{total_edges} edges from "
              f"{flood_count} discoveries")

    return flood_count


def _decode_path_hops(path_hex, hash_len):
    """Decode a path hex string into list of hop hashes."""
    if not path_hex:
        return []
    chunk = hash_len * 2  # bytes to hex chars
    return [path_hex[i:i+chunk] for i in range(0, len(path_hex), chunk)]


def _resolve_hop(hop_hash, graph):
    """Resolve a short hop hash to a full node prefix.
    Returns best match or None."""
    h = hop_hash.lower()
    matches = []
    for pfx in graph.nodes:
        if pfx.lower().startswith(h):
            matches.append(pfx)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Ambiguous — return first (best effort)
        return matches[0]
    return None


# ---------------------------------------------------------------------------
# Discovery orchestrator
# ---------------------------------------------------------------------------

async def progressive_discovery(mc, graph: NetworkGraph,
                                companion_prefix: str,
                                passwords: list[RepeaterAccess],
                                max_rounds: int = 5,
                                timeout: float = 30.0,
                                delay: float = 5.0,
                                infer_penalty: float = 5.0,
                                save_file: str = None,
                                default_guest_passwords: list = None,
                                radio_config: RadioConfig = None,
                                probe_distance_km: float = 2.0,
                                probe_min_snr: float = -5.0):
    """
    Run progressive topology discovery.

    Round 0: Login to companion repeater, fetch its neighbor table (seeds graph).
    Rounds 1+:
      Phase 1 — Trace sweep: trace all reachable nodes (best-first).
      Phase 2 — Proximity probe: test close node pairs, fill gaps early.
      Phase 3 — Login & neighbors: richer data, benefits from better routes.
      Phase 4 — Flood discovery: firmware-based, reveals missed routes.

    Saves topology after every graph update. State is persisted for resume.
    """
    if default_guest_passwords is None:
        default_guest_passwords = DEFAULT_GUEST_PASSWORDS

    state_file = state_file_for(save_file)

    # --- Build contact_map from radio ---
    contact_map = {}
    name_map = {}

    print(f"\n  TX: Requesting contact list from radio...")
    try:
        await mc.ensure_contacts(follow=True)
        repeater_count = 0
        for pub_key, contact in mc.contacts.items():
            if not isinstance(contact, dict):
                continue
            prefix = pub_key[:8].upper()
            if not prefix:
                continue
            contact_map[prefix] = contact
            name_map[prefix] = contact.get('adv_name', '') or f"[{prefix}]"
            if contact.get('type', 0) == 2:
                repeater_count += 1

        print(f"  RX: Loaded {len(contact_map)} contacts "
              f"({repeater_count} repeaters)")

        if companion_prefix not in contact_map:
            for key in contact_map:
                if key.startswith(companion_prefix):
                    print(f"  Resolved companion prefix: "
                          f"{companion_prefix} → {key}")
                    companion_prefix = key
                    break
    except Exception as e:
        print(f"  Warning: error fetching contacts: {e}")

    # Add companion node to graph
    comp_name = name_map.get(companion_prefix, f"[{companion_prefix}]")
    comp_contact = contact_map.get(companion_prefix, {})
    if companion_prefix not in graph.nodes:
        graph.add_node(RepeaterNode(
            prefix=companion_prefix, name=comp_name,
            lat=comp_contact.get('adv_lat', 0.0),
            lon=comp_contact.get('adv_lon', 0.0),
            last_seen=datetime.now().isoformat(timespec='seconds'),
        ))

    # --- Check for resumable state ---
    ds = DiscoveryState(companion_prefix=companion_prefix)
    resumed = False
    if os.path.exists(state_file):
        try:
            prev = DiscoveryState.load(state_file)
            # Only resume if companion matches, not completed, AND the
            # graph actually has nodes from the saved state (guards
            # against stale state file after topology was deleted).
            graph_has_data = len(graph.nodes) > 1
            if (prev.companion_prefix == companion_prefix
                    and not prev.completed
                    and graph_has_data):
                ds = prev
                resumed = True
            elif not graph_has_data and prev.traced_set:
                print(f"  Stale state file (graph empty) — "
                      f"starting fresh")
                os.remove(state_file)
        except Exception:
            pass

    if resumed:
        print("\n" + "=" * 40)
        print("  RESUMING PROGRESSIVE TOPOLOGY DISCOVERY")
        print(f"  Resumed: {datetime.now().isoformat(timespec='seconds')}")
        print(f"  Companion: {companion_prefix}")
        print(f"  Already traced: {len(ds.traced_set)}  "
              f"Already logged in: {len(ds.logged_in_set)}")
        print(f"  Continuing from round {ds.current_round + 1}")
        print("=" * 40)
        start_round = ds.current_round + 1
    else:
        print("\n" + "=" * 40)
        print("  MESHCORE PROGRESSIVE TOPOLOGY DISCOVERY")
        print(f"  Started: {datetime.now().isoformat(timespec='seconds')}")
        print(f"  Companion: {companion_prefix}")
        print(f"  Max rounds: {max_rounds}")
        print("=" * 40)
        ds.traced_set.add(companion_prefix)
        ds.logged_in_set.add(companion_prefix)
        start_round = 0

    ctx = _DiscoveryCtx(
        mc=mc, graph=graph, companion_prefix=companion_prefix,
        contact_map=contact_map, name_map=name_map, ds=ds,
        passwords=passwords,
        default_guest_passwords=default_guest_passwords,
        timeout=timeout, delay=delay, infer_penalty=infer_penalty,
        radio_config=radio_config, save_file=save_file,
        state_file=state_file,
        probe_distance_km=probe_distance_km,
        probe_min_snr=probe_min_snr,
    )

    stopped = False
    all_results = []

    try:
        if start_round == 0:
            await _run_round0(ctx)
            ds.current_round = 0
            ctx.save_and_report("round 0 done")
            start_round = 1

        for round_num in range(start_round, max_rounds + 1):
            round_start = time.monotonic()
            round_edges_before = graph.stats()['edges']

            print(f"\n  {'='*30}")
            print(f"  ROUND {round_num}")
            print(f"  {'='*30}")

            await ctx.refresh_contacts()
            trace_count = await _run_trace_phase(ctx)
            probe_count = await _run_proximity_probe(ctx)
            login_count = await _run_login_phase(ctx)
            flood_count = await _run_flood_discovery(ctx)

            round_edges = graph.stats()['edges'] - round_edges_before
            duration = time.monotonic() - round_start
            s = graph.stats()

            print(f"\n  Round {round_num}: {trace_count} traces, "
                  f"{login_count} logins, {probe_count} probes, "
                  f"{flood_count} floods, +{round_edges} edges")
            print(f"  Graph: {s['nodes']} nodes, {s['edges']} edges  "
                  f"({duration:.1f}s)")

            ds.current_round = round_num
            ctx.save()

            all_results.append(DiscoveryResult(
                round_num=round_num,
                attempted=trace_count + login_count,
                new_edges=round_edges,
                duration_secs=duration,
            ))

            if round_edges == 0:
                print(f"\n  Stopping: no new edges discovered")
                break

    except KeyboardInterrupt:
        stopped = True
        print(f"\n\n  Discovery stopped by user.")
        ctx.save_and_report("interrupted — resume later to continue")

    if not stopped:
        ds.completed = True
        ctx.save()
        if os.path.exists(state_file):
            os.remove(state_file)

    # --- Final report ---
    print("\n" + "=" * 40)
    print(f"  DISCOVERY {'STOPPED' if stopped else 'COMPLETE'}")
    print("=" * 40)

    print_topology_report(graph)
    results = all_pairs_widest(graph)
    print_all_pairs_report(results, graph)

    return all_results


# ---------------------------------------------------------------------------
# Discovery plan (dry-run)
# ---------------------------------------------------------------------------

def plan_discovery(graph: NetworkGraph,
                   companion_prefix: str,
                   passwords: list[RepeaterAccess],
                   default_guest_passwords: list = None):
    """Show what a discovery session would do without executing it."""
    if default_guest_passwords is None:
        default_guest_passwords = DEFAULT_GUEST_PASSWORDS

    print("\n" + "=" * 40)
    print("  DISCOVERY PLAN (dry run)")
    print(f"  Companion: {companion_prefix}")
    print("=" * 40)

    comp_node = graph.get_node(companion_prefix)
    if not comp_node:
        print(f"\n  ERROR: Companion {companion_prefix} not in graph")
        return

    queried = {companion_prefix}
    targets = []

    for prefix, node in graph.nodes.items():
        if prefix in queried:
            continue
        pw_list = match_passwords(node, passwords, default_guest_passwords)
        path_result = widest_path(graph, companion_prefix, prefix)
        targets.append((node, pw_list, path_result))

    targets.sort(key=lambda x: x[2].bottleneck_snr if x[2].found else -999,
                  reverse=True)

    if not targets:
        print("\n  No repeaters to query")
        return

    print(f"\n  Repeaters to query:")
    for node, pw_list, path_r in targets:
        if path_r.found:
            route = " -> ".join(path_r.path_names)
            print(f"    {node.name:<25} via {route}")
            print(f"      bottleneck: {path_r.bottleneck_snr:+.1f} dB  "
                  f"| hops: {path_r.hop_count}")
        else:
            print(f"    {node.name:<25} NO KNOWN PATH (will trace)")

        pw_strs = [f"'{p.password}'" if p.password else "(blank)"
                   for p in pw_list]
        print(f"      passwords: {' -> '.join(pw_strs)}")

    print(f"\n  Total: {len(targets)} repeaters")


# ---------------------------------------------------------------------------
# Interactive simulation (manual data entry, no radio)
# ---------------------------------------------------------------------------

def interactive_discovery():
    """
    Simulate progressive discovery by entering neighbors/trace data manually.
    """
    graph = NetworkGraph()
    passwords = []
    companion = None
    queried = set()

    print("\n  INTERACTIVE PROGRESSIVE DISCOVERY")
    print("  Commands: companion, node, password, query, trace, tracemulti,")
    print("           sweep, plan, path, matrix, show, save, load, quit")
    print("  Type 'help' for details.\n")

    while True:
        try:
            cmd = input("  discover> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not cmd:
            continue

        parts = cmd.split(maxsplit=1)
        action = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if action in ("quit", "q"):
            break

        elif action == "companion":
            if not rest:
                print("    Usage: companion PREFIX_OR_NAME")
                continue
            node = graph.get_node(rest)
            if node:
                companion = node.prefix
                print(f"    Companion: {node.name} [{node.prefix}]")
            else:
                print(f"    Node not found: {rest}")

        elif action == "node":
            args = rest.split()
            if len(args) < 2:
                print("    Usage: node PREFIX NAME")
                continue
            prefix = args[0].upper()
            name = args[1]
            graph.add_node(RepeaterNode(prefix=prefix, name=name))
            print(f"    Added: [{prefix}] {name}")

        elif action == "password":
            args = rest.split()
            if len(args) < 2:
                print("    Usage: password PREFIX_OR_NAME admin|guest [pw]")
                continue
            target, level = args[0], args[1]
            pw = args[2] if len(args) > 2 else ""
            node = graph.get_node(target)
            if node:
                node.access_level = level
                node.password = pw
                passwords.append(RepeaterAccess(
                    prefix=node.prefix, level=level,
                    password=pw, name=node.name))
                print(f"    Set {node.name}: {level}")
            elif target == "*":
                passwords.append(RepeaterAccess(
                    prefix="", level=level, password=pw, name="*"))
                print(f"    Set default: {level}")
            else:
                print(f"    Node not found: {target}")

        elif action == "query":
            if not rest:
                print("    Usage: query PREFIX_OR_NAME")
                continue
            node = graph.get_node(rest)
            if not node:
                print(f"    Node not found: {rest}")
                continue
            if companion:
                path_r = widest_path(graph, companion, node.prefix)
                if path_r.found:
                    print(f"    Recommended path: {path_r}")
                else:
                    print(f"    No known path (use flood)")

            print(f"    Paste neighbors for {node.name} (empty line to end):")
            lines = []
            while True:
                line = input("    | ")
                if not line.strip():
                    break
                lines.append(line)

            if lines:
                text = "\n".join(lines)
                before = graph.stats()
                graph.add_from_neighbors_output(node.prefix, text)
                graph.infer_reverse_edges(3.0)
                after = graph.stats()
                queried.add(node.prefix)
                print(f"    +{after['edges'] - before['edges']} edges, "
                      f"+{after['nodes'] - before['nodes']} nodes")

        elif action == "trace":
            args = rest.split()
            if len(args) < 3 or not companion:
                print("    Usage: trace TARGET forward_snr return_snr")
                if not companion:
                    print("    Set companion first")
                continue
            target = graph.get_node(args[0])
            if not target:
                print(f"    Node not found: {args[0]}")
                continue
            try:
                fwd, ret = float(args[1]), float(args[2])
            except ValueError:
                print("    SNR values must be numbers")
                continue
            before = graph.stats()['edges']
            graph.add_from_single_hop_trace(companion, target.prefix, fwd, ret)
            print(f"    {graph.nodes[companion].name} <-> {target.name}: "
                  f"fwd {fwd:+.1f} dB, ret {ret:+.1f} dB  "
                  f"(+{graph.stats()['edges'] - before} edges)")

        elif action == "tracemulti":
            args = rest.split()
            if len(args) < 3 or not companion:
                print("    Usage: tracemulti A,B,C fwd1,fwd2 ret1,ret2")
                continue
            path_names = [n.strip() for n in args[0].split(",")]
            path_nodes = []
            for name in path_names:
                node = graph.get_node(name)
                if not node:
                    print(f"    Node not found: {name}")
                    break
                path_nodes.append(node)
            else:
                try:
                    fwd = [float(x) for x in args[1].split(",")]
                    ret = [float(x) for x in args[2].split(",")]
                except ValueError:
                    print("    SNR values must be numbers")
                    continue
                before = graph.stats()['edges']
                graph.add_from_multihop_trace(
                    companion, [n.prefix for n in path_nodes], fwd, ret)
                print(f"    +{graph.stats()['edges'] - before} edges")

        elif action == "sweep":
            if not companion:
                print("    Set companion first")
                continue
            print(f"\n    TRACE SWEEP PLAN:")
            for node in sorted(graph.nodes.values(), key=lambda n: n.name):
                if node.prefix == companion:
                    continue
                fwd = graph.get_edge(companion, node.prefix)
                rev = graph.get_edge(node.prefix, companion)
                if fwd and rev and fwd.source == "trace":
                    print(f"      {node.name:<25} done")
                else:
                    print(f"      {node.name:<25} NEEDS TRACE")

        elif action == "plan":
            if not companion:
                print("    Set companion first")
                continue
            plan_discovery(graph, companion, passwords)

        elif action == "path":
            args = rest.split()
            if len(args) < 2:
                print("    Usage: path FROM TO")
                continue
            src = graph.get_node(args[0])
            dst = graph.get_node(args[1])
            if src and dst:
                print_path_result(widest_path(graph, src.prefix, dst.prefix),
                                  graph)
            else:
                print(f"    Node not found")

        elif action == "show":
            print_topology_report(graph)

        elif action == "matrix":
            print_all_pairs_report(all_pairs_widest(graph), graph)

        elif action == "save":
            fn = rest.strip() or "topology.json"
            graph.save(fn)
            print(f"    Saved to {fn}")

        elif action == "load":
            fn = rest.strip() or "topology.json"
            try:
                graph = NetworkGraph.load(fn)
                s = graph.stats()
                print(f"    Loaded: {s['nodes']} nodes, {s['edges']} edges")
            except Exception as e:
                print(f"    Error: {e}")

        elif action == "help":
            print("    companion PREFIX       set companion repeater")
            print("    node PREFIX NAME       add a repeater")
            print("    password TARGET level [pw]  set credentials")
            print("    query PREFIX           enter neighbors data")
            print("    trace TARGET fwd ret   enter trace SNRs")
            print("    tracemulti A,B fwd ret enter multi-hop trace")
            print("    sweep                  show needed traces")
            print("    plan                   show discovery plan")
            print("    path FROM TO           find widest path")
            print("    matrix                 all-pairs matrix")
            print("    show                   topology overview")
            print("    save/load [file]       save/load topology")
            print("    quit                   exit")

        else:
            print(f"    Unknown: {action} (type 'help')")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="MeshCore Progressive Topology Discovery",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --config config.json
  %(prog)s --interactive
  %(prog)s --topology network.json --plan --companion 53640000
        """
    )

    parser.add_argument("--config", "-C", metavar="FILE",
                        default="config.json",
                        help="Config file (default: config.json)")
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Interactive simulation mode")
    parser.add_argument("--topology", metavar="FILE",
                        help="Load existing topology")
    parser.add_argument("--passwords", metavar="FILE",
                        help="Passwords JSON file")
    parser.add_argument("--companion", metavar="PREFIX",
                        help="Companion repeater prefix")
    parser.add_argument("--plan", action="store_true",
                        help="Dry run (show plan only)")
    parser.add_argument("--save", metavar="FILE",
                        help="Save topology to file")

    # Connection overrides
    parser.add_argument("--serial", "-s", metavar="PORT")
    parser.add_argument("--tcp", "-t", metavar="HOST:PORT")
    parser.add_argument("--ble", nargs="?", const="scan")
    parser.add_argument("--baudrate", type=int, default=None)

    # Discovery params
    parser.add_argument("--max-rounds", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--delay", type=float, default=None)
    parser.add_argument("--infer-penalty", type=float, default=None)

    args = parser.parse_args()

    if args.interactive:
        interactive_discovery()
        return

    # Load config
    config = Config()
    if os.path.exists(args.config):
        try:
            config = load_config(args.config)
            print(f"Loaded config: {args.config}")
        except Exception as e:
            print(f"Warning: could not load {args.config}: {e}")
    elif args.config != "config.json":
        print(f"ERROR: config file not found: {args.config}")
        sys.exit(1)

    # CLI overrides
    if args.companion:
        config.companion_prefix = args.companion.upper()
    if args.save:
        config.discovery_save_file = args.save
    if args.max_rounds is not None:
        config.discovery_max_rounds = args.max_rounds
    if args.timeout is not None:
        config.discovery_timeout = args.timeout
    if args.delay is not None:
        config.discovery_delay = args.delay
    if args.infer_penalty is not None:
        config.discovery_infer_penalty = args.infer_penalty

    if args.serial:
        config.radio.protocol = "serial"
        config.radio.serial_port = args.serial
        if args.baudrate:
            config.radio.baudrate = args.baudrate
    elif args.tcp:
        config.radio.protocol = "tcp"
        host, port = args.tcp.split(":")
        config.radio.host = host
        config.radio.port = int(port)
    elif args.ble is not None:
        config.radio.protocol = "ble"
        if args.ble != "scan":
            config.radio.ble_address = args.ble

    # Load topology if provided
    graph = NetworkGraph()
    if args.topology:
        graph = NetworkGraph.load(args.topology)
        s = graph.stats()
        print(f"Loaded topology: {s['nodes']} nodes, {s['edges']} edges")

    # Load passwords
    passwords = config.passwords
    default_guest_pws = config.default_guest_passwords
    if args.passwords:
        passwords, default_guest_pws = load_passwords(args.passwords)

    # Plan mode
    if args.plan:
        if not config.companion_prefix:
            print("ERROR: --companion required")
            sys.exit(1)
        plan_discovery(graph, config.companion_prefix,
                       passwords, default_guest_pws)
        return

    # Live mode
    if not config.companion_prefix:
        print("ERROR: companion_prefix required (config or --companion)")
        sys.exit(1)

    if (not config.radio.host and not config.radio.serial_port
            and config.radio.protocol != "ble"):
        print("ERROR: no radio connection configured")
        sys.exit(1)

    async def run():
        mc = await connect_radio(config.radio)
        try:
            # Auto-detect companion from self_info if available
            if mc.self_info and mc.self_info.get("public_key"):
                si_prefix = mc.self_info["public_key"][:8].upper()
                si_name = mc.self_info.get("name", "")
                print(f"  Connected to: {si_name} [{si_prefix}]")
                if not config.companion_prefix:
                    config.companion_prefix = si_prefix

            await progressive_discovery(
                mc, graph, config.companion_prefix, passwords,
                max_rounds=config.discovery_max_rounds,
                timeout=config.discovery_timeout,
                delay=config.discovery_delay,
                infer_penalty=config.discovery_infer_penalty,
                save_file=config.discovery_save_file,
                default_guest_passwords=default_guest_pws,
                radio_config=config.radio,
                probe_distance_km=config.discovery_probe_distance_km,
                probe_min_snr=config.discovery_probe_min_snr,
            )
        finally:
            await mc.disconnect()

    asyncio.run(run())


if __name__ == "__main__":
    main()
