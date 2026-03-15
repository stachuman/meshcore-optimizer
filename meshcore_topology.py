#!/usr/bin/env python3
"""
MeshCore Network Topology & Widest-Path Router
===============================================
Builds a network graph from repeater neighbor data (partial or complete)
and computes optimal paths using the Widest Path (maximum bottleneck)
algorithm — a modified Dijkstra.

Handles real-world constraints:
  - Not all repeaters allow login (partial topology)
  - Asymmetric links (A→B SNR ≠ B→A SNR)
  - Multiple data sources (neighbors, adverts, traces, manual)
  - Edge confidence levels based on data freshness

Usage:
    # As library
    from meshcore_topology import NetworkGraph, widest_path

    # As standalone CLI
    python meshcore_topology.py --topology network.json --from NODE_A --to NODE_B

Author: Stan (Gdańsk MeshCore Network)
License: MIT
"""

import json
import heapq
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RepeaterNode:
    """A repeater in the network."""
    prefix: str              # first 8 hex chars of public key (uppercase)
    name: str                # human-readable name
    public_key: str = ""     # full public key hex
    lat: float = 0.0
    lon: float = 0.0
    firmware: str = ""
    access_level: str = "none"  # "admin", "guest", "none"
    password: str = ""          # admin or guest password (if known)
    last_seen: str = ""         # ISO timestamp
    status: dict = field(default_factory=dict)    # raw status from req_status
    status_timestamp: str = ""                     # ISO timestamp of last fetch

    def __hash__(self):
        return hash(self.prefix)

    def __eq__(self, other):
        if isinstance(other, RepeaterNode):
            return self.prefix == other.prefix
        return False

    @property
    def health_penalty(self) -> float:
        """Health penalty in dB (0.0 = healthy, higher = worse)."""
        return compute_node_health_penalty(self.status)


def compute_node_health_penalty(status: dict) -> float:
    """
    Compute a health penalty in dB from repeater status data.
    Returns 0.0 for healthy nodes, positive values for degraded nodes.
    The penalty reduces effective path SNR when routing through this node.
    """
    if not status:
        return 0.0

    penalty = 0.0

    # Battery: critical below 3300mV, warning below 3500mV
    bat = status.get("bat", 4200)
    if bat < 3300:
        penalty += 6.0
    elif bat < 3500:
        penalty += 2.0

    # TX queue congestion
    tx_queue = status.get("tx_queue_len", 0)
    if tx_queue > 5:
        penalty += 4.0
    elif tx_queue > 0:
        penalty += 1.0

    # Event queue overflows indicate chronic overload
    full_evts = status.get("full_evts", 0)
    if full_evts > 10:
        penalty += 4.0
    elif full_evts > 0:
        penalty += min(full_evts * 0.5, 3.0)

    # Flood duplicate rate: recv_flood includes dups, flood_dups is the subset
    recv_flood = status.get("recv_flood", 0)
    flood_dups = status.get("flood_dups", 0)
    if recv_flood > 100:
        dup_rate = flood_dups / recv_flood
        if dup_rate > 0.7:
            penalty += 3.0
        elif dup_rate > 0.5:
            penalty += 1.0

    return penalty


@dataclass
class DirectedEdge:
    """
    A directed link from one repeater to another.

    The SNR represents how well 'to_node' hears 'from_node'.
    Since links are asymmetric, A→B and B→A are separate edges.
    """
    from_prefix: str       # source node prefix
    to_prefix: str         # destination node prefix
    snr_db: float          # signal-to-noise ratio in dB
    source: str            # how we learned this: "neighbors", "advert",
                           #   "trace", "manual", "inferred"
    timestamp: str = ""    # when this was measured
    confidence: float = 1.0  # 0.0-1.0, decays with age
    last_heard_ago: int = 0  # seconds since last heard (from neighbors)


class NetworkGraph:
    """
    Directed graph of the MeshCore repeater network.

    Nodes are repeaters, edges are directed radio links with SNR.
    Handles partial topology gracefully.
    """

    def __init__(self):
        self.nodes: dict[str, RepeaterNode] = {}    # prefix → node
        self.edges: dict[str, list[DirectedEdge]] = {}  # from_prefix → [edges]
        self.reverse_edges: dict[str, list[DirectedEdge]] = {}  # to_prefix → [edges]
        self._edge_set: set[tuple[str, str]] = set()  # for dedup

    # --- Node management ---

    def add_node(self, node: RepeaterNode):
        """Add or update a repeater node."""
        self.nodes[node.prefix] = node
        if node.prefix not in self.edges:
            self.edges[node.prefix] = []
        if node.prefix not in self.reverse_edges:
            self.reverse_edges[node.prefix] = []

    def get_node(self, prefix_or_name: str) -> Optional[RepeaterNode]:
        """Find node by prefix (case-insensitive) or name (partial match)."""
        key = prefix_or_name.upper()
        if key in self.nodes:
            return self.nodes[key]

        # Try partial prefix match
        for prefix, node in self.nodes.items():
            if prefix.startswith(key):
                return node

        # Try name match (case-insensitive, partial)
        query = prefix_or_name.lower()
        for node in self.nodes.values():
            if query in node.name.lower():
                return node

        return None

    # --- Edge management ---

    def add_edge(self, edge: DirectedEdge):
        """
        Add or update a directed edge.
        If edge already exists, update only if newer or better source.
        """
        pair = (edge.from_prefix, edge.to_prefix)

        # Ensure nodes exist (create stubs if needed)
        if edge.from_prefix not in self.nodes:
            self.add_node(RepeaterNode(
                prefix=edge.from_prefix, name=f"[{edge.from_prefix}]"))
        if edge.to_prefix not in self.nodes:
            self.add_node(RepeaterNode(
                prefix=edge.to_prefix, name=f"[{edge.to_prefix}]"))

        # Source priority: neighbors > trace > advert > manual > inferred
        source_priority = {
            "neighbors": 5, "trace": 4, "advert": 3,
            "manual": 2, "inferred": 1
        }

        if pair in self._edge_set:
            # Update existing edge if better source
            existing = None
            for e in self.edges.get(edge.from_prefix, []):
                if e.to_prefix == edge.to_prefix:
                    existing = e
                    break

            if existing:
                existing_prio = source_priority.get(existing.source, 0)
                new_prio = source_priority.get(edge.source, 0)

                if new_prio >= existing_prio:
                    existing.snr_db = edge.snr_db
                    existing.source = edge.source
                    existing.timestamp = edge.timestamp
                    existing.confidence = edge.confidence
                    existing.last_heard_ago = edge.last_heard_ago
                return
        else:
            self._edge_set.add(pair)
            self.edges[edge.from_prefix].append(edge)
            if edge.to_prefix not in self.reverse_edges:
                self.reverse_edges[edge.to_prefix] = []
            self.reverse_edges[edge.to_prefix].append(edge)

    def get_neighbors(self, prefix: str) -> list[DirectedEdge]:
        """Get all outgoing edges from a node."""
        return self.edges.get(prefix, [])

    def get_edge(self, from_prefix: str, to_prefix: str) -> Optional[DirectedEdge]:
        """Get a specific edge."""
        for e in self.edges.get(from_prefix, []):
            if e.to_prefix == to_prefix:
                return e
        return None

    # --- Topology building from different sources ---

    def add_from_neighbors_output(self, repeater_prefix: str,
                                  neighbors_text: str,
                                  timestamp: str = ""):
        """
        Parse the output of 'neighbors' command and add edges.

        Format: {pubkey_prefix_hex}:{timestamp}:{snr×4}
        Example: BBC995C9:9904:38

        The neighbors command shows what repeater_prefix hears.
        So the edge direction is: neighbor → repeater_prefix
        (the neighbor's signal was heard by our repeater)
        """
        if not timestamp:
            timestamp = datetime.now().isoformat(timespec='seconds')

        for line in neighbors_text.strip().split('\n'):
            line = line.strip()
            if not line or ':' not in line:
                continue

            # Remove any prefix like "-> " or "GD_... (D): "
            if line.startswith('->'):
                line = line[2:].strip()

            parts = line.split(':')
            if len(parts) < 3:
                continue

            try:
                neighbor_prefix = parts[0].strip().upper()
                heard_timestamp = int(parts[1].strip())
                snr_x4 = int(parts[2].strip())
                snr_db = snr_x4 / 4.0

                self.add_edge(DirectedEdge(
                    from_prefix=neighbor_prefix,
                    to_prefix=repeater_prefix.upper(),
                    snr_db=snr_db,
                    source="neighbors",
                    timestamp=timestamp,
                    confidence=1.0,
                    last_heard_ago=heard_timestamp,
                ))
            except (ValueError, IndexError):
                continue

    def add_from_neighbors_api(self, repeater_prefix: str,
                              neighbours_list: list[dict],
                              timestamp: str = ""):
        """
        Add edges from the meshcore Python API fetch_all_neighbours() result.

        neighbours_list: [{"pubkey": "hex_prefix", "secs_ago": int, "snr": float}, ...]

        The neighbors list shows what repeater_prefix hears.
        Edge direction: neighbor → repeater_prefix
        (the neighbor's signal was heard by our repeater)
        """
        if not timestamp:
            timestamp = datetime.now().isoformat(timespec='seconds')

        for n in neighbours_list:
            neighbor_prefix = n["pubkey"][:8].upper()
            snr_db = n["snr"]
            secs_ago = n.get("secs_ago", 0)

            self.add_edge(DirectedEdge(
                from_prefix=neighbor_prefix,
                to_prefix=repeater_prefix.upper(),
                snr_db=snr_db,
                source="neighbors",
                timestamp=timestamp,
                confidence=1.0,
                last_heard_ago=secs_ago,
            ))

    def add_from_single_hop_trace(self, from_prefix: str, to_prefix: str,
                                  forward_snr: float, return_snr: float,
                                  timestamp: str = ""):
        """
        Add bidirectional edges from a single-hop trace.

        A single-hop trace (ping) to a repeater gives two SNR values:
          - forward_snr: how well the target heard us (A→B)
          - return_snr:  how well we heard the target (B→A)

        This is the highest-quality link data since it's a real
        round-trip measurement, not an advert or inference.
        """
        if not timestamp:
            timestamp = datetime.now().isoformat(timespec='seconds')

        self.add_edge(DirectedEdge(
            from_prefix=from_prefix.upper(),
            to_prefix=to_prefix.upper(),
            snr_db=forward_snr,
            source="trace",
            timestamp=timestamp,
            confidence=1.0,
        ))
        self.add_edge(DirectedEdge(
            from_prefix=to_prefix.upper(),
            to_prefix=from_prefix.upper(),
            snr_db=return_snr,
            source="trace",
            timestamp=timestamp,
            confidence=1.0,
        ))

    def add_from_multihop_trace(self, source_prefix: str,
                                path_prefixes: list[str],
                                forward_snrs: list[float],
                                return_snrs: list[float],
                                timestamp: str = ""):
        """
        Add edges from a multi-hop trace with per-hop bidirectional SNR.

        For trace path: source → A → B → C
          path_prefixes: [A, B, C]
          forward_snrs:  [snr at A, snr at B, snr at C]  (forward direction)
          return_snrs:   [snr at C, snr at B, snr at A]  (return direction)

        Forward gives: source→A, A→B, B→C
        Return gives:  C→B, B→A, A→source
        """
        if not timestamp:
            timestamp = datetime.now().isoformat(timespec='seconds')

        full_path = [source_prefix.upper()] + [p.upper() for p in path_prefixes]

        # Forward direction edges
        for i in range(len(full_path) - 1):
            if i < len(forward_snrs):
                self.add_edge(DirectedEdge(
                    from_prefix=full_path[i],
                    to_prefix=full_path[i + 1],
                    snr_db=forward_snrs[i],
                    source="trace",
                    timestamp=timestamp,
                    confidence=1.0,
                ))

        # Return direction edges (reversed path)
        rev_path = list(reversed(full_path))
        for i in range(len(rev_path) - 1):
            if i < len(return_snrs):
                self.add_edge(DirectedEdge(
                    from_prefix=rev_path[i],
                    to_prefix=rev_path[i + 1],
                    snr_db=return_snrs[i],
                    source="trace",
                    timestamp=timestamp,
                    confidence=1.0,
                ))

    def add_manual_edge(self, from_name: str, to_name: str,
                        snr_db: float, bidirectional: bool = False):
        """
        Manually add an edge by node name or prefix.
        Useful for entering data from mobile app observations.
        """
        from_node = self.get_node(from_name)
        to_node = self.get_node(to_name)

        if not from_node:
            print(f"  Warning: unknown node '{from_name}', creating stub")
            from_node = RepeaterNode(
                prefix=from_name[:8].upper(), name=from_name)
            self.add_node(from_node)

        if not to_node:
            print(f"  Warning: unknown node '{to_name}', creating stub")
            to_node = RepeaterNode(
                prefix=to_name[:8].upper(), name=to_name)
            self.add_node(to_node)

        timestamp = datetime.now().isoformat(timespec='seconds')

        self.add_edge(DirectedEdge(
            from_prefix=from_node.prefix,
            to_prefix=to_node.prefix,
            snr_db=snr_db,
            source="manual",
            timestamp=timestamp,
        ))

        if bidirectional:
            self.add_edge(DirectedEdge(
                from_prefix=to_node.prefix,
                to_prefix=from_node.prefix,
                snr_db=snr_db,
                source="manual",
                timestamp=timestamp,
            ))

    def infer_reverse_edges(self, penalty_db: float = 5.0):
        """
        For edges where we only know one direction, infer the reverse
        with a configurable SNR penalty.

        Default penalty of 5.0 dB is based on measured mean asymmetry
        across real bidirectional links (range: 0–15 dB).
        """
        timestamp = datetime.now().isoformat(timespec='seconds')
        to_add = []

        for from_p, edges in self.edges.items():
            for edge in edges:
                pair_reverse = (edge.to_prefix, edge.from_prefix)
                if pair_reverse not in self._edge_set:
                    to_add.append(DirectedEdge(
                        from_prefix=edge.to_prefix,
                        to_prefix=edge.from_prefix,
                        snr_db=edge.snr_db - penalty_db,
                        source="inferred",
                        timestamp=timestamp,
                        confidence=0.5,
                    ))

        for edge in to_add:
            self.add_edge(edge)

        if to_add:
            print(f"  Inferred {len(to_add)} reverse edges "
                  f"(penalty: -{penalty_db} dB)")

    # --- Access level management ---

    def set_access(self, prefix_or_name: str, level: str,
                   password: str = ""):
        """Set access level for a repeater."""
        node = self.get_node(prefix_or_name)
        if node:
            node.access_level = level
            node.password = password
        else:
            print(f"  Warning: node '{prefix_or_name}' not found")

    def get_loginable_repeaters(self) -> list[RepeaterNode]:
        """Return repeaters we can login to (admin or guest)."""
        return [n for n in self.nodes.values()
                if n.access_level in ("admin", "guest")]

    def get_no_access_repeaters(self) -> list[RepeaterNode]:
        """Return repeaters we cannot login to."""
        return [n for n in self.nodes.values()
                if n.access_level == "none"]

    # --- Statistics ---

    def stats(self) -> dict:
        """Network statistics."""
        total_edges = sum(len(e) for e in self.edges.values())
        snr_values = [e.snr_db for edges in self.edges.values()
                      for e in edges]

        return {
            "nodes": len(self.nodes),
            "edges": total_edges,
            "edge_pairs": len(self._edge_set),
            "loginable": len(self.get_loginable_repeaters()),
            "no_access": len(self.get_no_access_repeaters()),
            "avg_snr": sum(snr_values) / len(snr_values) if snr_values else 0,
            "min_snr": min(snr_values) if snr_values else 0,
            "max_snr": max(snr_values) if snr_values else 0,
        }

    # --- Serialization ---

    def save(self, filename: str):
        """Save topology to JSON."""
        data = {
            "timestamp": datetime.now().isoformat(),
            "nodes": {},
            "edges": [],
        }
        for prefix, node in self.nodes.items():
            nd = {
                "name": node.name,
                "prefix": node.prefix,
                "public_key": node.public_key,
                "lat": node.lat,
                "lon": node.lon,
                "access_level": node.access_level,
                "last_seen": node.last_seen,
            }
            if node.status:
                nd["status"] = node.status
                nd["status_timestamp"] = node.status_timestamp
            data["nodes"][prefix] = nd
        for from_p, edges in self.edges.items():
            for edge in edges:
                data["edges"].append({
                    "from": edge.from_prefix,
                    "to": edge.to_prefix,
                    "snr_db": round(edge.snr_db, 2),
                    "source": edge.source,
                    "timestamp": edge.timestamp,
                    "confidence": edge.confidence,
                    "last_heard_ago": edge.last_heard_ago,
                })

        with open(filename, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, filename: str) -> 'NetworkGraph':
        """Load topology from JSON."""
        with open(filename) as f:
            data = json.load(f)

        graph = cls()

        for prefix, nd in data.get("nodes", {}).items():
            graph.add_node(RepeaterNode(
                prefix=nd["prefix"],
                name=nd["name"],
                public_key=nd.get("public_key", ""),
                lat=nd.get("lat", 0),
                lon=nd.get("lon", 0),
                access_level=nd.get("access_level", "none"),
                last_seen=nd.get("last_seen", ""),
                status=nd.get("status", {}),
                status_timestamp=nd.get("status_timestamp", ""),
            ))

        for ed in data.get("edges", []):
            graph.add_edge(DirectedEdge(
                from_prefix=ed["from"],
                to_prefix=ed["to"],
                snr_db=ed["snr_db"],
                source=ed.get("source", "loaded"),
                timestamp=ed.get("timestamp", ""),
                confidence=ed.get("confidence", 1.0),
                last_heard_ago=ed.get("last_heard_ago", 0),
            ))

        return graph


# ---------------------------------------------------------------------------
# Widest Path Algorithm (Modified Dijkstra)
# ---------------------------------------------------------------------------

@dataclass
class PathResult:
    """Result of widest-path computation."""
    source: str              # source node prefix
    destination: str         # destination node prefix
    path: list               # list of node prefixes in order
    path_names: list         # list of node names in order
    bottleneck_snr: float    # SNR of weakest link
    hop_count: int
    edges: list              # DirectedEdge objects along the path
    found: bool = True

    def __str__(self):
        if not self.found:
            return f"No path from {self.source} to {self.destination}"
        names = " → ".join(self.path_names)
        return (f"{names}  |  bottleneck: {self.bottleneck_snr:+.1f} dB  "
                f"|  hops: {self.hop_count}")


def widest_path(graph: NetworkGraph, source_prefix: str,
                dest_prefix: str,
                min_snr_threshold: float = -15.0,
                excluded_intermediates: set = None,
                excluded_edges: set = None,
                use_node_health: bool = False) -> PathResult:
    """
    Find the path with maximum bottleneck SNR (widest path).

    Uses modified Dijkstra where:
      - d[v] = best achievable min-SNR to reach v from source
      - At each step, pick the node with best bottleneck
      - Update: candidate = min(d[u], snr(u→v))
      - If candidate > d[v]: update (wider bottleneck found)
      - Tie-break: prefer fewer hops

    Args:
        graph: the network graph
        source_prefix: starting node
        dest_prefix: target node
        min_snr_threshold: ignore edges below this SNR
        excluded_intermediates: nodes to avoid as intermediates
                                (source and dest are never excluded)
        excluded_edges: set of (from_prefix, to_prefix) tuples to skip
        use_node_health: apply health penalty for unhealthy intermediates

    Returns:
        PathResult with the optimal path
    """
    source = source_prefix.upper()
    dest = dest_prefix.upper()

    if source not in graph.nodes:
        return PathResult(source, dest, [], [], -999, 0, [], found=False)
    if dest not in graph.nodes:
        return PathResult(source, dest, [], [], -999, 0, [], found=False)

    # d[prefix] = best bottleneck SNR achievable from source
    d = {prefix: float('-inf') for prefix in graph.nodes}
    d[source] = float('inf')  # source to itself has no bottleneck

    hops = {prefix: float('inf') for prefix in graph.nodes}
    hops[source] = 0

    prev = {prefix: None for prefix in graph.nodes}

    # Max-heap: use negative values since heapq is a min-heap
    # Heap entries: (-bottleneck_snr, hop_count, prefix)
    heap = [(-float('inf'), 0, source)]  # source has infinite bottleneck
    visited = set()

    while heap:
        neg_bottleneck, h, u = heapq.heappop(heap)
        bottleneck_u = -neg_bottleneck

        if u in visited:
            continue
        visited.add(u)

        # Early termination
        if u == dest:
            break

        for edge in graph.get_neighbors(u):
            v = edge.to_prefix

            if v in visited:
                continue

            # Skip excluded edges
            if excluded_edges and (u, v) in excluded_edges:
                continue

            # Skip excluded intermediates (but allow dest)
            if (excluded_intermediates and v != dest
                    and v in excluded_intermediates):
                continue

            # Bidirectional: use the weaker direction since the path
            # is physically the same route for both send and return
            reverse_edge = graph.get_edge(v, u)
            effective_snr = min(edge.snr_db,
                                reverse_edge.snr_db if reverse_edge
                                else edge.snr_db)

            # Apply node health penalty for intermediate nodes
            if use_node_health and v != dest and v in graph.nodes:
                effective_snr -= graph.nodes[v].health_penalty

            # Skip edges below threshold
            if effective_snr < min_snr_threshold:
                continue

            # The bottleneck through u→v is min(bottleneck to u, effective SNR)
            candidate = min(bottleneck_u, effective_snr)
            new_hops = hops[u] + 1

            # Update if: wider bottleneck, or same bottleneck + fewer hops
            if (candidate > d[v] or
                    (candidate == d[v] and new_hops < hops[v])):
                d[v] = candidate
                hops[v] = new_hops
                prev[v] = u
                heapq.heappush(heap, (-candidate, new_hops, v))

    # Reconstruct path
    if d[dest] == float('-inf'):
        return PathResult(source, dest, [], [], -999, 0, [], found=False)

    path = []
    current = dest
    while current is not None:
        path.append(current)
        current = prev[current]
    path.reverse()

    # Collect edges along path
    path_edges = []
    for i in range(len(path) - 1):
        edge = graph.get_edge(path[i], path[i + 1])
        if edge:
            path_edges.append(edge)

    path_names = [graph.nodes[p].name if p in graph.nodes else p
                  for p in path]

    return PathResult(
        source=source,
        destination=dest,
        path=path,
        path_names=path_names,
        bottleneck_snr=d[dest],
        hop_count=len(path) - 1,
        edges=path_edges,
        found=True,
    )


def widest_path_alternatives(graph: NetworkGraph, source_prefix: str,
                             dest_prefix: str, k: int = 3,
                             min_snr_threshold: float = -15.0,
                             use_node_health: bool = False
                             ) -> list[PathResult]:
    """
    Find up to k alternative paths, each avoiding intermediates of
    better paths. For direct (1-hop) paths, the direct edge is blocked
    to force multi-hop alternatives.
    """
    results = []
    seen_paths = set()
    excluded = set()
    blocked_edges = set()

    for _ in range(k):
        pr = widest_path(graph, source_prefix, dest_prefix,
                         min_snr_threshold=min_snr_threshold,
                         excluded_intermediates=excluded,
                         excluded_edges=blocked_edges,
                         use_node_health=use_node_health)
        if not pr.found:
            break
        path_key = tuple(pr.path)
        if path_key in seen_paths:
            break
        seen_paths.add(path_key)
        results.append(pr)
        intermediates = pr.path[1:-1]
        if intermediates:
            for p in intermediates:
                excluded.add(p)
        else:
            # Direct 1-hop path — block the edge to find multi-hop routes
            blocked_edges.add((pr.path[0], pr.path[1]))

    return results


def all_pairs_widest(graph: NetworkGraph,
                     min_snr_threshold: float = -15.0) -> dict:
    """
    Compute widest path between all pairs of repeaters.
    Returns dict[(source, dest)] → PathResult
    """
    results = {}
    prefixes = list(graph.nodes.keys())

    for src in prefixes:
        for dst in prefixes:
            if src != dst:
                result = widest_path(graph, src, dst, min_snr_threshold)
                results[(src, dst)] = result

    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_topology_report(graph: NetworkGraph):
    """Print a topology overview."""
    stats = graph.stats()

    print("\n" + "=" * 40)
    print("  MESHCORE NETWORK TOPOLOGY")
    print(f"  Generated: {datetime.now().isoformat(timespec='seconds')}")
    print("=" * 40)

    print(f"\n  Nodes: {stats['nodes']}  |  "
          f"Edges: {stats['edges']}  |  "
          f"Loginable: {stats['loginable']}  |  "
          f"No access: {stats['no_access']}")

    if stats['edges'] > 0:
        print(f"  SNR range: {stats['min_snr']:+.1f} to "
              f"{stats['max_snr']:+.1f} dB  "
              f"(avg: {stats['avg_snr']:+.1f} dB)")

    # Adjacency list
    print(f"\n  ADJACENCY LIST (who hears whom)")
    print("  " + "-" * 36)
    for prefix, node in sorted(graph.nodes.items(), key=lambda x: x[1].name):
        outgoing = graph.edges.get(prefix, [])

        access_icon = {"admin": "🔓", "guest": "👤", "none": "🔒"}
        icon = access_icon.get(node.access_level, "?")

        print(f"\n  {icon} {node.name} [{prefix}]")

        if outgoing:
            print(f"     Hears:")
            for e in sorted(outgoing, key=lambda x: x.snr_db, reverse=True):
                to_name = graph.nodes[e.to_prefix].name if e.to_prefix in graph.nodes else e.to_prefix
                quality = "✅" if e.snr_db >= 5 else ("⚠️" if e.snr_db >= 0 else "❌")
                src_tag = f" [{e.source}]" if e.source != "neighbors" else ""
                print(f"       → {to_name:<25} {e.snr_db:>+6.1f} dB {quality}{src_tag}")

        if not outgoing:
            print(f"     (no outgoing link data)")


def print_path_result(result: PathResult, graph: NetworkGraph):
    """Print detailed path result."""
    if not result.found:
        src_name = graph.nodes[result.source].name if result.source in graph.nodes else result.source
        dst_name = graph.nodes[result.destination].name if result.destination in graph.nodes else result.destination
        print(f"\n  ❌ No path found: {src_name} → {dst_name}")
        return

    print(f"\n  {'─' * 60}")
    print(f"  Path: {' → '.join(result.path_names)}")
    print(f"  Bottleneck SNR: {result.bottleneck_snr:+.1f} dB  |  "
          f"Hops: {result.hop_count}")
    print()

    # Visual path with SNR per link
    for i, edge in enumerate(result.edges):
        from_name = graph.nodes[edge.from_prefix].name if edge.from_prefix in graph.nodes else edge.from_prefix
        to_name = graph.nodes[edge.to_prefix].name if edge.to_prefix in graph.nodes else edge.to_prefix

        bar_len = max(0, int((edge.snr_db + 15) * 2))
        bar = "█" * min(bar_len, 40)
        quality = "✅" if edge.snr_db >= 5 else ("⚠️" if edge.snr_db >= 0 else "❌")
        is_bottleneck = " ← BOTTLENECK" if edge.snr_db == result.bottleneck_snr else ""
        src_tag = f" ({edge.source})" if edge.source != "neighbors" else ""

        print(f"  {from_name}")
        print(f"    ──{edge.snr_db:>+6.1f} dB──→  {quality} {bar}{is_bottleneck}{src_tag}")

    print(f"  {result.path_names[-1]}")


def print_all_pairs_report(results: dict, graph: NetworkGraph):
    """Print matrix of all-pairs widest paths."""
    prefixes = sorted(graph.nodes.keys(), key=lambda p: graph.nodes[p].name)

    print(f"\n  ALL-PAIRS BOTTLENECK SNR MATRIX")
    print("  " + "-" * 36)

    # Header
    names = [graph.nodes[p].name[:8] for p in prefixes]
    header = "  " + " " * 14 + "  ".join(f"{n:>8}" for n in names)
    print(header)

    for src in prefixes:
        src_name = graph.nodes[src].name[:12]
        row = f"  {src_name:<14}"
        for dst in prefixes:
            if src == dst:
                row += f"{'---':>10}"
            else:
                r = results.get((src, dst))
                if r and r.found:
                    snr_str = f"{r.bottleneck_snr:+.1f}"
                    row += f"{snr_str:>10}"
                else:
                    row += f"{'✗':>10}"
        print(row)


# ---------------------------------------------------------------------------
# CLI main
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="MeshCore Network Topology & Widest-Path Router")

    parser.add_argument("--topology", metavar="FILE",
                        help="Load topology from JSON file")
    parser.add_argument("--from-node", metavar="PREFIX",
                        help="Source node for path computation")
    parser.add_argument("--to-node", metavar="PREFIX",
                        help="Destination node for path computation")
    parser.add_argument("--all-pairs", action="store_true",
                        help="Compute all-pairs widest paths")
    parser.add_argument("--infer", type=float, default=None, metavar="DB",
                        help="Infer reverse edges with penalty (default: 5.0)")
    parser.add_argument("--min-snr", type=float, default=-15.0,
                        help="Minimum SNR threshold (default: -15.0)")

    args = parser.parse_args()

    if not args.topology:
        parser.print_help()
        print("\n  Quick start:")
        print("    python meshcore_topology.py --topology net.json --all-pairs")
        print("    python meshcore_topology.py --topology net.json "
              "--from-node MORENA --to-node SWIBNO")
        return

    graph = NetworkGraph.load(args.topology)
    print(f"Loaded topology: {graph.stats()['nodes']} nodes, "
          f"{graph.stats()['edges']} edges")

    if args.infer is not None:
        graph.infer_reverse_edges(args.infer)

    print_topology_report(graph)

    if args.from_node and args.to_node:
        src = graph.get_node(args.from_node)
        dst = graph.get_node(args.to_node)
        if src and dst:
            result = widest_path(graph, src.prefix, dst.prefix, args.min_snr)
            print_path_result(result, graph)
        else:
            print(f"Node not found: {args.from_node if not src else args.to_node}")

    if args.all_pairs:
        results = all_pairs_widest(graph, args.min_snr)
        print_all_pairs_report(results, graph)


if __name__ == "__main__":
    main()
