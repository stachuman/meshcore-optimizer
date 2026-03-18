"""
Embedded HTML/JS/CSS for the MeshCore network map UI.
"""

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
    position: absolute; top: 48px; bottom: 0; right: 0; width: 420px;
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
    padding: 8px 14px; font-size: 12px; line-height: 1.5;
    max-height: 300px; overflow-y: auto;
}
#path-result .path-primary { color: #00d4ff; font-weight: bold; }
#path-result .path-alt { color: #888; }
#path-result .bottleneck { color: #ffaa00; }
#path-result .reverse { color: #ff66aa; }
.path-columns { display: flex; gap: 8px; }
.path-col { flex: 1; min-width: 0; }
.path-col h4 { font-size: 11px; color: #8899aa; margin: 0 0 4px 0; text-transform: uppercase; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.path-col label { display: block; cursor: pointer; padding: 1px 0; font-size: 11px; }
.path-details { display: flex; gap: 8px; margin-top: 6px; border-top: 1px solid #0f3460; padding-top: 6px; }
.path-detail-col { flex: 1; min-width: 0; }
.path-actions { margin-top: 6px; border-top: 1px solid #0f3460; padding-top: 6px; display: flex; gap: 4px; flex-wrap: wrap; align-items: center; }

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
    background: rgba(255,255,255,0.9); border: 1px solid #ccc; border-radius: 6px;
    padding: 8px 12px; font-size: 12px; line-height: 1.6; color: #555;
}
.legend b { color: #333; }
.legend-row { display: flex; align-items: center; gap: 6px; }
.legend-dot { width: 12px; height: 12px; border-radius: 50%; display: inline-block; }
.legend-line { width: 24px; height: 3px; display: inline-block; border-radius: 1px; }
.no-gps-badge { font-size: 10px; color: #ff9800; font-style: italic; }

/* --- Search --- */
.search-wrap { position: relative; }
.search-wrap input {
    width: 160px; padding: 4px 8px; border-radius: 4px; font-size: 13px;
    background: #0d1b2a; border: 1px solid #1a5276; color: #e0e0e0;
}
.search-wrap input:focus { border-color: #00d4ff; outline: none; }
.search-results {
    display: none; position: absolute; top: 100%; left: 0; width: 240px;
    max-height: 200px; overflow-y: auto; background: #16213e;
    border: 1px solid #0f3460; border-radius: 0 0 6px 6px;
    z-index: 1100; margin-top: 2px;
}
.search-results.open { display: block; }
.search-item {
    padding: 6px 10px; cursor: pointer; font-size: 13px;
    border-bottom: 1px solid #0f3460;
}
.search-item:hover { background: #1a5276; }
.search-item small { color: #666; }

/* --- Settings Modal --- */
.modal-overlay {
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.6);
    z-index: 2000; align-items: center; justify-content: center;
}
.modal-overlay.open { display: flex; }
.modal {
    background: #16213e; border: 1px solid #0f3460; border-radius: 10px;
    width: 440px; max-width: 95vw; max-height: 90vh; overflow-y: auto;
    padding: 20px 24px; box-shadow: 0 8px 32px rgba(0,0,0,0.5);
}
.modal h2 { font-size: 18px; color: #00d4ff; margin-bottom: 12px; }
.modal h4 { font-size: 13px; color: #00d4ff; margin: 14px 0 6px;
            text-transform: uppercase; letter-spacing: 0.5px; }
.modal label.field { display: block; font-size: 12px; color: #8899aa;
                     margin-bottom: 2px; margin-top: 8px; }
.tab-bar {
    display: flex; border-bottom: 1px solid #0f3460; margin-bottom: 12px;
}
.tab-btn {
    padding: 6px 14px; cursor: pointer; font-size: 13px; color: #8899aa;
    border: none; background: none; border-bottom: 2px solid transparent;
}
.tab-btn:hover { color: #e0e0e0; }
.tab-btn.active { color: #00d4ff; border-bottom-color: #00d4ff; }
.tab-page { display: none; }
.tab-page.active { display: block; }
.modal input, .modal select {
    width: 100%; padding: 7px 10px; border-radius: 4px;
    background: #0d1b2a; border: 1px solid #1a5276; color: #e0e0e0;
    font-size: 13px;
}
.modal input:focus, .modal select:focus { border-color: #00d4ff; outline: none; }
.modal .row { display: flex; gap: 10px; }
.modal .row > * { flex: 1; }
.modal .actions { display: flex; gap: 8px; margin-top: 18px; justify-content: flex-end; }
.modal .repeater-list {
    max-height: 160px; overflow-y: auto; margin-top: 6px;
    border: 1px solid #1a5276; border-radius: 4px; background: #0d1b2a;
}
.modal .repeater-item {
    padding: 6px 10px; cursor: pointer; font-size: 13px;
    border-bottom: 1px solid #0f3460;
}
.modal .repeater-item:hover { background: #1a5276; }
.modal .repeater-item.selected { background: #0f3460; color: #00d4ff; }
.modal .hint { font-size: 11px; color: #666; margin-top: 4px; }
.modal .test-status { font-size: 12px; margin-top: 6px; }
</style>
</head>
<body>

<div id="topbar">
    <h1>MeshCore Network Map</h1>
    <span class="stats" id="stats"></span>
    <div class="controls">
        <div class="search-wrap">
            <input id="searchInput" type="text" placeholder="Find repeater..."
                   autocomplete="off" oninput="onSearch(this.value)"
                   onfocus="onSearch(this.value)">
            <div id="searchResults" class="search-results"></div>
        </div>
        <label><input type="checkbox" id="chkAutoRefresh" checked> Auto-refresh</label>
        <button onclick="refreshTopology()">Refresh</button>
        <button onclick="openSettings()">Settings</button>
        <button id="btnPanel" onclick="togglePanel()" class="active">Panel</button>
    </div>
</div>

<div id="map"></div>

<!-- Settings Modal -->
<div class="modal-overlay" id="settingsModal">
<div class="modal">
    <h2 id="settingsTitle">Settings</h2>
    <div class="tab-bar">
        <span class="tab-btn active" onclick="switchTab('tabRadio',this)">Radio</span>
        <span class="tab-btn" onclick="switchTab('tabDiscovery',this)">Discovery</span>
        <span class="tab-btn" onclick="switchTab('tabHealth',this)">Health</span>
        <span class="tab-btn" onclick="switchTab('tabPasswords',this)">Passwords</span>
    </div>

    <!-- Tab: Radio -->
    <div id="tabRadio" class="tab-page active">
        <label class="field">Protocol</label>
        <select id="cfgProtocol" onchange="updateProtocolFields()">
            <option value="tcp">TCP</option>
            <option value="serial">Serial / USB</option>
            <option value="ble">BLE</option>
        </select>
        <div class="row" id="rowTcp">
            <div>
                <label class="field">Host</label>
                <input id="cfgHost" placeholder="192.168.1.100">
            </div>
            <div style="max-width:100px">
                <label class="field">Port</label>
                <input id="cfgPort" type="number" value="5000">
            </div>
        </div>
        <div id="rowSerial" style="display:none">
            <div class="row">
                <div>
                    <label class="field">Serial Port</label>
                    <input id="cfgSerialPort" placeholder="/dev/ttyACM0">
                </div>
                <div style="max-width:120px">
                    <label class="field">Baud Rate</label>
                    <input id="cfgBaudrate" type="number" value="115200">
                </div>
            </div>
        </div>
        <div id="rowBle" style="display:none">
            <label class="field">BLE Address (optional)</label>
            <input id="cfgBleAddress" placeholder="AA:BB:CC:DD:EE:FF">
        </div>
        <div style="margin-top:8px">
            <button onclick="testRadio()" id="btnTestRadio">Test Connection</button>
            <span class="test-status" id="testStatus"></span>
        </div>

        <h4>Home Repeater</h4>
        <div class="hint">Click "Test Connection" to detect repeaters, or enter prefix manually.</div>
        <div class="repeater-list" id="repeaterList" style="display:none"></div>
        <label class="field">Companion Prefix</label>
        <input id="cfgCompanion" placeholder="e.g. 53649FDE" style="text-transform:uppercase">
    </div>

    <!-- Tab: Discovery -->
    <div id="tabDiscovery" class="tab-page">
        <div class="row">
            <div>
                <label class="field">Max Rounds</label>
                <input id="cfgMaxRounds" type="number" value="5">
            </div>
            <div>
                <label class="field">Timeout (s)</label>
                <input id="cfgTimeout" type="number" value="30" step="1">
            </div>
        </div>
        <div class="row">
            <div>
                <label class="field">Delay (s)</label>
                <input id="cfgDelay" type="number" value="5" step="0.5">
            </div>
            <div>
                <label class="field">Infer Penalty (dB)</label>
                <input id="cfgInferPenalty" type="number" value="5" step="0.5">
            </div>
        </div>
        <div class="row">
            <div>
                <label class="field">Hop Penalty (dB/hop)</label>
                <input id="cfgHopPenalty" type="number" value="1.0" step="0.5">
                <div class="hint">Extra cost per hop. Higher = prefer shorter paths.</div>
            </div>
            <div>
                <label class="field">Probe Distance (km)</label>
                <input id="cfgProbeDist" type="number" value="2.0" step="0.5">
                <div class="hint">Max distance to probe missing links.</div>
            </div>
        </div>
        <div class="row">
            <div>
                <label class="field">Probe Min SNR (dB)</label>
                <input id="cfgProbeMinSnr" type="number" value="-5.0" step="1">
                <div class="hint">Only probe gaps where path is below this. Higher = probe more.</div>
            </div>
            <div>
                <label class="field">Save File</label>
                <input id="cfgSaveFile" value="topology.json">
            </div>
            <div></div>
        </div>
    </div>

    <!-- Tab: Health Penalties -->
    <div id="tabHealth" class="tab-page">
        <div class="hint" style="margin-bottom:8px">
            Penalty in dB subtracted from effective SNR when routing through unhealthy nodes.
            Set to 0 to disable a factor.
        </div>
        <h4>Battery</h4>
        <div class="row">
            <div>
                <label class="field">Critical (&lt;3300mV)</label>
                <input id="cfgHpBatCrit" type="number" value="3.0" step="0.5">
            </div>
            <div>
                <label class="field">Warning (&lt;3500mV)</label>
                <input id="cfgHpBatWarn" type="number" value="1.0" step="0.5">
            </div>
        </div>
        <h4>TX Queue</h4>
        <div class="row">
            <div>
                <label class="field">High (&gt;5)</label>
                <input id="cfgHpTxqHigh" type="number" value="4.0" step="0.5">
            </div>
            <div>
                <label class="field">Low (&gt;0)</label>
                <input id="cfgHpTxqLow" type="number" value="1.0" step="0.5">
            </div>
        </div>
        <h4>Event Overflows</h4>
        <div class="row">
            <div>
                <label class="field">High (&gt;10)</label>
                <input id="cfgHpEvtHigh" type="number" value="4.0" step="0.5">
            </div>
            <div>
                <label class="field">Per event (1-10)</label>
                <input id="cfgHpEvtPer" type="number" value="0.5" step="0.1">
            </div>
        </div>
        <h4>Flood Duplicates</h4>
        <div class="row">
            <div>
                <label class="field">High (&gt;70%)</label>
                <input id="cfgHpFloodHigh" type="number" value="3.0" step="0.5">
            </div>
            <div>
                <label class="field">Medium (&gt;50%)</label>
                <input id="cfgHpFloodMed" type="number" value="1.0" step="0.5">
            </div>
        </div>
    </div>

    <!-- Tab: Passwords -->
    <div id="tabPasswords" class="tab-page">
        <label class="field">Default Guest Passwords</label>
        <input id="cfgGuestPws" placeholder="blank, hello" value="">
        <div class="hint">Comma-separated. Use "blank" for empty password. Tried on every repeater.</div>

        <h4>Per-Repeater Passwords</h4>
        <div class="hint">One per line: prefix level password (e.g. "53649FDE admin secret")</div>
        <textarea id="cfgPasswords" rows="4" style="width:100%;padding:7px 10px;border-radius:4px;
            background:#0d1b2a;border:1px solid #1a5276;color:#e0e0e0;font-size:13px;
            font-family:monospace;resize:vertical"></textarea>
    </div>

    <div class="actions">
        <button onclick="closeSettings()">Cancel</button>
        <button class="success" onclick="saveSettings()">Save</button>
    </div>
</div>
</div>

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
            <button onclick="askFirmwarePath()">FW Route</button>
            <button onclick="clearPath()">Clear</button>
        </div>
    </div>
    <div id="path-result"></div>

    <!-- Manual Trace -->
    <div class="panel-section">
        <h3>Trace Path</h3>
        <input id="traceInput" type="text" placeholder="e.g. 5364,ee9f,bb57,41a1,bb57,ee9f,5364"
               style="font-family:monospace;font-size:12px">
        <div class="panel-row" style="margin-top:4px">
            <span id="tracePreview" style="font-size:11px;color:#8899aa;flex:1"></span>
            <button onclick="sendTrace()">Send</button>
        </div>
        <div id="traceResult" style="font-size:12px;margin-top:4px"></div>
    </div>

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
let map, topo = null, markers = {}, edgeLines = [], pathLines = [], traceLines = [];
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

L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
    maxZoom: 19,
}).addTo(map);

// Legend
const legend = L.control({position: 'bottomleft'});
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

// --- Search ---
function onSearch(query) {
    const results = document.getElementById('searchResults');
    if (!topo || !query || query.length < 1) {
        results.classList.remove('open');
        return;
    }
    const q = query.toLowerCase();
    const matches = Object.entries(topo.nodes)
        .filter(([pfx, n]) => n.name.toLowerCase().includes(q) || pfx.toLowerCase().includes(q))
        .sort((a, b) => a[1].name.localeCompare(b[1].name))
        .slice(0, 10);

    if (!matches.length) { results.classList.remove('open'); return; }

    results.innerHTML = '';
    for (const [pfx, n] of matches) {
        const item = document.createElement('div');
        item.className = 'search-item';
        item.innerHTML = `${escHtml(n.name)} <small>[${pfx.substring(0,4)}]</small>`;
        item.onclick = () => goToNode(pfx);
        results.appendChild(item);
    }
    results.classList.add('open');
}

function goToNode(pfx) {
    document.getElementById('searchResults').classList.remove('open');
    document.getElementById('searchInput').value = '';
    const node = topo.nodes[pfx];
    if (!node) return;
    map.flyTo([node._lat, node._lon], 14, { duration: 0.8 });
    const m = markers[pfx];
    if (m) setTimeout(() => m.openPopup(), 500);
}

// Close search on click outside
document.addEventListener('click', function(e) {
    if (!e.target.closest('.search-wrap'))
        document.getElementById('searchResults').classList.remove('open');
});

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
    h += `<br><a class="btn" onclick="nodeCmd('status','${pfx}')">Req Status</a>`;
    h += `<a class="btn" onclick="nodeCmd('neighbors','${pfx}')">Req Neighbors</a>`;
    h += `<span id="cmdStatus_${pfx}" style="font-size:11px;margin-left:4px"></span>`;
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

async function nodeCmd(action, pfx) {
    const statusEl = document.getElementById('cmdStatus_' + pfx);
    if (statusEl) { statusEl.textContent = 'starting...'; statusEl.style.color = '#ff9800'; }

    try {
        const resp = await fetch('/api/node/command', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ action, prefix: pfx }),
        });
        const data = await resp.json();
        if (!data.ok) {
            if (statusEl) { statusEl.textContent = data.message || data.error || 'failed'; statusEl.style.color = '#f44336'; }
            return;
        }
    } catch (e) {
        if (statusEl) { statusEl.textContent = 'error'; statusEl.style.color = '#f44336'; }
        return;
    }

    // Start polling logs + result
    startDiscPoll();
    if (statusEl) { statusEl.textContent = 'running...'; }
    _pollNodeResult(action, pfx, statusEl);
}

function _pollNodeResult(action, pfx, statusEl) {
    const poll = setInterval(async () => {
        try {
            const resp = await fetch('/api/node/result');
            const data = await resp.json();
            if (data.busy) return; // still running
            clearInterval(poll);
            if (data.ok) {
                let msg = action === 'status'
                    ? `OK (${data.status?.bat || '?'}mV, penalty ${(data.health_penalty||0).toFixed(1)}dB)`
                    : `OK (+${data.edges_added || 0} edges)`;
                if (statusEl) { statusEl.textContent = msg; statusEl.style.color = '#4caf50'; }
                refreshTopology();
            } else {
                if (statusEl) { statusEl.textContent = data.error || 'failed'; statusEl.style.color = '#f44336'; }
            }
        } catch (e) {
            clearInterval(poll);
            if (statusEl) { statusEl.textContent = 'error'; statusEl.style.color = '#f44336'; }
        }
    }, 2000);
}

// --- Manual Trace ---
function clearTraceLines() {
    traceLines.forEach(l => map.removeLayer(l));
    traceLines = [];
}

function resolveHop(h) {
    if (!topo) return null;
    h = h.toLowerCase();
    for (const [pfx, n] of Object.entries(topo.nodes)) {
        if (pfx.toLowerCase().startsWith(h)) return { pfx, node: n };
    }
    return null;
}

document.getElementById('traceInput').addEventListener('input', function() {
    const val = this.value.trim();
    clearTraceLines();
    if (!val || !topo) { document.getElementById('tracePreview').textContent = ''; return; }

    const hops = val.split(',').map(h => h.trim());
    const resolved = hops.map(resolveHop);
    const names = resolved.map((r, i) => r ? r.node.name : '?');
    document.getElementById('tracePreview').textContent = names.join(' → ');

    // Draw on map — only forward half (before return trip)
    const mid = Math.ceil(hops.length / 2);
    const fwdResolved = resolved.slice(0, mid);
    const coords = [];
    for (const r of fwdResolved) {
        if (r && r.node._lat) coords.push([r.node._lat, r.node._lon]);
    }
    if (coords.length >= 2) {
        const line = L.polyline(coords, {
            color: '#e040fb', weight: 4, opacity: 0.7,
            dashArray: '8,6', lineCap: 'round',
        });
        line.addTo(map);
        traceLines.push(line);

        // Mark each hop with its short hash
        for (let i = 0; i < fwdResolved.length; i++) {
            const r = fwdResolved[i];
            if (!r || !r.node._lat) continue;
            const label = L.marker([r.node._lat, r.node._lon], {
                icon: L.divIcon({
                    className: '',
                    html: `<div style="background:#e040fb;color:#fff;font-size:10px;padding:1px 4px;border-radius:3px;white-space:nowrap;transform:translate(-50%,-150%)">${hops[i]}</div>`,
                    iconSize: [0,0],
                }), interactive: false,
            });
            label.addTo(map);
            traceLines.push(label);
        }
    }
});

async function sendTrace() {
    const path = document.getElementById('traceInput').value.trim();
    if (!path) return;
    const resultEl = document.getElementById('traceResult');
    resultEl.innerHTML = '<span style="color:#ff9800">Sending...</span>';

    startDiscPoll();
    try {
        const resp = await fetch('/api/trace', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ path }),
        });
        const data = await resp.json();
        if (!data.ok) {
            resultEl.innerHTML = `<span style="color:#f44336">${data.message || data.error}</span>`;
            return;
        }
        // Poll for result
        const poll = setInterval(async () => {
            try {
                const r = await fetch('/api/node/result');
                const d = await r.json();
                if (d.busy) return;
                clearInterval(poll);
                if (d.ok) {
                    const msg = d.edges_added > 0
                        ? `<span style="color:#4caf50">+${d.edges_added} edges</span>`
                        : `<span style="color:#8899aa">No new edges</span>`;
                    resultEl.innerHTML = msg + (d.error ? ` <span style="color:#ff9800">(${d.error})</span>` : '');
                    if (d.edges_added > 0) refreshTopology();
                } else {
                    resultEl.innerHTML = `<span style="color:#f44336">${d.error || 'failed'}</span>`;
                }
            } catch (e) { clearInterval(poll); }
        }, 2000);
    } catch (e) {
        resultEl.innerHTML = `<span style="color:#f44336">Error: ${e}</span>`;
    }
}

async function askFirmwarePath() {
    const to = document.getElementById('selTo').value;
    if (!to) {
        document.getElementById('path-result').innerHTML =
            `<span style="color:#f44336">Select destination node</span>`;
        return;
    }
    const resultEl = document.getElementById('path-result');
    const existing = resultEl.innerHTML;
    resultEl.innerHTML = existing +
        `<div style="margin-top:8px;color:#ff9800">Asking firmware for route...</div>`;

    startDiscPoll();
    try {
        const resp = await fetch('/api/path/firmware', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ prefix: to }),
        });
        const data = await resp.json();
        if (!data.ok) {
            appendFwResult(existing, `<span style="color:#f44336">FW: ${data.message || data.error}</span>`);
            return;
        }
        _pollFwResult(existing);
    } catch (e) {
        appendFwResult(existing, `<span style="color:#f44336">FW error: ${e}</span>`);
    }
}

function _pollFwResult(existingHtml) {
    const poll = setInterval(async () => {
        try {
            const r = await fetch('/api/node/result');
            const d = await r.json();
            if (d.busy) return;
            clearInterval(poll);
            if (d.ok) {
                let html = '<div style="margin-top:8px;border-top:1px solid #0f3460;padding-top:6px">';
                html += '<b style="color:#ff9800">Firmware Route</b><br>';

                // Out path
                if (d.out_path) {
                    const op = d.out_path;
                    const snr = op.bottleneck_snr !== null ? ` (${fmtSnr(op.bottleneck_snr)} dB)` : ' (SNR unknown)';
                    html += `<div>Out: ${op.path_names.map(escHtml).join(' → ')}${snr}</div>`;
                    if (op.missing_edges && op.missing_edges.length) {
                        for (const me of op.missing_edges) {
                            html += `<div style="color:#f44336;font-size:11px">  Missing: ${escHtml(me.from_name)} ↔ ${escHtml(me.to_name)}</div>`;
                        }
                    }
                }
                // In path
                if (d.in_path) {
                    const ip = d.in_path;
                    const snr = ip.bottleneck_snr !== null ? ` (${fmtSnr(ip.bottleneck_snr)} dB)` : ' (SNR unknown)';
                    html += `<div>In: ${ip.path_names.map(escHtml).join(' → ')}${snr}</div>`;
                    if (ip.missing_edges && ip.missing_edges.length) {
                        for (const me of ip.missing_edges) {
                            html += `<div style="color:#f44336;font-size:11px">  Missing: ${escHtml(me.from_name)} ↔ ${escHtml(me.to_name)}</div>`;
                        }
                    }
                }

                // Draw firmware paths on map
                function drawFwPath(fwp, color) {
                    if (!fwp || !fwp.path || !topo) return;
                    const coords = [];
                    for (const pfx of fwp.path) {
                        // Try exact match, then prefix match
                        let n = topo.nodes[pfx];
                        if (!n) {
                            for (const [k, v] of Object.entries(topo.nodes)) {
                                if (k.startsWith(pfx) || pfx.startsWith(k)) { n = v; break; }
                            }
                        }
                        if (n && n._lat) coords.push([n._lat, n._lon]);
                    }
                    if (coords.length >= 2) {
                        const line = L.polyline(coords, {
                            color, weight: 3, opacity: 0.7,
                            dashArray: '4,6', lineCap: 'round',
                        });
                        line.addTo(map);
                        pathLines.push(line);
                    }
                }
                drawFwPath(d.out_path, '#ff9800');
                drawFwPath(d.in_path, '#ff6600');

                if (d.edges_probed > 0) {
                    html += `<div style="color:#4caf50">+${d.edges_probed} edges discovered and added!</div>`;
                    refreshTopology();
                }

                html += '</div>';
                appendFwResult(existingHtml, html);
            } else {
                appendFwResult(existingHtml, `<div style="margin-top:6px;color:#f44336">FW: ${d.error}</div>`);
            }
        } catch (e) {
            clearInterval(poll);
        }
    }, 2000);
}

function appendFwResult(existingHtml, fwHtml) {
    const el = document.getElementById('path-result');
    // Remove any "Asking firmware..." message
    const cleaned = el.innerHTML.replace(/<div[^>]*>Asking firmware[^<]*<\/div>/, '');
    el.innerHTML = cleaned + fwHtml;
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

let _lastPathData = null; // stored for re-draw on radio selection change

function fmtSnr(v) { return (v>=0?'+':'')+v.toFixed(1); }
function pathHex(pr) { return pr.path.map(p => p.substring(0,4).toLowerCase()).join(','); }
function pathLine(pr) {
    return pr.path.map(pfx => {
        const n = topo.nodes[pfx];
        return `${escHtml(n?n.name:pfx)} [${pfx.substring(0,4)}]`;
    }).join(' → ');
}

function getSelectedIdx(name) {
    const el = document.querySelector('input[name="'+name+'"]:checked');
    return el ? parseInt(el.value) : 0;
}

function buildSmartTrace() {
    if (!_lastPathData) return null;
    const fwd = _lastPathData.paths || [];
    const rev = _lastPathData.reverse_paths || [];
    const fi = getSelectedIdx('fwdChoice');
    const ri = getSelectedIdx('revChoice');
    if (!fwd[fi]) return null;
    const fwdHops = fwd[fi].path.map(p => p.substring(0,4).toLowerCase());
    const revHops = rev[ri]
        ? rev[ri].path.map(p => p.substring(0,4).toLowerCase())
        : fwdHops.slice().reverse();
    const combined = fwdHops.concat(revHops.slice(1));
    const compShort = (companionPrefix || '').substring(0,4).toLowerCase();
    if (!compShort) return null;
    if (combined[0] !== compShort && combined[combined.length-1] !== compShort) return null;
    if (combined[0] !== compShort) combined.reverse();
    return combined.join(',');
}

function getSelectedFwdHex() {
    if (!_lastPathData) return '';
    const fwd = _lastPathData.paths || [];
    const fi = getSelectedIdx('fwdChoice');
    return fwd[fi] ? pathHex(fwd[fi]) : '';
}
function getSelectedRevHex() {
    if (!_lastPathData) return '';
    const rev = _lastPathData.reverse_paths || [];
    const ri = getSelectedIdx('revChoice');
    return rev[ri] ? pathHex(rev[ri]) : '';
}

function renderPaths(data) {
    _lastPathData = data;
    const fwd = data.paths || [];
    const rev = data.reverse_paths || [];

    if (!fwd.length && !rev.length) {
        let msg = '<span style="color:#f44336">No path found</span>';
        if (data.diag) {
            const d = data.diag;
            msg += '<div style="font-size:11px;color:#8899aa;margin-top:4px">';
            msg += `Source has ${d.src_edges} outgoing link(s), `;
            msg += `destination has ${d.dst_edges} incoming link(s)`;
            msg += '</div>';
        }
        document.getElementById('path-result').innerHTML = msg;
        return;
    }

    // Draw initial state on map
    showSelectedPaths(fwd, 0, rev, 0);

    // Direction labels from first path's node names
    const fwdLabel = fwd.length ? `${fwd[0].path_names[0]} \u2192 ${fwd[0].path_names[fwd[0].path_names.length-1]}` : '';
    const revLabel = rev.length ? `${rev[0].path_names[0]} \u2192 ${rev[0].path_names[rev[0].path_names.length-1]}` : '';

    // Build two-column radio layout
    let html = '<div class="path-columns">';

    // Forward column
    html += '<div class="path-col">';
    html += `<h4 style="color:#00d4ff">\u2192 ${escHtml(fwdLabel)}</h4>`;
    if (fwd.length) {
        for (let i = 0; i < fwd.length; i++) {
            const p = fwd[i];
            const lbl = i === 0 ? 'Best' : `Alt ${i+1}`;
            const chk = i === 0 ? 'checked' : '';
            html += `<label><input type="radio" name="fwdChoice" value="${i}" ${chk} onchange="onFwdChoice(${i})" style="margin-right:3px">`;
            html += `<b>${lbl}</b> ${fmtSnr(p.bottleneck_snr)} dB, ${p.hop_count}h</label>`;
        }
    } else {
        html += '<span style="color:#f44336">No path</span>';
    }
    html += '</div>';

    // Return column
    html += '<div class="path-col">';
    html += `<h4 style="color:#ff66aa">\u2190 ${escHtml(revLabel)}</h4>`;
    if (rev.length) {
        for (let i = 0; i < rev.length; i++) {
            const p = rev[i];
            const lbl = i === 0 ? 'Best' : `Alt ${i+1}`;
            const chk = i === 0 ? 'checked' : '';
            html += `<label><input type="radio" name="revChoice" value="${i}" ${chk} onchange="onRevChoice(${i})" style="margin-right:3px">`;
            html += `<b>${lbl}</b> ${fmtSnr(p.bottleneck_snr)} dB, ${p.hop_count}h</label>`;
        }
    } else {
        html += '<span style="color:#f44336">No path</span>';
    }
    html += '</div></div>';

    // Detail panels side by side
    html += '<div class="path-details">';
    html += '<div class="path-detail-col" id="fwdDetail">';
    if (fwd.length) html += pathDetailHtml(fwd[0]);
    html += '</div>';
    html += '<div class="path-detail-col" id="revDetail">';
    if (rev.length) html += pathDetailHtml(rev[0]);
    html += '</div></div>';

    // Health notice
    if (data.health_aware) html += '<div style="margin-top:4px;color:#ff9800;font-size:11px">\ud83c\udfe5 Health penalties applied</div>';

    // Action buttons
    html += '<div class="path-actions">';
    html += '<a class="btn" style="font-size:10px;padding:2px 6px" onclick="copyToClipboard(getSelectedFwdHex(),this)">Copy Fwd</a>';
    html += '<a class="btn" style="font-size:10px;padding:2px 6px" onclick="copyToClipboard(getSelectedRevHex(),this)">Copy Ret</a>';
    html += '<a class="btn" style="font-size:10px;padding:2px 6px" onclick="copyToClipboard(getSelectedFwdHex()+\' | \'+getSelectedRevHex(),this)">Copy Both</a>';

    const compShort = (companionPrefix || '').substring(0,4).toLowerCase();
    const fwdStart = fwd.length ? fwd[0].path[0].substring(0,4).toLowerCase() : '';
    const fwdEnd = fwd.length ? fwd[0].path[fwd[0].path.length-1].substring(0,4).toLowerCase() : '';
    const canTrace = compShort && (fwdStart === compShort || fwdEnd === compShort);
    if (canTrace) {
        html += '<a class="btn" style="font-size:10px;padding:2px 6px" onclick="doSmartTrace()">Trace</a>';
    } else if (companionPrefix) {
        html += '<span style="font-size:10px;color:#667;margin-left:4px">Trace: companion not endpoint</span>';
    }
    html += '</div>';

    document.getElementById('path-result').innerHTML = html;
}

function pathDetailHtml(pr) {
    const hex = pathHex(pr);
    let html = `<div class="path-primary" style="font-size:11px">${pathLine(pr)}</div>`;
    html += `<div style="font-size:10px;color:#8899aa;margin-top:1px">`;
    html += `${hex}</div>`;
    html += '<table style="margin-top:3px;font-size:11px">';
    const srcIcons = {neighbors:'N', trace:'T', advert:'A', inferred:'~', manual:'M'};
    for (const e of pr.edges) {
        const fn = topo.nodes[e.from];
        const si = srcIcons[e.source] || '?';
        html += `<tr><td>${escHtml(fn?fn.name:e.from)} [${e.from.substring(0,4)}]</td><td>\u2192</td>`;
        html += `<td class="${snrClass(e.snr_db)}">${fmtSnr(e.snr_db)}</td>`;
        html += `<td style="color:#667;font-size:10px;padding-left:4px" title="${e.source||''}, conf ${e.confidence||''}">${si}</td></tr>`;
    }
    html += '</table>';
    if (pr.node_health) {
        const entries = Object.entries(pr.node_health).filter(([,v]) => v > 0);
        if (entries.length) {
            html += '<div style="margin-top:3px;font-size:10px;color:#ff9800">';
            for (const [pfx, pen] of entries) {
                const n = topo.nodes[pfx];
                html += `${escHtml(n?n.name:pfx)}: -${pen.toFixed(1)} dB `;
            }
            html += '</div>';
        }
    }
    return html;
}

function copyToTrace(rt) {
    document.getElementById('traceInput').value = rt;
    document.getElementById('traceInput').dispatchEvent(new Event('input'));
}

function doSmartTrace() {
    const trace = buildSmartTrace();
    if (trace) copyToTrace(trace);
}

function copyToClipboard(text, btn) {
    const done = () => { if(btn){const orig=btn.textContent;btn.textContent='Copied!';setTimeout(()=>btn.textContent=orig,1500);} };
    const fallback = () => {
        const ta = document.createElement('textarea');
        ta.value = text; ta.style.position = 'fixed'; ta.style.left = '-9999px';
        document.body.appendChild(ta); ta.select();
        document.execCommand('copy'); document.body.removeChild(ta);
        done();
    };
    try { navigator.clipboard.writeText(text).then(done, fallback); }
    catch(e) { fallback(); }
}

function onFwdChoice(idx) {
    if (!_lastPathData) return;
    const fwd = _lastPathData.paths || [];
    const rev = _lastPathData.reverse_paths || [];
    const ri = getSelectedIdx('revChoice');
    showSelectedPaths(fwd, idx, rev, ri);
    const el = document.getElementById('fwdDetail');
    if (el && fwd[idx]) el.innerHTML = pathDetailHtml(fwd[idx]);
}

function onRevChoice(idx) {
    if (!_lastPathData) return;
    const fwd = _lastPathData.paths || [];
    const rev = _lastPathData.reverse_paths || [];
    const fi = getSelectedIdx('fwdChoice');
    showSelectedPaths(fwd, fi, rev, idx);
    const el = document.getElementById('revDetail');
    if (el && rev[idx]) el.innerHTML = pathDetailHtml(rev[idx]);
}

function showSelectedPaths(fwd, fi, rev, ri) {
    pathLines.forEach(l => map.removeLayer(l));
    pathLines = [];
    resetNodeStyles();

    // Draw non-selected forward alts (thin, dashed)
    for (let i = fwd.length - 1; i >= 0; i--) {
        if (i === fi) continue;
        drawPathLine(fwd[i], '#aaaaaa', 2, '6,4', 0.3);
    }
    // Draw non-selected return alts (thin, dashed)
    for (let i = rev.length - 1; i >= 0; i--) {
        if (i === ri) continue;
        drawPathLine(rev[i], '#ff66aa', 2, '6,4', 0.3);
    }
    // Draw selected return (thick, solid, pink)
    if (rev[ri]) {
        drawPathLine(rev[ri], '#ff66aa', 4, null, 0.8);
        for (const pfx of rev[ri].path) highlightNode(pfx, true);
    }
    // Draw selected forward on top (thick, solid, cyan)
    if (fwd[fi]) {
        drawPathLine(fwd[fi], '#00d4ff', 5, null, 0.9);
        for (const pfx of fwd[fi].path) highlightNode(pfx, true);
    }
}

function resetNodeStyles() {
    if (!topo) return;
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
    _lastPathData = null;
    pathLines.forEach(l => map.removeLayer(l));
    pathLines = [];
    clearTraceLines();
    resetNodeStyles();
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

    // Keep polling while discovery runs OR a node command is in progress
    const busy = data.status === 'running' || data.status === 'stopping'
                 || data.command_busy;
    if (!busy) {
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

// --- Settings ---
function switchTab(tabId, btn) {
    document.querySelectorAll('.tab-page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById(tabId).classList.add('active');
    btn.classList.add('active');
}

async function openSettings() {
    try {
        const resp = await fetch('/api/config');
        const cfg = await resp.json();
        populateSettingsForm(cfg);
        document.getElementById('settingsModal').classList.add('open');
        // If no config exists, change title to setup wizard
        if (!cfg.config_exists) {
            document.getElementById('settingsTitle').textContent = 'Initial Setup';
        } else {
            document.getElementById('settingsTitle').textContent = 'Settings';
        }
    } catch (e) {
        alert('Failed to load config: ' + e);
    }
}

function closeSettings() {
    document.getElementById('settingsModal').classList.remove('open');
}

function updateProtocolFields() {
    const proto = document.getElementById('cfgProtocol').value;
    document.getElementById('rowTcp').style.display = proto === 'tcp' ? '' : 'none';
    document.getElementById('rowSerial').style.display = proto === 'serial' ? '' : 'none';
    document.getElementById('rowBle').style.display = proto === 'ble' ? '' : 'none';
}

function populateSettingsForm(cfg) {
    // Radio
    const radio = cfg.radio || {};
    document.getElementById('cfgProtocol').value = radio.protocol || 'tcp';
    document.getElementById('cfgHost').value = radio.host || '';
    document.getElementById('cfgPort').value = radio.port || 5000;
    document.getElementById('cfgSerialPort').value = radio.serial_port || '';
    document.getElementById('cfgBaudrate').value = radio.baudrate || 115200;
    document.getElementById('cfgBleAddress').value = radio.ble_address || '';
    updateProtocolFields();
    document.getElementById('cfgCompanion').value = cfg.companion_prefix || '';
    document.getElementById('testStatus').textContent = '';
    document.getElementById('repeaterList').style.display = 'none';

    // Discovery
    const disc = cfg.discovery || {};
    document.getElementById('cfgMaxRounds').value = disc.max_rounds || 5;
    document.getElementById('cfgTimeout').value = disc.timeout || 30;
    document.getElementById('cfgDelay').value = disc.delay || 5;
    document.getElementById('cfgInferPenalty').value = disc.infer_penalty || 5;
    document.getElementById('cfgHopPenalty').value = disc.hop_penalty ?? 1.0;
    document.getElementById('cfgProbeDist').value = disc.probe_distance_km ?? 2.0;
    document.getElementById('cfgProbeMinSnr').value = disc.probe_min_snr ?? -5.0;
    document.getElementById('cfgSaveFile').value = disc.save_file || 'topology.json';

    // Health penalties
    const hp = cfg.health_penalties || {};
    document.getElementById('cfgHpBatCrit').value = hp.battery_critical ?? 3.0;
    document.getElementById('cfgHpBatWarn').value = hp.battery_warning ?? 1.0;
    document.getElementById('cfgHpTxqHigh').value = hp.txqueue_high ?? 4.0;
    document.getElementById('cfgHpTxqLow').value = hp.txqueue_low ?? 1.0;
    document.getElementById('cfgHpEvtHigh').value = hp.full_evts_high ?? 4.0;
    document.getElementById('cfgHpEvtPer').value = hp.full_evts_per ?? 0.5;
    document.getElementById('cfgHpFloodHigh').value = hp.flood_dup_high ?? 3.0;
    document.getElementById('cfgHpFloodMed').value = hp.flood_dup_medium ?? 1.0;

    // Passwords
    const pws = cfg.default_guest_passwords || ['', 'hello'];
    document.getElementById('cfgGuestPws').value =
        pws.map(p => p === '' ? 'blank' : p).join(', ');
    // Per-repeater passwords
    const pwLines = (cfg.passwords || []).map(p =>
        `${p.prefix || p.name || '?'} ${p.level} ${p.password}`
    ).join('\n');
    document.getElementById('cfgPasswords').value = pwLines;

    // Reset to first tab
    switchTab('tabRadio', document.querySelector('.tab-btn'));
}

function buildConfigFromForm() {
    // Guest passwords
    const pwsRaw = document.getElementById('cfgGuestPws').value;
    const guestPws = pwsRaw.split(',').map(p => {
        p = p.trim();
        return p.toLowerCase() === 'blank' ? '' : p;
    }).filter(p => p !== undefined);

    // Per-repeater passwords
    const pwLines = document.getElementById('cfgPasswords').value.trim();
    const passwords = [];
    if (pwLines) {
        for (const line of pwLines.split('\n')) {
            const parts = line.trim().split(/\s+/);
            if (parts.length >= 2) {
                passwords.push({
                    prefix: parts[0].toUpperCase(),
                    level: parts[1] || 'guest',
                    password: parts.slice(2).join(' '),
                    name: '',
                });
            }
        }
    }

    const v = id => document.getElementById(id).value;
    const f = id => parseFloat(v(id));
    const i = id => parseInt(v(id));

    return {
        radio: {
            protocol: v('cfgProtocol'),
            host: v('cfgHost').trim(),
            port: i('cfgPort') || 5000,
            serial_port: v('cfgSerialPort').trim(),
            baudrate: i('cfgBaudrate') || 115200,
            ble_address: v('cfgBleAddress').trim(),
        },
        companion_prefix: v('cfgCompanion').trim().toUpperCase(),
        discovery: {
            max_rounds: i('cfgMaxRounds') || 5,
            timeout: f('cfgTimeout') || 30,
            delay: f('cfgDelay') || 5,
            infer_penalty: f('cfgInferPenalty') || 5,
            hop_penalty: f('cfgHopPenalty') ?? 1.0,
            probe_distance_km: f('cfgProbeDist') ?? 2.0,
            probe_min_snr: f('cfgProbeMinSnr') ?? -5.0,
            save_file: v('cfgSaveFile') || 'topology.json',
        },
        passwords: passwords,
        default_guest_passwords: guestPws,
        health_penalties: {
            battery_critical: f('cfgHpBatCrit') ?? 3.0,
            battery_warning: f('cfgHpBatWarn') ?? 1.0,
            txqueue_high: f('cfgHpTxqHigh') ?? 4.0,
            txqueue_low: f('cfgHpTxqLow') ?? 1.0,
            full_evts_high: f('cfgHpEvtHigh') ?? 4.0,
            full_evts_per: f('cfgHpEvtPer') ?? 0.5,
            flood_dup_high: f('cfgHpFloodHigh') ?? 3.0,
            flood_dup_medium: f('cfgHpFloodMed') ?? 1.0,
        },
    };
}

async function saveSettings() {
    const cfg = buildConfigFromForm();
    if (!cfg.companion_prefix) {
        alert('Please set a companion prefix (home repeater).');
        return;
    }
    if (!cfg.radio.host && cfg.radio.protocol === 'tcp') {
        alert('Please enter a radio host address.');
        return;
    }
    try {
        const resp = await fetch('/api/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(cfg),
        });
        const data = await resp.json();
        if (data.ok) {
            companionPrefix = cfg.companion_prefix;
            closeSettings();
            refreshTopology();
        } else {
            alert('Save failed: ' + (data.error || 'unknown'));
        }
    } catch (e) { alert('Save failed: ' + e); }
}

async function testRadio() {
    const btn = document.getElementById('btnTestRadio');
    const status = document.getElementById('testStatus');
    btn.disabled = true;
    status.textContent = 'Connecting...';
    status.style.color = '#ff9800';

    const body = {
        protocol: document.getElementById('cfgProtocol').value,
        host: document.getElementById('cfgHost').value.trim(),
        port: parseInt(document.getElementById('cfgPort').value) || 5000,
        serial_port: document.getElementById('cfgSerialPort').value.trim(),
        baudrate: parseInt(document.getElementById('cfgBaudrate').value) || 115200,
        ble_address: document.getElementById('cfgBleAddress').value.trim(),
    };

    try {
        const resp = await fetch('/api/radio/test', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (data.ok) {
            status.textContent = `Connected! ${data.repeaters.length} repeaters found.`;
            status.style.color = '#4caf50';
            showRepeaterList(data.repeaters);
        } else {
            status.textContent = data.error || 'Connection failed';
            status.style.color = '#f44336';
        }
    } catch (e) {
        status.textContent = 'Request failed';
        status.style.color = '#f44336';
    }
    btn.disabled = false;
}

function showRepeaterList(repeaters) {
    const list = document.getElementById('repeaterList');
    if (!repeaters.length) { list.style.display = 'none'; return; }
    list.style.display = 'block';
    list.innerHTML = '';
    const curCompanion = document.getElementById('cfgCompanion').value.toUpperCase();
    for (const r of repeaters) {
        const item = document.createElement('div');
        item.className = 'repeater-item' + (r.prefix === curCompanion ? ' selected' : '');
        item.textContent = `${r.name}  [${r.prefix}]`;
        item.onclick = function() {
            document.getElementById('cfgCompanion').value = r.prefix;
            list.querySelectorAll('.repeater-item').forEach(i => i.classList.remove('selected'));
            item.classList.add('selected');
        };
        list.appendChild(item);
    }
}

// Close modal on overlay click
document.getElementById('settingsModal').addEventListener('click', function(e) {
    if (e.target === this) closeSettings();
});

// --- Init ---
refreshTopology();
toggleAutoRefresh();
// Check if discovery is already running
fetch('/api/discovery/status').then(r => r.json()).then(d => {
    updateDiscUI(d);
    if (d.status === 'running' || d.status === 'stopping') startDiscPoll();
}).catch(() => {});
// Auto-open setup wizard if no config
fetch('/api/config').then(r => r.json()).then(cfg => {
    if (!cfg.config_exists) openSettings();
}).catch(() => {});
</script>
</body>
</html>
"""

