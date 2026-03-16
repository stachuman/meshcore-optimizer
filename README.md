# MeshCore Network Topology Discovery & Path Optimizer

Automated mesh network topology discovery, visualization, and optimal path routing for [MeshCore](https://github.com/rocketscream/MeshCore) LoRa repeater networks.

Connects to your MeshCore companion radio, progressively discovers all reachable repeaters, maps their interconnections with bidirectional SNR measurements, and computes optimal routes using a widest-path (maximum bottleneck) algorithm.

![Network Map](view_1.jpg)

## Features

**Topology Discovery**
- Progressive multi-round discovery starting from your companion repeater
- Trace sweep (no login needed) + neighbor table fetch (login with guest/admin passwords)
- Bidirectional SNR measurements for every link
- Automatic reverse edge inference for one-way links
- Resumable discovery -- interrupt anytime, continue later
- Alternative path probing through different intermediates

**Path Optimization**
- Widest-path algorithm (modified Dijkstra) finds routes with the best worst-link SNR
- Considers asymmetric links -- uses the weaker direction for realistic routing
- Alternative paths computed by excluding intermediates of better routes
- Health-aware routing -- penalizes congested or low-battery intermediates

**Node Health Monitoring**
- Collects repeater status during discovery (battery, TX queue, event overflows, flood duplicate rate, uptime)
- Computes health penalty scores that feed into path optimization
- Identifies problematic nodes (congestion, low battery, RF overload)

**Interactive Web Map**
- Real-time network visualization on OpenStreetMap (dark theme)
- Nodes color-coded by health, links colored by SNR quality
- Click any two nodes to compute and display optimal route with per-hop SNR
- Start/stop discovery from the browser -- live log streaming
- Auto-refresh every 10 seconds during discovery
- Nodes without GPS coordinates are estimated from neighbor positions
- Accessible from any device on your local network

**Text UI Manager**
- Menu-driven interface for discovery, path finding, topology editing, and configuration
- Network reports: topology overview, all-pairs bottleneck matrix, weak links, node health
- Manual data entry for traces and neighbor tables (from mobile app observations)
- Password and radio configuration management

## Requirements

- Python 3.7+
- A MeshCore companion radio accessible via TCP, serial, or BLE
- The [meshcore](https://pypi.org/project/meshcore/) Python library

## Installation

```bash
git clone https://github.com/stachuman/meshcore-optimizer.git
cd meshcore-optimizer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Copy the example config and edit for your setup:

```bash
cp config.example.json config.json
```

**config.json:**
```json
{
  "radio": {
    "protocol": "tcp",
    "host": "192.168.1.100",
    "port": 5000
  },
  "companion_prefix": "00000000",
  "discovery": {
    "max_rounds": 5,
    "timeout": 30.0,
    "delay": 5.0,
    "infer_penalty": 5.0,
    "save_file": "topology.json"
  },
  "passwords": [],
  "default_guest_passwords": ["", "hello"]
}
```

- **radio.protocol** -- `tcp`, `serial`, or `ble`
- **radio.host / port** -- for TCP connections (e.g., to a MeshCore device running a TCP server)
- **companion_prefix** -- first 8 hex chars of your companion repeater's public key
- **discovery.infer_penalty** -- SNR penalty (dB) applied when estimating reverse links

Optionally set up known passwords for repeaters:

```bash
cp passwords_example.json passwords.json
```

## Usage

### Web Map (recommended)

Start the interactive map server:

```bash
python meshcore_web.py
```

Open the displayed URL (e.g., `http://192.168.1.20:8080`) in a browser on any device on your network. From there you can:

- Start/stop discovery using the panel controls
- Click two nodes to find the optimal path between them
- Toggle health-aware routing
- Watch nodes appear in real-time during discovery

Options:
```
--config, -C FILE    Config file (default: config.json)
--topology, -f FILE  Topology file (default: topology.json)
--port, -p PORT      HTTP port (default: 8080)
```

### Command-Line Discovery

Run discovery directly:

```bash
python meshcore_discovery.py --config config.json
```

Options:
```
--config, -C FILE       Config file (default: config.json)
--topology FILE         Load existing topology to extend
--companion PREFIX      Override companion prefix
--max-rounds N          Max discovery rounds
--timeout SECS          Per-operation timeout
--plan                  Dry run -- show what would be discovered
--interactive, -i       Manual data entry mode (no radio needed)
```

### Path Computation

Compute optimal routes from an existing topology file:

```bash
# Best path between two nodes
python meshcore_topology.py --topology topology.json --from-node MORENA --to-node SWIBNO

# All-pairs bottleneck matrix
python meshcore_topology.py --topology topology.json --all-pairs

# With reverse edge inference
python meshcore_topology.py --topology topology.json --all-pairs --infer 5.0
```

### Text UI Manager

Full-featured menu interface:

```bash
python meshcore_manager.py
```

Provides discovery control, path finding, network reports, topology editing, and configuration management in a single interface.

## How It Works

### Discovery Process

1. **Round 0** -- Login to companion repeater, fetch its neighbor table (seeds the graph)
2. **Rounds 1+** -- Two phases per round:
   - **Trace sweep** -- Send trace packets to all reachable nodes (best SNR first), collecting bidirectional link measurements without needing login
   - **Login phase** -- Authenticate to accessible repeaters for full neighbor tables, and collect node status (battery, congestion, uptime)
3. **After each update** -- Infer reverse edges for one-way links, save topology to disk

### Widest-Path Algorithm

Routes are computed using a modified Dijkstra algorithm that maximizes the minimum SNR along a path (the "bottleneck"). Key features:

- **Bidirectional SNR** -- Uses `min(forward_snr, reverse_snr)` since packets must travel both directions
- **Hop-count tie-breaking** -- When two paths have equal bottleneck SNR, the shorter one wins
- **Health penalties** -- Optionally reduces effective SNR through nodes with low battery, congested TX queues, event queue overflows, or high flood duplicate rates
- **Alternative paths** -- Computed by excluding intermediates of better paths to find diverse routes

### Data Sources (in order of quality)

| Source | Method | Quality |
|--------|--------|---------|
| `neighbors` | Guest login + fetch neighbor table | Full SNR data with timestamps |
| `trace` | Send trace through repeater chain | Bidirectional per-hop SNR |
| `inferred` | Estimated reverse of known edges | Forward SNR minus penalty |

## Project Structure

```
meshcore_topology.py    Graph data structures, widest-path algorithm, serialization
meshcore_discovery.py   Progressive radio-based topology discovery
meshcore_web.py         Interactive web map with live discovery control
meshcore_manager.py     Text UI for network management
config.example.json     Example radio/discovery configuration
passwords_example.json  Example repeater credentials
```

## License

MIT -- see [LICENSE](LICENSE).

## Author

Stan -- Gdansk MeshCore Network
