#!/usr/bin/env python3
"""
MeshCore Interactive Network Map
=================================
Web-based map for visualizing mesh network topology, node health,
interactive path finding, and live discovery control.

Usage:
    python -m meshcore_optimizer.web                          # default topology.json
    python -m meshcore_optimizer.web --topology net.json       # custom file
    python -m meshcore_optimizer.web --port 9090               # custom port

Author: Stan (Gdańsk MeshCore Network)
License: MIT
"""

import asyncio
import http.server
import io
import json
import os
import re
import socket
import sys
import threading
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs

from meshcore_optimizer.topology import (
    NetworkGraph, widest_path, widest_path_alternatives,
    compute_node_health_penalty,
)


# ---------------------------------------------------------------------------
# Discovery runner — manages background discovery in its own thread + loop
# ---------------------------------------------------------------------------

class DiscoveryRunner:
    """Runs progressive_discovery in a background thread with log capture."""

    def __init__(self):
        self._thread = None
        self._loop = None
        self._task = None
        self._lock = threading.Lock()
        self.status = "idle"      # idle, running, stopping, completed, error
        self.error = ""
        self.logs = []            # list of log lines
        self._max_logs = 500
        self.started_at = ""
        self.stopped_at = ""

    @property
    def running(self):
        return self.status == "running"

    def start(self, config_file="config.json", topology_file="topology.json"):
        with self._lock:
            if self.running:
                return False, "Discovery already running"

            self.status = "running"
            self.error = ""
            self.logs = []
            self.started_at = datetime.now().isoformat(timespec="seconds")
            self.stopped_at = ""

        self._thread = threading.Thread(
            target=self._run_thread,
            args=(config_file, topology_file),
            daemon=True,
        )
        self._thread.start()
        return True, "Discovery started"

    def stop(self):
        with self._lock:
            if not self.running:
                return False, "Discovery not running"
            self.status = "stopping"

        # Cancel the asyncio task from outside the loop
        if self._loop and self._task and not self._task.done():
            self._loop.call_soon_threadsafe(self._task.cancel)
        return True, "Stop requested"

    def get_state(self):
        return {
            "status": self.status,
            "error": self.error,
            "log_count": len(self.logs),
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
        }

    def get_logs(self, since=0):
        """Return logs from index 'since' onward."""
        with self._lock:
            return self.logs[since:]

    def _log(self, line):
        with self._lock:
            self.logs.append(line)
            if len(self.logs) > self._max_logs:
                self.logs = self.logs[-self._max_logs:]

    def _run_thread(self, config_file, topology_file):
        """Thread entry point — sets up asyncio loop and runs discovery."""
        # Lazy imports to avoid circular deps when meshcore isn't installed
        from meshcore_optimizer.config import load_config, Config
        from meshcore_optimizer.radio import connect_radio
        from meshcore_optimizer.discovery import progressive_discovery
        from meshcore_optimizer.topology import NetworkGraph

        # Capture print output
        log_capture = _LogCapture(self._log)
        old_stdout = sys.stdout
        sys.stdout = log_capture

        try:
            # Load config
            config = Config()
            if os.path.exists(config_file):
                config = load_config(config_file)
                self._log(f"Loaded config: {config_file}")
            else:
                self._log(f"Config not found: {config_file}")
                self.status = "error"
                self.error = f"Config not found: {config_file}"
                return

            if not config.companion_prefix:
                self.status = "error"
                self.error = "No companion_prefix in config"
                return

            # Update companion on the handler
            MapHandler.companion_prefix = config.companion_prefix

            # Load existing topology
            graph = NetworkGraph()
            if os.path.exists(topology_file):
                try:
                    graph = NetworkGraph.load(topology_file)
                    s = graph.stats()
                    self._log(f"Loaded topology: {s['nodes']} nodes, "
                              f"{s['edges']} edges")
                except Exception:
                    pass

            # Run discovery
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def _run():
                mc = await connect_radio(config.radio)
                try:
                    await progressive_discovery(
                        mc, graph, config.companion_prefix,
                        config.passwords,
                        max_rounds=config.discovery_max_rounds,
                        timeout=config.discovery_timeout,
                        delay=config.discovery_delay,
                        infer_penalty=config.discovery_infer_penalty,
                        save_file=topology_file,
                        default_guest_passwords=config.default_guest_passwords,
                        radio_config=config.radio,
                        probe_distance_km=config.discovery_probe_distance_km,
                        probe_min_snr=config.discovery_probe_min_snr,
                        neighbor_max_age_h=config.discovery_neighbor_max_age_h,
                    )
                finally:
                    await mc.disconnect()

            self._task = self._loop.create_task(_run())
            try:
                self._loop.run_until_complete(self._task)
                self.status = "completed"
            except asyncio.CancelledError:
                self._log("\nDiscovery stopped by user.")
                self.status = "idle"
            except Exception as e:
                self._log(f"\nDiscovery error: {e}")
                self.status = "error"
                self.error = str(e)
            finally:
                _close_loop(self._loop)
                self._loop = None
                self._task = None
                self.stopped_at = datetime.now().isoformat(timespec="seconds")

        except Exception as e:
            self._log(f"Fatal: {e}")
            self.status = "error"
            self.error = str(e)
        finally:
            sys.stdout = old_stdout


class _LogCapture(io.TextIOBase):
    """Captures print output and forwards to a callback, line by line."""

    def __init__(self, callback):
        self._cb = callback
        self._buf = ""
        self._real = sys.__stdout__

    _TERMINAL_NOISE = re.compile(r'\x1b\[[IO]')

    def write(self, s):
        if self._real:
            self._real.write(s)
        s = self._TERMINAL_NOISE.sub('', s)
        if not s:
            return 0
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._cb(line)
        return len(s)

    def flush(self):
        if self._real:
            self._real.flush()


def _close_loop(loop):
    """Close an asyncio loop, draining pending tasks first."""
    try:
        pending = asyncio.all_tasks(loop)
        if pending:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True))
    except Exception:
        pass
    loop.close()


# Module-level singleton
_discovery = DiscoveryRunner()


# ---------------------------------------------------------------------------
# Node commands — single-node status/neighbors request via radio
# ---------------------------------------------------------------------------

class NodeCommander:
    """Runs a single-node command (status/neighbors) in a background thread.
    Non-blocking: start() returns immediately, poll get_result() for outcome."""

    def __init__(self):
        self._lock = threading.Lock()
        self.busy = False
        self.result = None

    def start(self, action, target_prefix, config_file, topology_file):
        """Start a command. Returns immediately."""
        with self._lock:
            if self.busy:
                return False, "Another command is running"
            if _discovery.running:
                return False, "Discovery is running"
            self.busy = True
            self.result = None

        t = threading.Thread(
            target=self._run_thread,
            args=(action, target_prefix, config_file, topology_file),
            daemon=True,
        )
        t.start()
        return True, "Command started"

    def get_result(self):
        """Poll for result. Returns None while still running."""
        if self.busy:
            return None
        return self.result

    def _run_thread(self, action, target_prefix, config_file, topology_file):
        from meshcore_optimizer.config import load_config, Config, match_passwords
        from meshcore_optimizer.radio import connect_radio
        from meshcore_optimizer.discovery import _login_and_neighbors, _trace_repeater
        from meshcore_optimizer.topology import (
            NetworkGraph, widest_path, round_trip_bottleneck,
            best_bidirectional_path,
        )

        log_capture = _LogCapture(_discovery._log)
        old_stdout = sys.stdout
        sys.stdout = log_capture

        try:
            config = Config()
            if os.path.exists(config_file):
                config = load_config(config_file)

            graph = NetworkGraph()
            if os.path.exists(topology_file):
                graph = NetworkGraph.load(topology_file)

            # Handle trace action separately -- target_prefix is the path
            if action == "trace":
                print(f"  === TRACE: {target_prefix} ===")
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    self.result = loop.run_until_complete(
                        self._async_trace(config, graph,
                                          target_prefix, topology_file))
                finally:
                    _close_loop(loop)
                return

            # Handle disc_path action
            if action == "disc_path":
                target = target_prefix.upper()
                node = graph.get_node(target)
                print(f"  === DISC_PATH: "
                      f"{node.name if node else target} ===")
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    self.result = loop.run_until_complete(
                        self._async_disc_path(config, graph,
                                              target, topology_file))
                finally:
                    _close_loop(loop)
                return

            target = target_prefix.upper()

            node = graph.get_node(target)
            if not node:
                self.result = {"ok": False, "error": f"Node {target} not in topology"}
                return

            if len(node.prefix) < 8:
                # Stub node from 1-byte trace — can't contact directly
                neighbors = []
                for pfx, edges in graph.edges.items():
                    for e in edges:
                        if e.to_prefix == node.prefix and pfx != node.prefix:
                            n = graph.nodes.get(pfx)
                            neighbors.append(n.name if n else pfx)
                for pfx, edges in graph.reverse_edges.items():
                    for e in edges:
                        if e.from_prefix == node.prefix and pfx != node.prefix:
                            n = graph.nodes.get(pfx)
                            neighbors.append(n.name if n else pfx)
                hint = ", ".join(sorted(set(neighbors)))
                self.result = {
                    "ok": False,
                    "error": f"[{node.prefix}] is a stub node (short prefix "
                             f"from 1-byte trace). Login to a neighbor to "
                             f"discover its full identity: {hint}",
                }
                return

            print(f"  === {action.upper()}: "
                  f"{node.name} [{node.prefix}] ===")

            # Resolve companion (may be short prefix like "5364")
            comp_node = graph.get_node(config.companion_prefix)
            companion = comp_node.prefix if comp_node else config.companion_prefix

            fwd_path = widest_path(graph, companion, node.prefix)
            rev_path = widest_path(graph, node.prefix, companion)
            fwd_rt = round_trip_bottleneck(graph, fwd_path) if fwd_path.found else float('-inf')
            rev_rt = round_trip_bottleneck(graph, rev_path) if rev_path.found else float('-inf')

            if fwd_path.found:
                fwd_str = " -> ".join(fwd_path.path_names)
                print(f"  Fwd: {fwd_str} "
                      f"({fwd_path.bottleneck_snr:+.1f} dB, "
                      f"round-trip {fwd_rt:+.1f} dB)")
            if rev_path.found:
                rev_str = " -> ".join(rev_path.path_names)
                print(f"  Rev: {rev_str} "
                      f"({rev_path.bottleneck_snr:+.1f} dB, "
                      f"round-trip {rev_rt:+.1f} dB)")

            path_result = best_bidirectional_path(graph, companion,
                                                     node.prefix)
            if path_result.found:
                choice = "fwd" if fwd_rt >= rev_rt else "rev"
                print(f"  Using {choice} path "
                      f"({path_result.bottleneck_snr:+.1f} dB, "
                      f"{path_result.hop_count} hops)")

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                self.result = loop.run_until_complete(
                    self._async_run(config, graph, node, path_result,
                                    action, topology_file))
            finally:
                _close_loop(loop)

        except Exception as e:
            self.result = {"ok": False, "error": str(e)}
        finally:
            sys.stdout = old_stdout
            _discovery._log(f"--- done ---")
            with self._lock:
                self.busy = False

    async def _async_run(self, config, graph, node, path_result,
                         action, topology_file):
        from meshcore_optimizer.config import match_passwords
        from meshcore_optimizer.radio import (
            connect_radio, find_contact, set_contact_path,
        )

        mc = await connect_radio(config.radio)
        try:
            await mc.ensure_contacts(follow=True)

            contact = find_contact(mc, node.prefix)
            if not contact:
                return {"ok": False, "error": f"No contact for {node.name}"}

            await set_contact_path(mc, contact, path_result)

            pw_list = match_passwords(
                node, config.passwords,
                config.default_guest_passwords)
            # For single-node commands, limit password attempts to
            # avoid long waits (each attempt can take 10s+ over radio)
            pw_list = pw_list[:2]

            if action == "status":
                return await self._do_status_only(
                    mc, contact, node, pw_list, config, graph, topology_file)
            else:
                return await self._do_neighbors(
                    mc, contact, node, pw_list, config, graph, topology_file)
        finally:
            await mc.disconnect()

    async def _async_trace(self, config, graph, trace_path, topology_file):
        """Send a manual trace and return results."""
        from meshcore_optimizer.radio import connect_radio
        from meshcore_optimizer.discovery import _trace_repeater

        comp_node = graph.get_node(config.companion_prefix)
        companion = comp_node.prefix if comp_node else config.companion_prefix

        # Resolve hop names for display
        hops = trace_path.split(",")
        hop_names = []
        for h in hops:
            resolved = None
            for pfx, n in graph.nodes.items():
                if pfx[:len(h)].lower() == h.lower():
                    resolved = n.name
                    break
            hop_names.append(f"{resolved or '?'} [{h}]")
        print(f"  Path: {' -> '.join(hop_names)}")

        mc = await connect_radio(config.radio)
        try:
            await mc.ensure_contacts(follow=True)

            # Use the first hop's contact (companion)
            contact = None
            for pub_key, ct in mc.contacts.items():
                if isinstance(ct, dict):
                    contact = ct
                    break

            if not contact:
                return {"ok": False, "error": "No contacts available"}

            # Determine target (last unique hop before return)
            target_hop = hops[len(hops) // 2] if len(hops) > 1 else hops[0]
            target_pfx = None
            for pfx in graph.nodes:
                if pfx[:len(target_hop)].lower() == target_hop.lower():
                    target_pfx = pfx
                    break
            target_pfx = target_pfx or target_hop.upper()

            edges_before = graph.stats()['edges']
            ok, t_edges, err = await _trace_repeater(
                mc, contact, companion, target_pfx,
                graph, config.discovery_timeout,
                forced_trace_path=trace_path)

            if ok and t_edges > 0:
                graph.infer_reverse_edges(config.discovery_infer_penalty)
                graph.save(topology_file)

            s = graph.stats()
            return {
                "ok": ok,
                "edges_added": t_edges,
                "error": err or "",
                "graph_nodes": s["nodes"],
                "graph_edges": s["edges"],
            }
        finally:
            await mc.disconnect()

    async def _async_disc_path(self, config, graph, target_prefix,
                               topology_file):
        """Send disc_path flood and return firmware's route with analysis."""
        from meshcore_optimizer.radio import (
            connect_radio, find_contact, build_contact_map,
        )
        from meshcore_optimizer.discovery import (
            _decode_path_hops, _resolve_hop, _is_endpoint_prefix,
            analyze_and_probe_flood,
        )
        from meshcore import EventType

        node = graph.get_node(target_prefix)
        if not node:
            return {"ok": False, "error": f"Node {target_prefix} not found"}

        comp_node = graph.get_node(config.companion_prefix)
        companion = comp_node.prefix if comp_node else config.companion_prefix

        mc = await connect_radio(config.radio)
        try:
            await mc.ensure_contacts(follow=True)
            contact = find_contact(mc, node.prefix)
            if not contact:
                return {"ok": False, "error": f"No contact for {node.name}"}

            path_queue = asyncio.Queue()
            def _on_path(event):
                path_queue.put_nowait(event)
            sub = mc.subscribe(EventType.PATH_RESPONSE, _on_path)

            try:
                print(f"  TX: disc_path to {node.name} [{node.prefix[:4]}]...")
                res = await asyncio.wait_for(
                    mc.commands.send_path_discovery(contact),
                    timeout=config.discovery_timeout)

                if res is None or res.type == EventType.ERROR:
                    sub.unsubscribe()
                    print(f"  TX: disc_path send failed")
                    return {"ok": False, "error": "disc_path send failed"}

                print(f"  Waiting for response "
                      f"(timeout={config.discovery_timeout}s)...")
                try:
                    ev = await asyncio.wait_for(
                        path_queue.get(),
                        timeout=config.discovery_timeout)
                except asyncio.TimeoutError:
                    print(f"  RX: No response (timeout)")
                    return {"ok": False, "error": "No response (timeout)"}
                finally:
                    sub.unsubscribe()

                out_path_hex = ev.payload.get("out_path", "")
                in_path_hex = ev.payload.get("in_path", "")
                out_hlen = ev.payload.get("out_path_hash_len", 1)
                in_hlen = ev.payload.get("in_path_hash_len", 1)

                # Build display info for the web UI response
                result = {"ok": True, "node": node.prefix, "name": node.name}

                for direction, path_hex, hlen, is_out in [
                    ("out_path", out_path_hex, out_hlen, True),
                    ("in_path", in_path_hex, in_hlen, False),
                ]:
                    hops = _decode_path_hops(path_hex, hlen)
                    resolved = [_resolve_hop(h, graph) for h in hops]
                    # in_path is target→companion; reverse to
                    # companion→target so both paths are consistent
                    if not is_out:
                        hops = list(reversed(hops))
                        resolved = list(reversed(resolved))
                    result[direction] = self._build_disc_path_info(
                        graph, companion, comp_node, node,
                        path_hex, hops, resolved, is_out)

                # Build contact map and probe missing edges
                # using shared function from discovery.py
                contact_map, _ = build_contact_map(mc)

                def _save():
                    graph.save(topology_file)

                new_edges = await analyze_and_probe_flood(
                    mc, graph, companion, node.prefix,
                    contact_map, config.discovery_timeout,
                    config.discovery_delay,
                    config.discovery_infer_penalty,
                    out_path_hex, in_path_hex,
                    out_hlen=out_hlen, in_hlen=in_hlen,
                    on_save=_save)

                if new_edges > 0:
                    result["edges_probed"] = new_edges

                return result

            except Exception as e:
                sub.unsubscribe()
                return {"ok": False, "error": str(e)}

        finally:
            await mc.disconnect()

    @staticmethod
    def _build_disc_path_info(graph, companion, comp_node, node,
                              path_hex, hops, resolved, is_out):
        """Build display info dict for one direction of a disc_path result."""
        if not hops:
            # Direct path (no intermediaries)
            if is_out:
                src, dst = companion, node.prefix
                src_name = comp_node.name if comp_node else companion[:4]
                dst_name = node.name
            else:
                src, dst = node.prefix, companion
                src_name = node.name
                dst_name = comp_node.name if comp_node else companion[:4]
            info = {
                "path": [src, dst], "path_names": [src_name, dst_name],
                "hop_count": 1, "bottleneck_snr": None, "missing_edges": [],
            }
            e = graph.get_edge(src, dst)
            if e:
                info["bottleneck_snr"] = round(e.snr_db, 2)
            return info

        # Filter endpoint hops, keep intermediaries
        from meshcore_optimizer.discovery import _is_endpoint_prefix
        intermediates = [r for r in resolved
                         if r and not _is_endpoint_prefix(r, companion, node.prefix)]
        full = [companion] + intermediates + [node.prefix]
        if not is_out:
            full = list(reversed(full))

        names = []
        bottleneck = None
        missing_edges = []
        for pfx in full:
            n = graph.nodes.get(pfx)
            names.append(n.name if n else f"[{pfx[:4]}]")
        for i in range(len(full) - 1):
            a, b = full[i], full[i + 1]
            if not a or not b or a == b:
                continue
            edge = graph.get_edge(a, b)
            rev_edge = graph.get_edge(b, a)
            if edge:
                snr = edge.snr_db
                if bottleneck is None or snr < bottleneck:
                    bottleneck = snr
            elif rev_edge:
                snr = rev_edge.snr_db - 2.0
                if bottleneck is None or snr < bottleneck:
                    bottleneck = snr
            else:
                missing_edges.append({
                    "from": a, "to": b,
                    "from_name": graph.nodes[a].name if a in graph.nodes else a[:4],
                    "to_name": graph.nodes[b].name if b in graph.nodes else b[:4],
                })
        return {
            "path": full,
            "path_names": names,
            "hop_count": len(full) - 1,
            "bottleneck_snr": round(bottleneck, 2) if bottleneck is not None else None,
            "missing_edges": missing_edges,
        }

    async def _do_status_only(self, mc, contact, node, pw_list,
                              config, graph, topology_file):
        """Login, fetch status only (no neighbors), logout."""
        from meshcore_optimizer.radio import login_to_node, fetch_status

        timeout = config.discovery_timeout

        for pw_entry in pw_list:
            ok, err = await login_to_node(
                mc, contact, node.name, pw_entry.password, timeout,
                max_wait=15)
            if not ok:
                if err == "CONNECTION_LOST":
                    return {"ok": False, "error": "Connection lost"}
                continue

            await fetch_status(mc, contact, node, timeout)

            try:
                await mc.commands.send_logout(contact)
            except Exception:
                pass

            graph.save(topology_file)
            s = graph.stats()
            return {
                "ok": True, "node": node.prefix, "name": node.name,
                "status": node.status or {},
                "health_penalty": node.health_penalty,
                "graph_nodes": s["nodes"], "graph_edges": s["edges"],
            }

        return {"ok": False, "error": "All passwords failed"}

    async def _do_neighbors(self, mc, contact, node, pw_list,
                            config, graph, topology_file):
        """Login, fetch status + neighbors, logout."""
        from meshcore_optimizer.discovery import _login_and_neighbors

        for pw_entry in pw_list:
            ok, n_edges, err, used_pw = await _login_and_neighbors(
                mc, contact, node, pw_entry, graph,
                config.discovery_timeout, max_login_wait=15)

            if not ok:
                if err == "CONNECTION_LOST":
                    return {"ok": False, "error": "Connection lost"}
                continue

            graph.infer_reverse_edges(config.discovery_infer_penalty)
            graph.save(topology_file)
            s = graph.stats()
            return {
                "ok": True, "node": node.prefix, "name": node.name,
                "edges_added": n_edges,
                "graph_nodes": s["nodes"], "graph_edges": s["edges"],
            }

        return {"ok": False, "error": "All passwords failed"}


_commander = NodeCommander()


# ---------------------------------------------------------------------------
# API handler
# ---------------------------------------------------------------------------

class MapHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for the map web interface."""

    topology_file = "topology.json"
    config_file = "config.json"
    companion_prefix = ""
    _graph_ref = None
    _last_good_topo = None

    def log_message(self, format, *args):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except BrokenPipeError:
            pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _load_graph(self):
        if self._graph_ref is not None:
            return self._graph_ref
        try:
            g = NetworkGraph.load(self.topology_file)
            MapHandler._last_good_topo = g
            return g
        except Exception:
            return MapHandler._last_good_topo or NetworkGraph()

    def _graph_to_json(self, graph):
        nodes = {}
        for pfx, node in graph.nodes.items():
            nodes[pfx] = {
                "name": node.name, "prefix": node.prefix,
                "lat": node.lat, "lon": node.lon,
                "access_level": node.access_level,
                "last_seen": node.last_seen,
                "status": node.status,
                "status_timestamp": node.status_timestamp,
                "health_penalty": node.health_penalty,
            }
        edges = []
        for from_p, edge_list in graph.edges.items():
            for e in edge_list:
                ed = {
                    "from": e.from_prefix, "to": e.to_prefix,
                    "snr_db": round(e.snr_db, 2),
                    "source": e.source, "confidence": e.confidence,
                }
                if e.snr_min_db is not None:
                    ed["snr_min_db"] = round(e.snr_min_db, 2)
                if e.observation_count > 1:
                    ed["observation_count"] = e.observation_count
                edges.append(ed)
        return {
            "nodes": nodes, "edges": edges,
            "companion_prefix": self.companion_prefix,
            "stats": graph.stats(),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }

    def _read_post_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            return json.loads(self.rfile.read(length))
        return {}

    def _load_config_dict(self):
        """Load config file as raw dict, or return defaults."""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "radio": {"protocol": "tcp", "host": "", "port": 5000},
            "companion_prefix": "",
            "discovery": {
                "max_rounds": 5, "timeout": 30.0, "delay": 5.0,
                "infer_penalty": 5.0, "save_file": "topology.json",
            },
            "passwords": [],
            "default_guest_passwords": ["", "hello"],
        }

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self._send_html(MAP_HTML)
        elif path == "/api/topology":
            graph = self._load_graph()
            self._send_json(self._graph_to_json(graph))
        elif path == "/api/path":
            self._handle_path(params)
        elif path == "/api/discovery/status":
            state = _discovery.get_state()
            since = int(params.get("log_since", ["0"])[0])
            state["logs"] = _discovery.get_logs(since)
            state["command_busy"] = _commander.busy
            self._send_json(state)
        elif path == "/api/config":
            cfg = self._load_config_dict()
            cfg["config_exists"] = os.path.exists(self.config_file)
            self._send_json(cfg)
        elif path == "/api/node/result":
            result = _commander.get_result()
            if result is None:
                self._send_json({"busy": True})
            else:
                result["busy"] = False
                self._send_json(result)
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/discovery/start":
            ok, msg = _discovery.start(
                config_file=self.config_file,
                topology_file=self.topology_file,
            )
            self._send_json({"ok": ok, "message": msg})
        elif path == "/api/discovery/stop":
            ok, msg = _discovery.stop()
            self._send_json({"ok": ok, "message": msg})
        elif path == "/api/config":
            self._handle_save_config()
        elif path == "/api/radio/test":
            self._handle_radio_test()
        elif path == "/api/node/command":
            self._handle_node_command()
        elif path == "/api/trace":
            self._handle_trace()
        elif path == "/api/path/firmware":
            self._handle_firmware_path()
        else:
            self.send_error(404)

    def _handle_save_config(self):
        body = self._read_post_body()
        try:
            with open(self.config_file, 'w') as f:
                json.dump(body, f, indent=2)
            MapHandler.companion_prefix = body.get("companion_prefix", "")
            # Apply health weights
            from meshcore_optimizer.topology import set_health_weights
            set_health_weights(body.get("health_penalties"))
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 500)

    def _handle_radio_test(self):
        """Test radio connection and return list of repeaters."""
        body = self._read_post_body()
        protocol = body.get("protocol", "tcp")
        host = body.get("host", "")
        port = body.get("port", 5000)
        serial_port = body.get("serial_port", "")
        baudrate = body.get("baudrate", 115200)
        ble_address = body.get("ble_address", "")

        result = {"ok": False, "repeaters": [], "error": ""}

        def _run():
            from meshcore_optimizer.config import RadioConfig
            from meshcore_optimizer.radio import connect_radio
            rc = RadioConfig(protocol=protocol, host=host, port=int(port),
                             serial_port=serial_port, baudrate=int(baudrate),
                             ble_address=ble_address)

            async def _test():
                mc = await connect_radio(rc)
                try:
                    await mc.ensure_contacts(follow=True)
                    for pub_key, ct in mc.contacts.items():
                        if not isinstance(ct, dict):
                            continue
                        if ct.get('type', 0) == 2:
                            pfx = pub_key[:8].upper()
                            name = ct.get('adv_name', '') or f"[{pfx}]"
                            result["repeaters"].append({
                                "prefix": pfx, "name": name,
                            })
                    result["ok"] = True
                finally:
                    await mc.disconnect()

            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    asyncio.wait_for(_test(), timeout=15))
            finally:
                _close_loop(loop)

        try:
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=20)
            if t.is_alive():
                result["error"] = "Connection timeout"
            elif not result["ok"] and not result["error"]:
                result["error"] = "Connection failed"
        except Exception as e:
            result["error"] = str(e)

        result["repeaters"].sort(key=lambda r: r["name"])
        self._send_json(result)

    def _handle_node_command(self):
        body = self._read_post_body()
        action = body.get("action", "")
        target = body.get("prefix", "")
        if action not in ("status", "neighbors"):
            self._send_json({"ok": False, "error": "action must be 'status' or 'neighbors'"}, 400)
            return
        if not target:
            self._send_json({"ok": False, "error": "prefix required"}, 400)
            return
        ok, msg = _commander.start(
            action, target,
            self.config_file, self.topology_file,
        )
        self._send_json({"ok": ok, "message": msg})

    def _handle_trace(self):
        body = self._read_post_body()
        trace_path = body.get("path", "").strip()
        if not trace_path:
            self._send_json({"ok": False, "error": "path required"}, 400)
            return
        ok, msg = _commander.start(
            "trace", trace_path,
            self.config_file, self.topology_file,
        )
        self._send_json({"ok": ok, "message": msg})

    def _handle_firmware_path(self):
        body = self._read_post_body()
        target = body.get("prefix", "").strip()
        if not target:
            self._send_json({"ok": False, "error": "prefix required"}, 400)
            return
        ok, msg = _commander.start(
            "disc_path", target,
            self.config_file, self.topology_file,
        )
        self._send_json({"ok": ok, "message": msg})

    def _handle_path(self, params):
        src = params.get("from", [""])[0]
        dst = params.get("to", [""])[0]
        use_health = params.get("health", ["0"])[0] == "1"
        k = min(int(params.get("k", ["3"])[0]), 5)

        if not src or not dst:
            self._send_json({"error": "from and to required"}, 400)
            return

        graph = self._load_graph()
        src_node = graph.get_node(src)
        dst_node = graph.get_node(dst)
        if not src_node or not dst_node:
            self._send_json({"error": "node not found"}, 404)
            return

        cfg = self._load_config_dict()
        hp = cfg.get("discovery", {}).get("hop_penalty", 1.0)

        fwd = widest_path_alternatives(
            graph, src_node.prefix, dst_node.prefix,
            k=k, use_node_health=use_health, hop_penalty=hp)
        rev = widest_path_alternatives(
            graph, dst_node.prefix, src_node.prefix,
            k=k, use_node_health=use_health, hop_penalty=hp)

        def _pr(pr):
            result = {
                "path": pr.path, "path_names": pr.path_names,
                "bottleneck_snr": round(pr.bottleneck_snr, 2),
                "hop_count": pr.hop_count,
                "edges": [{"from": e.from_prefix, "to": e.to_prefix,
                           "snr_db": round(e.snr_db, 2),
                           "snr_min_db": round(e.snr_min_db, 2) if e.snr_min_db is not None else None,
                           "observation_count": e.observation_count,
                           "source": e.source,
                           "confidence": round(e.confidence, 2)}
                          for e in pr.edges],
            }
            if use_health:
                result["node_health"] = {
                    pfx: round(graph.nodes[pfx].health_penalty, 1)
                    for pfx in pr.path[1:-1]
                    if pfx in graph.nodes
                    and graph.nodes[pfx].health_penalty > 0
                }
            return result

        resp = {
            "paths": [_pr(p) for p in fwd],
            "reverse_paths": [_pr(p) for p in rev],
            "health_aware": use_health,
            "hop_penalty": hp,
        }
        if not fwd:
            resp["diag"] = {
                "src_edges": len(graph.edges.get(src_node.prefix, [])),
                "dst_edges": len(graph.reverse_edges.get(dst_node.prefix, [])),
            }
        self._send_json(resp)

from meshcore_optimizer.web_template import MAP_HTML



# ---------------------------------------------------------------------------
# Server start / stop
# ---------------------------------------------------------------------------

class _ThreadedHTTPServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def start_map_server(graph=None, companion_prefix="",
                     topology_file="topology.json", port=8080,
                     config_file="config.json"):
    """Start the map web server in a background daemon thread."""
    MapHandler.topology_file = topology_file
    MapHandler.config_file = config_file
    MapHandler.companion_prefix = companion_prefix
    MapHandler._graph_ref = graph

    server = _ThreadedHTTPServer(("0.0.0.0", port), MapHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    lan_ip = _get_lan_ip()
    return f"http://{lan_ip}:{port}"


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="MeshCore Interactive Network Map")
    parser.add_argument("--topology", "-f", default="topology.json",
                        help="Topology JSON file (default: topology.json)")
    parser.add_argument("--config", "-C", default="config.json",
                        help="Config file for companion prefix")
    parser.add_argument("--port", "-p", type=int, default=8080,
                        help="HTTP port (default: 8080)")
    args = parser.parse_args()

    companion = ""
    if os.path.exists(args.config):
        try:
            with open(args.config) as f:
                cfg = json.load(f)
            companion = cfg.get("companion_prefix", "")
            # Apply health penalty weights from config
            from meshcore_optimizer.topology import set_health_weights
            set_health_weights(cfg.get("health_penalties"))
        except Exception:
            pass

    url = start_map_server(
        topology_file=args.topology,
        companion_prefix=companion,
        port=args.port,
        config_file=args.config,
    )

    print(f"MeshCore Map: {url}")
    print(f"Topology:     {args.topology}")
    print(f"Open the URL above in a browser on any device on your network.")
    print(f"Press Ctrl+C to stop.\n")

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
