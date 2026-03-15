#!/usr/bin/env python3
"""
MeshCore Interactive Network Map
=================================
Web-based map for visualizing mesh network topology, node health,
interactive path finding, and live discovery control.

Usage:
    python meshcore_web.py                          # default topology.json
    python meshcore_web.py --topology net.json       # custom file
    python meshcore_web.py --port 9090               # custom port

Author: Stan (Gdańsk MeshCore Network)
License: MIT
"""

import asyncio
import http.server
import io
import json
import os
import socket
import sys
import threading
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs

from meshcore_topology import (
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
        return self.logs[since:]

    def _log(self, line):
        self.logs.append(line)
        if len(self.logs) > self._max_logs:
            self.logs = self.logs[-self._max_logs:]

    def _run_thread(self, config_file, topology_file):
        """Thread entry point — sets up asyncio loop and runs discovery."""
        # Lazy imports to avoid circular deps when meshcore isn't installed
        from meshcore_discovery import (
            load_config, Config, connect_radio, progressive_discovery,
            NetworkGraph,
        )

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
                self._loop.close()
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

    def write(self, s):
        if self._real:
            self._real.write(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._cb(line)
        return len(s)

    def flush(self):
        if self._real:
            self._real.flush()


# Module-level singleton
_discovery = DiscoveryRunner()


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
                edges.append({
                    "from": e.from_prefix, "to": e.to_prefix,
                    "snr_db": round(e.snr_db, 2),
                    "source": e.source, "confidence": e.confidence,
                })
        return {
            "nodes": nodes, "edges": edges,
            "companion_prefix": self.companion_prefix,
            "stats": graph.stats(),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
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
            self._send_json(state)
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
        else:
            self.send_error(404)

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

        fwd = widest_path_alternatives(
            graph, src_node.prefix, dst_node.prefix,
            k=k, use_node_health=use_health)
        rev = widest_path_alternatives(
            graph, dst_node.prefix, src_node.prefix,
            k=k, use_node_health=use_health)

        def _pr(pr):
            return {
                "path": pr.path, "path_names": pr.path_names,
                "bottleneck_snr": round(pr.bottleneck_snr, 2),
                "hop_count": pr.hop_count,
                "edges": [{"from": e.from_prefix, "to": e.to_prefix,
                           "snr_db": round(e.snr_db, 2)} for e in pr.edges],
            }

        self._send_json({
            "paths": [_pr(p) for p in fwd],
            "reverse_paths": [_pr(p) for p in rev],
            "health_aware": use_health,
        })


# ---------------------------------------------------------------------------
# Frontend HTML/JS/CSS
# ---------------------------------------------------------------------------

MAP_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MeshCore Network Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #1a1a2e; color: #e0e0e0; }

#map { position: absolute; top: 48px; bottom: 0; left: 0; right: 320px;
       transition: right 0.2s; }
#map.panel-closed { right: 0; }

#topbar {
    height: 48px; background: #16213e; display: flex;
    align-items: center; padding: 0 16px; gap: 12px;
    border-bottom: 1px solid #0f3460; z-index: 1000; position: relative;
}
#topbar h1 { font-size: 16px; color: #00d4ff; white-space: nowrap; }
#topbar .stats { font-size: 13px; color: #8899aa; }
#topbar .controls { margin-left: auto; display: flex; gap: 8px; align-items: center; }

button, .btn {
    background: #0f3460; border: 1px solid #1a5276; color: #e0e0e0;
    padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 13px;
}
button:hover, .btn:hover { background: #1a5276; }
button.active { background: #00d4ff; color: #1a1a2e; }
button.danger { background: #8b0000; border-color: #b22222; }
button.danger:hover { background: #b22222; }
button.success { background: #1b5e20; border-color: #2e7d32; }
button.success:hover { background: #2e7d32; }
label { font-size: 13px; color: #8899aa; cursor: pointer; }

/* --- Side Panel --- */
#panel {
    position: absolute; top: 48px; bottom: 0; right: 0; width: 320px;
    background: #16213e; border-left: 1px solid #0f3460;
    display: flex; flex-direction: column; z-index: 900;
    transition: transform 0.2s;
}
#panel.closed { transform: translateX(100%); }

.panel-section {
    padding: 12px 14px; border-bottom: 1px solid #0f3460;
}
.panel-section h3 {
    font-size: 13px; color: #00d4ff; margin-bottom: 8px;
    text-transform: uppercase; letter-spacing: 0.5px;
}
.panel-section select {
    width: 100%; padding: 6px 8px; border-radius: 4px;
    background: #0d1b2a; border: 1px solid #1a5276; color: #e0e0e0;
    font-size: 13px; margin-bottom: 6px;
}
.panel-row { display: flex; gap: 6px; align-items: center; margin-bottom: 6px; }
.panel-row label { flex-shrink: 0; }

#path-result {
    padding: 8px 14px; font-size: 12px; line-height: 1.6;
    max-height: 180px; overflow-y: auto;
}
#path-result .path-primary { color: #00d4ff; font-weight: bold; }
#path-result .path-alt { color: #888; }
#path-result .bottleneck { color: #ffaa00; }
#path-result .reverse { color: #ff66aa; }

/* Discovery log */
#discovery-log {
    flex: 1; overflow-y: auto; padding: 8px 10px;
    font-family: 'Cascadia Code', 'Fira Code', monospace;
    font-size: 11px; line-height: 1.4; color: #8899aa;
    background: #0d1b2a; min-height: 80px;
}
#discovery-log .log-line { white-space: pre-wrap; word-break: break-all; }
#discovery-status {
    padding: 6px 14px; font-size: 12px;
    border-top: 1px solid #0f3460; border-bottom: 1px solid #0f3460;
    display: flex; align-items: center; gap: 8px;
}
.status-dot {
    width: 8px; height: 8px; border-radius: 50%; display: inline-block;
}
.status-dot.idle { background: #666; }
.status-dot.running { background: #4caf50; animation: pulse 1.5s infinite; }
.status-dot.error { background: #f44336; }
.status-dot.completed { background: #00d4ff; }
.status-dot.stopping { background: #ff9800; }
@keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.4; } }

/* --- Popups --- */
.leaflet-popup-content-wrapper {
    background: #16213e !important; color: #e0e0e0 !important;
    border: 1px solid #0f3460 !important; border-radius: 8px !important;
}
.leaflet-popup-tip { background: #16213e !important; }
.leaflet-popup-content { font-size: 13px; line-height: 1.5; }
.leaflet-popup-content .node-name { font-weight: bold; color: #00d4ff; font-size: 14px; }
.leaflet-popup-content .health-ok { color: #4caf50; }
.leaflet-popup-content .health-warn { color: #ff9800; }
.leaflet-popup-content .health-bad { color: #f44336; }
.leaflet-popup-content .snr-good { color: #4caf50; }
.leaflet-popup-content .snr-marginal { color: #ff9800; }
.leaflet-popup-content .snr-bad { color: #f44336; }
.leaflet-popup-content table { border-collapse: collapse; margin-top: 4px; }
.leaflet-popup-content td { padding: 1px 6px; }
.leaflet-popup-content .btn {
    display: inline-block; margin: 4px 4px 0 0; padding: 2px 10px;
    font-size: 12px; text-decoration: none;
}

.legend {
    background: #16213e; border: 1px solid #0f3460; border-radius: 6px;
    padding: 8px 12px; font-size: 12px; line-height: 1.6; color: #8899aa;
}
.legend b { color: #e0e0e0; }
.legend-row { display: flex; align-items: center; gap: 6px; }
.legend-dot { width: 12px; height: 12px; border-radius: 50%; display: inline-block; }
.legend-line { width: 24px; height: 3px; display: inline-block; border-radius: 1px; }
.no-gps-badge { font-size: 10px; color: #ff9800; font-style: italic; }
</style>
</head>
<body>

<div id="topbar">
    <h1>MeshCore Network Map</h1>
    <span class="stats" id="stats"></span>
    <div class="controls">
        <label><input type="checkbox" id="chkAutoRefresh" checked> Auto-refresh</label>
        <button onclick="refreshTopology()">Refresh</button>
        <button id="btnPanel" onclick="togglePanel()" class="active">Panel</button>
    </div>
</div>

<div id="map"></div>

<!-- Side Panel -->
<div id="panel">
    <!-- Path Finder -->
    <div class="panel-section">
        <h3>Find Path</h3>
        <select id="selFrom"><option value="">-- From --</option></select>
        <select id="selTo"><option value="">-- To --</option></select>
        <div class="panel-row">
            <label><input type="checkbox" id="chkHealth"> Health-aware</label>
            <button onclick="panelFindPath()" style="margin-left:auto">Find</button>
            <button onclick="clearPath()">Clear</button>
        </div>
    </div>
    <div id="path-result"></div>

    <!-- Discovery -->
    <div class="panel-section">
        <h3>Discovery</h3>
        <div class="panel-row">
            <button id="btnDiscStart" class="success" onclick="discoveryStart()">Start</button>
            <button id="btnDiscStop" class="danger" onclick="discoveryStop()" disabled>Stop</button>
        </div>
    </div>
    <div id="discovery-status">
        <span class="status-dot idle" id="discDot"></span>
        <span id="discStatusText">Idle</span>
    </div>
    <div id="discovery-log"></div>
</div>

<script>
// --- State ---
let map, topo = null, markers = {}, edgeLines = [], pathLines = [];
let firstLoad = true;
let pathFrom = null, pathTo = null;
let companionPrefix = '';
let refreshTimer = null;
let discLogSince = 0;
let discPollTimer = null;
const REFRESH_MS = 10000;

// --- Map init ---
map = L.map('map', { zoomControl: true, attributionControl: false })
    .setView([54.35, 18.65], 11);

L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    maxZoom: 19,
}).addTo(map);

// Legend
const legend = L.control({position: 'bottomright'});
legend.onAdd = function() {
    const div = L.DomUtil.create('div', 'legend');
    div.innerHTML = `
        <b>Nodes</b><br>
        <div class="legend-row"><span class="legend-dot" style="background:#4caf50"></span> Healthy</div>
        <div class="legend-row"><span class="legend-dot" style="background:#ff9800"></span> Degraded</div>
        <div class="legend-row"><span class="legend-dot" style="background:#f44336"></span> Critical</div>
        <div class="legend-row"><span class="legend-dot" style="background:#0088ff;border:2px solid #00d4ff"></span> Home</div>
        <div class="legend-row"><span class="legend-dot" style="background:#666;border:2px dashed #999"></span> No GPS</div>
        <br><b>Links</b><br>
        <div class="legend-row"><span class="legend-line" style="background:#4caf50"></span> &gt;5 dB</div>
        <div class="legend-row"><span class="legend-line" style="background:#ff9800"></span> 0-5 dB</div>
        <div class="legend-row"><span class="legend-line" style="background:#f44336"></span> &lt;0 dB</div>
    `;
    return div;
};
legend.addTo(map);

// --- Panel toggle ---
function togglePanel() {
    const panel = document.getElementById('panel');
    const mapEl = document.getElementById('map');
    const btn = document.getElementById('btnPanel');
    panel.classList.toggle('closed');
    mapEl.classList.toggle('panel-closed');
    btn.classList.toggle('active');
    setTimeout(() => map.invalidateSize(), 250);
}

// --- Helpers ---
function snrColor(snr) {
    if (snr >= 5) return '#4caf50';
    if (snr >= 0) return '#ff9800';
    return '#f44336';
}
function snrClass(snr) {
    if (snr >= 5) return 'snr-good';
    if (snr >= 0) return 'snr-marginal';
    return 'snr-bad';
}
function healthColor(penalty) {
    if (penalty <= 0) return '#4caf50';
    if (penalty < 4) return '#ff9800';
    return '#f44336';
}
function healthClass(penalty) {
    if (penalty <= 0) return 'health-ok';
    if (penalty < 4) return 'health-warn';
    return 'health-bad';
}
function formatUptime(secs) {
    if (!secs) return '-';
    const d = Math.floor(secs / 86400);
    const h = Math.floor((secs % 86400) / 3600);
    return d > 0 ? `${d}d ${h}h` : `${h}h`;
}
function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// --- Position estimation for no-GPS nodes ---
function estimatePosition(node, nodes, edges) {
    const neighborPfxs = new Set();
    for (const e of edges) {
        if (e.from === node.prefix) neighborPfxs.add(e.to);
        if (e.to === node.prefix) neighborPfxs.add(e.from);
    }
    const gpsNeighbors = [];
    for (const pfx of neighborPfxs) {
        const n = nodes[pfx];
        if (n && n.lat && n.lon) gpsNeighbors.push(n);
    }
    if (gpsNeighbors.length > 0) {
        const lat = gpsNeighbors.reduce((s,n) => s + n.lat, 0) / gpsNeighbors.length;
        const lon = gpsNeighbors.reduce((s,n) => s + n.lon, 0) / gpsNeighbors.length;
        const angle = Math.random() * 2 * Math.PI;
        return { lat: lat + 0.005*Math.cos(angle), lon: lon + 0.008*Math.sin(angle), estimated: true };
    }
    return null;
}

function buildEdgeLookup(edges) {
    const lookup = {};
    for (const e of edges) lookup[e.from + ':' + e.to] = e;
    return lookup;
}

// --- Populate dropdowns ---
function populateNodeSelects() {
    if (!topo) return;
    const nodes = Object.entries(topo.nodes)
        .map(([pfx, n]) => ({ pfx, name: n.name }))
        .sort((a, b) => a.name.localeCompare(b.name));

    for (const selId of ['selFrom', 'selTo']) {
        const sel = document.getElementById(selId);
        const curVal = sel.value;
        const label = selId === 'selFrom' ? '-- From --' : '-- To --';
        sel.innerHTML = `<option value="">${label}</option>`;
        for (const n of nodes) {
            const opt = document.createElement('option');
            opt.value = n.pfx;
            opt.textContent = n.name;
            if (n.pfx === curVal) opt.selected = true;
            sel.appendChild(opt);
        }
    }
}

// --- Render topology ---
function renderTopology(data) {
    topo = data;
    companionPrefix = data.companion_prefix || '';

    const s = data.stats;
    document.getElementById('stats').textContent =
        `${s.nodes} nodes | ${s.edges} edges | ${data.timestamp}`;

    // Clear existing
    Object.values(markers).forEach(m => map.removeLayer(m));
    markers = {};
    edgeLines.forEach(l => map.removeLayer(l));
    edgeLines = [];

    const nodes = data.nodes;
    const edges = data.edges;
    const edgeLookup = buildEdgeLookup(edges);

    // Position no-GPS nodes
    let gridIdx = 0;
    let lats = [], lons = [];
    for (const n of Object.values(nodes)) {
        if (n.lat && n.lon) { lats.push(n.lat); lons.push(n.lon); }
    }
    const minLat = lats.length ? Math.min(...lats) - 0.03 : 54.3;
    const lonCenter = lats.length ? (Math.min(...lons) + Math.max(...lons)) / 2 : 18.6;
    const lonSpan = lats.length ? Math.max(...lons) - Math.min(...lons) : 0.5;

    for (const [pfx, node] of Object.entries(nodes)) {
        if (!node.lat || !node.lon) {
            const est = estimatePosition(node, nodes, edges);
            if (est) { node._lat = est.lat; node._lon = est.lon; node._estimated = true; }
            else {
                node._lat = minLat - 0.01;
                node._lon = lonCenter - lonSpan/3 + gridIdx * (lonSpan/8);
                node._estimated = true; gridIdx++;
            }
        } else { node._lat = node.lat; node._lon = node.lon; node._estimated = false; }
    }

    // Draw edges
    const drawnPairs = new Set();
    for (const e of edges) {
        const fromN = nodes[e.from], toN = nodes[e.to];
        if (!fromN || !toN) continue;
        const pairKey = [e.from, e.to].sort().join(':');
        if (drawnPairs.has(pairKey)) continue;
        drawnPairs.add(pairKey);

        const rev = edgeLookup[e.to + ':' + e.from];
        const worstSnr = rev ? Math.min(e.snr_db, rev.snr_db) : e.snr_db;

        const line = L.polyline(
            [[fromN._lat, fromN._lon], [toN._lat, toN._lon]],
            { color: snrColor(worstSnr),
              weight: Math.max(1, Math.min(4, (worstSnr+10)/5)),
              opacity: 0.4,
              dashArray: e.source === 'inferred' ? '5,5' : null }
        );

        let popup = `<b>${escHtml(fromN.name)}</b> ↔ <b>${escHtml(toN.name)}</b><br><table>`;
        popup += `<tr><td>${escHtml(fromN.name)} →</td><td class="${snrClass(e.snr_db)}">${e.snr_db >= 0?'+':''}${e.snr_db.toFixed(1)} dB</td><td>[${e.source}]</td></tr>`;
        if (rev) popup += `<tr><td>${escHtml(toN.name)} →</td><td class="${snrClass(rev.snr_db)}">${rev.snr_db >= 0?'+':''}${rev.snr_db.toFixed(1)} dB</td><td>[${rev.source}]</td></tr>`;
        else popup += `<tr><td>${escHtml(toN.name)} →</td><td style="color:#666">unknown</td></tr>`;
        popup += `</table>`;
        line.bindPopup(popup);
        line.addTo(map);
        edgeLines.push(line);
    }

    // Draw nodes
    for (const [pfx, node] of Object.entries(nodes)) {
        const isComp = pfx === companionPrefix;
        const penalty = node.health_penalty || 0;
        let color = '#888';
        if (node.status && Object.keys(node.status).length > 0) color = healthColor(penalty);
        if (isComp) color = '#0088ff';

        const marker = L.circleMarker([node._lat, node._lon], {
            radius: isComp ? 10 : 7,
            fillColor: color, fillOpacity: 0.9,
            color: isComp ? '#00d4ff' : (node._estimated ? '#999' : color),
            weight: isComp ? 3 : 2,
            dashArray: node._estimated ? '3,3' : null,
        });

        marker.bindTooltip(node.name, { permanent: false, direction: 'top', offset: [0,-8] });
        marker.bindPopup(buildNodePopup(pfx, node));
        marker.on('click', function(ev) { L.DomEvent.stopPropagation(ev); handleNodeClick(pfx); });
        marker.addTo(map);
        markers[pfx] = marker;
    }

    if (firstLoad && lats.length > 0) {
        map.fitBounds([[Math.min(...lats)-0.01, Math.min(...lons)-0.01],
                        [Math.max(...lats)+0.01, Math.max(...lons)+0.01]]);
        firstLoad = false;
    }

    populateNodeSelects();
}

function buildNodePopup(pfx, node) {
    const isComp = pfx === companionPrefix;
    const penalty = node.health_penalty || 0;
    let h = `<span class="node-name">${escHtml(node.name)}</span>`;
    if (isComp) h += ` 🏠`;
    if (node._estimated) h += ` <span class="no-gps-badge">📍 estimated</span>`;
    h += `<br><small>${pfx}</small>`;
    if (node.lat && node.lon) h += `<br><small>${node.lat.toFixed(4)}, ${node.lon.toFixed(4)}</small>`;

    const outE = topo.edges.filter(e => e.from === pfx);
    const inE = topo.edges.filter(e => e.to === pfx);
    h += `<br>Links: ${outE.length} out, ${inE.length} in`;

    if (node.status && Object.keys(node.status).length > 0) {
        const s = node.status;
        const bat = s.bat||0;
        const batPct = Math.max(0,Math.min(100,((bat-3000)/1200*100))).toFixed(0);
        const batC = bat<3300?'health-bad':(bat<3500?'health-warn':'health-ok');
        h += `<br><br><b>Status</b><table>`;
        h += `<tr><td>Battery</td><td class="${batC}">${bat}mV (~${batPct}%)</td></tr>`;
        h += `<tr><td>TX queue</td><td>${s.tx_queue_len||0}</td></tr>`;
        h += `<tr><td>Full events</td><td>${s.full_evts||0}</td></tr>`;
        h += `<tr><td>Uptime</td><td>${formatUptime(s.uptime)}</td></tr>`;
        if (s.recv_flood) {
            const dr = s.flood_dups ? (s.flood_dups/s.recv_flood*100).toFixed(0) : '0';
            h += `<tr><td>Flood dups</td><td>${dr}%</td></tr>`;
        }
        h += `<tr><td>Noise floor</td><td>${s.noise_floor||'-'} dBm</td></tr>`;
        h += `<tr><td>Health</td><td class="${healthClass(penalty)}">${penalty>0?'+':''}${penalty.toFixed(1)} dB</td></tr>`;
        h += `</table>`;
        if (node.status_timestamp) h += `<small>Updated: ${node.status_timestamp}</small>`;
    } else { h += `<br><small style="color:#666">No status data</small>`; }

    h += `<br><a class="btn" onclick="setPathFrom('${pfx}')">Route FROM</a>`;
    h += `<a class="btn" onclick="setPathTo('${pfx}')">Route TO</a>`;
    if (!isComp) h += `<a class="btn" onclick="setCompanion('${pfx}')">Set Home</a>`;
    return h;
}

// --- Path finding ---
function handleNodeClick(pfx) {
    if (!pathFrom) setPathFrom(pfx);
    else if (!pathTo && pfx !== pathFrom) setPathTo(pfx);
}

function setPathFrom(pfx) {
    clearPath();
    pathFrom = pfx;
    highlightNode(pfx, true);
    document.getElementById('selFrom').value = pfx;
    const name = topo.nodes[pfx] ? topo.nodes[pfx].name : pfx;
    document.getElementById('path-result').innerHTML =
        `<span style="color:#8899aa">From: <b>${escHtml(name)}</b> — click destination</span>`;
}

function setPathTo(pfx) {
    pathTo = pfx;
    document.getElementById('selTo').value = pfx;
    document.getElementById('path-result').innerHTML = `<span style="color:#8899aa">Computing...</span>`;
    computePath();
}

function setCompanion(pfx) {
    companionPrefix = pfx;
    refreshTopology();
}

function panelFindPath() {
    const from = document.getElementById('selFrom').value;
    const to = document.getElementById('selTo').value;
    if (!from || !to) { document.getElementById('path-result').innerHTML =
        `<span style="color:#f44336">Select both nodes</span>`; return; }
    clearPath();
    pathFrom = from;
    pathTo = to;
    highlightNode(from, true);
    highlightNode(to, true);
    computePath();
}

async function computePath() {
    if (!pathFrom || !pathTo) return;
    const useHealth = document.getElementById('chkHealth').checked ? 1 : 0;
    try {
        const resp = await fetch(`/api/path?from=${pathFrom}&to=${pathTo}&health=${useHealth}&k=3`);
        const data = await resp.json();
        if (data.error) {
            document.getElementById('path-result').innerHTML =
                `<span style="color:#f44336">${data.error}</span>`;
            return;
        }
        renderPaths(data);
    } catch (e) {
        document.getElementById('path-result').innerHTML =
            `<span style="color:#f44336">Request failed</span>`;
    }
}

function renderPaths(data) {
    pathLines.forEach(l => map.removeLayer(l));
    pathLines = [];

    const fwd = data.paths || [];
    const rev = data.reverse_paths || [];

    if (!fwd.length) {
        document.getElementById('path-result').innerHTML =
            `<span style="color:#f44336">No path found</span>`;
        return;
    }

    const p = fwd[0];

    // Draw alternatives behind
    for (let i = fwd.length-1; i >= 1; i--) drawPathLine(fwd[i], '#555', 3, '8,6', 0.5);
    // Primary
    drawPathLine(p, '#00d4ff', 5, null, 0.9);
    // Reverse if different
    if (rev.length > 0) {
        const rp = rev[0].path.join(',');
        const fp = p.path.join(',');
        if (rp !== fp.split(',').reverse().join(',')) drawPathLine(rev[0], '#ff66aa', 3, '6,4', 0.6);
    }
    for (const pfx of p.path) highlightNode(pfx, true);

    // Result panel
    let html = `<div class="path-primary">${p.path_names.map(escHtml).join(' → ')}</div>`;
    html += `<div><span class="bottleneck">Bottleneck: ${p.bottleneck_snr>=0?'+':''}${p.bottleneck_snr.toFixed(1)} dB</span> | Hops: ${p.hop_count}</div>`;

    // Per-hop detail
    html += `<table style="margin-top:4px">`;
    for (const e of p.edges) {
        const fn = topo.nodes[e.from], tn = topo.nodes[e.to];
        html += `<tr><td>${escHtml(fn?fn.name:e.from)}</td><td>→</td><td class="${snrClass(e.snr_db)}">${e.snr_db>=0?'+':''}${e.snr_db.toFixed(1)} dB</td></tr>`;
    }
    html += `</table>`;

    // Alternatives
    for (let i = 1; i < fwd.length; i++) {
        const a = fwd[i];
        html += `<div class="path-alt" style="margin-top:4px">Alt ${i+1}: ${a.path_names.map(escHtml).join(' → ')} (${a.bottleneck_snr>=0?'+':''}${a.bottleneck_snr.toFixed(1)} dB)</div>`;
    }

    // Reverse
    if (rev.length > 0) {
        const r = rev[0];
        html += `<div class="reverse" style="margin-top:4px">Reverse: ${r.path_names.map(escHtml).join(' → ')} (${r.bottleneck_snr>=0?'+':''}${r.bottleneck_snr.toFixed(1)} dB)</div>`;
    }

    if (data.health_aware) html += `<div style="margin-top:4px;color:#ff9800">🏥 Health penalties applied</div>`;

    document.getElementById('path-result').innerHTML = html;
}

function drawPathLine(pathResult, color, weight, dashArray, opacity) {
    const coords = [];
    for (const pfx of pathResult.path) {
        const node = topo.nodes[pfx];
        if (node) coords.push([node._lat, node._lon]);
    }
    if (coords.length < 2) return;

    const line = L.polyline(coords, {
        color, weight, opacity, dashArray, lineCap:'round', lineJoin:'round',
    });
    for (let i = 0; i < coords.length - 1; i++) {
        const edge = pathResult.edges[i];
        if (!edge) continue;
        const snr = edge.snr_db;
        const label = L.marker([(coords[i][0]+coords[i+1][0])/2, (coords[i][1]+coords[i+1][1])/2], {
            icon: L.divIcon({
                className: '',
                html: `<div style="background:${color};color:#fff;font-size:11px;padding:1px 4px;border-radius:3px;white-space:nowrap;transform:translate(-50%,-50%)">${snr>=0?'+':''}${snr.toFixed(1)}</div>`,
                iconSize: [0,0],
            }), interactive: false,
        });
        label.addTo(map); pathLines.push(label);
    }
    line.addTo(map); pathLines.push(line);
}

function highlightNode(pfx, on) {
    const m = markers[pfx];
    if (m && on) { m.setStyle({weight:4, color:'#00d4ff', fillOpacity:1.0}); m.setRadius(10); }
}

function clearPath() {
    pathFrom = null; pathTo = null;
    pathLines.forEach(l => map.removeLayer(l));
    pathLines = [];
    if (topo) {
        for (const [pfx, node] of Object.entries(topo.nodes)) {
            const m = markers[pfx]; if (!m) continue;
            const isC = pfx === companionPrefix;
            const pen = node.health_penalty || 0;
            let col = '#888';
            if (node.status && Object.keys(node.status).length) col = healthColor(pen);
            if (isC) col = '#0088ff';
            m.setStyle({ weight: isC?3:2, color: isC?'#00d4ff':(node._estimated?'#999':col),
                          fillOpacity: 0.9, dashArray: node._estimated?'3,3':null });
            m.setRadius(isC ? 10 : 7);
        }
    }
    document.getElementById('path-result').innerHTML = '';
    document.getElementById('selFrom').value = '';
    document.getElementById('selTo').value = '';
}

// --- Discovery control ---
async function discoveryStart() {
    try {
        const resp = await fetch('/api/discovery/start', { method: 'POST' });
        const data = await resp.json();
        if (data.ok) {
            discLogSince = 0;
            document.getElementById('discovery-log').innerHTML = '';
            startDiscPoll();
        } else {
            alert(data.message);
        }
    } catch (e) { alert('Failed: ' + e); }
}

async function discoveryStop() {
    try {
        await fetch('/api/discovery/stop', { method: 'POST' });
    } catch (e) { alert('Failed: ' + e); }
}

function startDiscPoll() {
    if (discPollTimer) return;
    discPollTimer = setInterval(pollDiscovery, 2000);
    pollDiscovery();
}

async function pollDiscovery() {
    try {
        const resp = await fetch(`/api/discovery/status?log_since=${discLogSince}`);
        const data = await resp.json();
        updateDiscUI(data);
    } catch (e) {}
}

function updateDiscUI(data) {
    const dot = document.getElementById('discDot');
    const text = document.getElementById('discStatusText');
    const startBtn = document.getElementById('btnDiscStart');
    const stopBtn = document.getElementById('btnDiscStop');

    dot.className = 'status-dot ' + data.status;
    const labels = { idle:'Idle', running:'Running...', stopping:'Stopping...', completed:'Completed', error:'Error' };
    text.textContent = labels[data.status] || data.status;
    if (data.error) text.textContent += ': ' + data.error;

    startBtn.disabled = (data.status === 'running' || data.status === 'stopping');
    stopBtn.disabled = (data.status !== 'running');

    // Append new logs
    if (data.logs && data.logs.length > 0) {
        const logEl = document.getElementById('discovery-log');
        for (const line of data.logs) {
            const div = document.createElement('div');
            div.className = 'log-line';
            div.textContent = line;
            logEl.appendChild(div);
        }
        discLogSince += data.logs.length;
        logEl.scrollTop = logEl.scrollHeight;
    }

    // Stop polling when not running
    if (data.status !== 'running' && data.status !== 'stopping') {
        if (discPollTimer) { clearInterval(discPollTimer); discPollTimer = null; }
    }
}

// --- Refresh ---
async function refreshTopology() {
    try {
        const resp = await fetch('/api/topology');
        const data = await resp.json();
        renderTopology(data);
    } catch (e) { console.error('Refresh failed:', e); }
}

function toggleAutoRefresh() {
    if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
    if (document.getElementById('chkAutoRefresh').checked)
        refreshTimer = setInterval(refreshTopology, REFRESH_MS);
}
document.getElementById('chkAutoRefresh').addEventListener('change', toggleAutoRefresh);

// --- Init ---
refreshTopology();
toggleAutoRefresh();
// Check if discovery is already running
fetch('/api/discovery/status').then(r => r.json()).then(d => {
    updateDiscUI(d);
    if (d.status === 'running' || d.status === 'stopping') startDiscPoll();
}).catch(() => {});
</script>
</body>
</html>
"""


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
