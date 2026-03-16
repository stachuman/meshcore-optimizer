# Discovery Process & Routing Algorithm

This document describes how MeshCore Optimizer discovers network topology and computes optimal routes.

## Overview

Discovery is a multi-round, multi-phase process. Each round progressively builds a more complete picture of the mesh network by combining four data collection methods, ordered from cheapest to most expensive:

```
Round 0: Seed — login to companion repeater, fetch its neighbor table
Round 1+:
  Phase 1 — Trace sweep      (cheap, no login needed)
  Phase 2 — Login & neighbors (richer data, needs password)
  Phase 3 — Proximity probe   (targeted, tests GPS-based hypotheses)
  Phase 4 — Flood discovery   (expensive, last resort)
```

Rounds repeat until no new edges are discovered or `max_rounds` is reached.

## Resume / Continue Mode

Discovery state is saved after every graph update, enabling stop-and-resume:

- **State file** (`topology_discovery_state.json`) tracks:
  - `traced_set` — nodes that have been traced (Phase 1)
  - `logged_in_set` — nodes that have been logged into (Phase 2)
  - `current_round` — last completed round number
  - `completed` — whether discovery finished normally

- **Resume behavior**: on restart, previously traced/logged nodes are skipped. Discovery continues from the next round.

- **Stale detection**: if the topology file was deleted (fresh start) but an old state file exists, the system detects the mismatch (graph has 0-1 nodes but state claims many traced) and starts fresh.

- **Clean completion**: when discovery ends normally (no new edges or max rounds), the state file is deleted.

## Round 0: Seeding the Graph

The first step is always to query the companion repeater (the base repeater our radio is connected to).

1. Find the companion in the contact list
2. Try passwords in priority order (exact match, name match, wildcards, defaults)
3. On successful login:
   - Request node **status** (battery, TX queue, event overflows, uptime) with 2 retries
   - Fetch full **neighbor table** via binary API (retry up to 4 times)
4. Add all neighbor edges to the graph
5. **Infer reverse edges** for any one-directional links
6. Save topology

This seeds the graph with the companion's local view of the network — typically 10-40 neighbor nodes.

## Phase 1: Trace Sweep

Traces every reachable node to get bidirectional SNR measurements. No login required — traces work on any repeater.

### Best-First Selection

Each iteration picks the untreated node with the best "effective SNR" path from the companion:

1. For all nodes not yet in `traced_set`, compute `widest_path()` from companion
2. Apply intermediate failure penalty: `-10 dB * consecutive_fails` for each intermediate
3. Select the node with highest effective score

### Alternative Paths

For each target, up to 3 alternative paths are computed. Each alternative excludes intermediates used by better paths, forcing different routes. Alternatives too weak (> 10 dB below primary) are skipped.

### Trace Mechanics

A trace sends a packet along a specified route and measures SNR at each hop:

```
Trace path: companion → A → B → target → B → A → companion
```

The response contains per-hop SNR values. The first and last entries (client-to-repeater) are skipped — only mesh radio hops are extracted as edges.

### Failure Handling

When a trace fails through an intermediate, that intermediate's fail counter increments. At 3 consecutive failures, the intermediate is **deprioritized** (penalized in scoring, not excluded). This allows the algorithm to try other routes first while still eventually retrying.

After all alternatives are exhausted for a target, it's marked as traced (won't be retried this round).

## Phase 2: Login & Neighbors

Logs into reachable repeaters to fetch their full neighbor tables. This provides the richest data — every node the repeater can hear, with SNR and timestamps.

### Candidate Selection

Not every node is attempted:
- Must have a contact in the radio's contact list
- Must not already be in `logged_in_set`
- Must have at least one matching password
- Must be reachable with bottleneck SNR >= -10.0 dB

Candidates are sorted by path quality (best first).

### Data Collection

On successful login:
1. **Status request** with 2 retries — updates node health data
2. **Neighbor table fetch** with up to 4 retries — adds edges to graph
3. **Logout** to clean up session
4. Infer reverse edges and save topology

### Password Matching Priority

1. Exact prefix match from password list
2. Name substring match (case-insensitive)
3. Wildcard entries (`*`)
4. Default guest passwords (e.g., blank, "hello")

## Phase 3: Proximity Probe

After phases 1 and 2, some nearby nodes may have no known link between them — a "proximity gap." This phase tests those gaps using targeted traces.

### Gap Detection

Using GPS coordinates of all nodes:
1. Compute haversine distance between every pair
2. Filter: distance < `probe_distance_km` (default 2.0 km, configurable)
3. Filter: no edge exists in either direction
4. Sort by distance (closest first)

### Probe Execution

For each gap (node A ↔ node B):
1. Check if at least one is reachable from companion
2. Pick the one with the better path as "via", the other as "target"
3. Construct a trace that goes through both:
   ```
   companion → ... → via_node → target_node → ... → companion
   ```
4. The trace response reveals the via→target SNR directly

### Why This Matters

Some links exist physically but aren't discovered by phases 1-2:
- Node A's neighbor table doesn't list B (different timing, or login failed)
- Direct trace to B uses a different route that doesn't pass through A
- The firmware knows the link but our graph doesn't

Example: two repeaters 330m apart in the same neighborhood, both heard by the companion, but neither's neighbor table lists the other. A proximity probe discovers the direct link.

## Phase 4: Flood Discovery

Last resort for nodes where:
- Trace failed or timed out
- Login failed (no valid password, unreachable)
- Proximity probe didn't help
- But we believe the node should be reachable

Uses MeshCore's `send_path_discovery` command, which sends a flood packet and waits for the network to respond with discovered routes.

### Candidate Selection

Only nodes that:
- Have zero **measured** edges (no "neighbors" or "trace" source edges)
- Have already been attempted by trace (in `traced_set`)
- Have a contact in the radio's contact list

### Limitations

- Expensive: flood packets consume airtime across the entire mesh
- Response provides route information (out_path, in_path) but no SNR values
- Used sparingly and only as a final fallback

## Routing Algorithm: Widest Path

Routes are computed using a **widest-path algorithm** — a modified Dijkstra that maximizes the minimum SNR along a path (the "bottleneck").

### Core Idea

Instead of minimizing total distance, we maximize the weakest link:
- `d[v]` = best achievable minimum-SNR to reach node `v` from source
- At each step, pick the unvisited node with highest `d[v]`
- Update: `candidate = min(d[u], effective_snr(u→v))`
- If `candidate > d[v]`: update (wider bottleneck found)
- Tie-break: prefer fewer hops

### Bidirectional SNR

Mesh packets travel both directions (send and acknowledge), so both link directions matter:

| Forward Edge | Reverse Edge | Effective SNR |
|---|---|---|
| Measured | Measured | `min(forward, reverse)` — conservative |
| Measured | Inferred | `measured - 2.0 dB` — trust measurement with small penalty |
| Inferred | Measured | `measured - 2.0 dB` — same |
| Measured | None | `measured` — no reverse data available |

The `-2.0 dB` softening for inferred edges prevents excessive double-penalization. Without it, a 5 dB infer penalty combined with `min()` creates a devastating compound penalty on partially-measured links.

### Hop Penalty

Each hop subtracts `hop_penalty` dB (default 1.0, configurable) from the path score:

```
score = min(bottleneck_to_u, effective_snr_u_v) - hop_penalty
```

This means a 3-hop path needs `2 * hop_penalty` dB better bottleneck SNR than a 1-hop path to be preferred. Set to 0 to disable (pure SNR optimization).

### Health Penalty (optional)

When health-aware routing is enabled, intermediate nodes with poor health reduce the effective SNR of paths through them:

| Factor | Condition | Default Penalty |
|---|---|---|
| Battery critical | < 3300 mV | 3.0 dB |
| Battery warning | < 3500 mV | 1.0 dB |
| TX queue high | > 5 items | 4.0 dB |
| TX queue low | > 0 items | 1.0 dB |
| Event overflows | > 10 | 4.0 dB |
| Event overflows | 1-10 | 0.5 per event (max 3.0) |
| Flood dup rate | > 70% | 3.0 dB |
| Flood dup rate | > 50% | 1.0 dB |

All penalties are configurable in `config.json` under `health_penalties`. Set any value to 0 to disable that factor.

Health penalty is **not applied to the destination node** — only intermediates, since you must reach the destination regardless of its health.

### Alternative Paths

`widest_path_alternatives(k=3)` finds up to `k` diverse paths:

1. Find best path (primary)
2. Exclude all intermediates of the primary, find next best path
3. Exclude all intermediates of paths 1-2, find next
4. For direct (1-hop) paths: block the direct edge to force multi-hop alternatives

This provides fallback options when the primary path fails.

## Edge Data Sources

Edges are tagged with their source, which determines update priority:

| Source | Priority | Method | Data Quality |
|---|---|---|---|
| `neighbors` | 5 (highest) | Guest login + API | Full SNR, timestamp |
| `trace` | 4 | Send trace packet | Bidirectional per-hop SNR |
| `advert` | 3 | Advertisement data | Basic connectivity |
| `manual` | 2 | User-entered | Variable |
| `inferred` | 1 (lowest) | Reverse edge guess | Measured SNR minus penalty |

When an edge already exists, it's only updated if the new data has equal or higher priority.

## Inferred Reverse Edges

For one-directional links (A hears B, but we don't know if B hears A), a reverse edge is created with:

```
reverse_snr = forward_snr - infer_penalty
```

Default `infer_penalty` is 5.0 dB, based on measured mean asymmetry across real bidirectional links (range 0-15 dB). Configurable in `config.json`.

Inferred edges have:
- `source = "inferred"`
- `confidence = 0.5`

They are replaced when real measurement data arrives (higher source priority).

## Configuration Reference

All discovery parameters in `config.json`:

```json
{
  "discovery": {
    "max_rounds": 5,
    "timeout": 30.0,
    "delay": 5.0,
    "infer_penalty": 5.0,
    "hop_penalty": 1.0,
    "probe_distance_km": 2.0,
    "save_file": "topology.json"
  }
}
```

| Parameter | Default | Description |
|---|---|---|
| `max_rounds` | 5 | Maximum discovery rounds |
| `timeout` | 30.0 | Per-operation timeout (seconds) |
| `delay` | 5.0 | Pause between operations (seconds) |
| `infer_penalty` | 5.0 | SNR penalty for inferred reverse edges (dB) |
| `hop_penalty` | 1.0 | Per-hop cost in path scoring (dB) |
| `probe_distance_km` | 2.0 | Max distance for proximity gap probing (km) |
| `save_file` | topology.json | Output topology file |
