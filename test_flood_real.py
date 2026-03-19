#!/usr/bin/env python3
"""
Real radio test: flood probe discovery.

Connects to the radio, loads topology, picks a target node,
sends a disc_path flood, decodes the response, builds traces,
and runs them.  Prints everything for manual verification.

Usage:
    # Full flood + analysis (dry run by default)
    python test_flood_real.py --config config.json --topology topology.json --target b3

    # Full flood + actually send traces
    python test_flood_real.py --config config.json --topology topology.json --target b3 --trace

    # Skip flood — replay a captured flood response
    python test_flood_real.py --config config.json --topology topology.json --target b3 --out-path 53bb --in-path 0d9bbb

    # Skip flood — send a specific trace directly
    python test_flood_real.py --config config.json --topology topology.json --target b3 --send-trace 53,bb,b3,bb,9b,0d,53
"""

import argparse
import asyncio
import sys
import time

from meshcore_optimizer.config import load_config
from meshcore_optimizer.topology import (
    NetworkGraph, DirectedEdge, widest_path, widest_path_alternatives,
)
from meshcore_optimizer.radio import connect_radio, find_contact
from meshcore_optimizer.discovery import _decode_path_hops, _resolve_hop
from meshcore_optimizer.constants import TRACE_TIMEOUT_MARGIN


def pick_target(graph, companion_prefix, contact_map, target_prefix=None):
    """Pick a target node — either specified or the one with worst path."""
    if target_prefix:
        target_prefix = target_prefix.upper()
        # Allow short prefix matching
        matches = [p for p in graph.nodes if p.startswith(target_prefix)]
        if not matches:
            print(f"  ERROR: No node matching '{target_prefix}'")
            sys.exit(1)
        if len(matches) > 1:
            print(f"  Ambiguous prefix '{target_prefix}':")
            for m in matches:
                print(f"    {graph.nodes[m].name} [{m}]")
            sys.exit(1)
        pfx = matches[0]
        if pfx not in contact_map:
            print(f"  ERROR: {graph.nodes[pfx].name} [{pfx}] not in contacts")
            sys.exit(1)
        return pfx

    # Auto-pick: worst path quality among contactable nodes
    candidates = []
    for pfx in graph.nodes:
        if pfx == companion_prefix or pfx not in contact_map:
            continue
        pr = widest_path(graph, companion_prefix, pfx)
        if not pr.found:
            candidates.append((-999, 99, pfx))
            continue
        inferred = sum(1 for e in pr.edges if e.source == "inferred")
        candidates.append((pr.bottleneck_snr, -pr.hop_count - inferred * 2, pfx))

    if not candidates:
        print("  ERROR: No contactable nodes found")
        sys.exit(1)

    candidates.sort()
    pfx = candidates[0][2]
    return pfx


def normalize_companion(companion, graph):
    """Normalize companion prefix to match graph node key."""
    if companion in graph.nodes:
        return companion
    for k in graph.nodes:
        if k.upper().startswith(companion.upper()):
            print(f"  (Normalized companion: {companion} -> {k})")
            return k
    return companion


def analyze_flood(companion, target, graph, out_path_hex, in_path_hex,
                  out_hlen, in_hlen):
    """Decode flood response, build paths and trace plan.

    Returns (loop_trace, individual_probes) or (None, []) if nothing to do.
    """
    out_hops = _decode_path_hops(out_path_hex, out_hlen)
    in_hops = _decode_path_hops(in_path_hex, in_hlen)
    out_resolved = [_resolve_hop(h, graph) for h in out_hops]
    in_resolved = [_resolve_hop(h, graph) for h in in_hops]

    def _hop_name(h, r):
        if r and r in graph.nodes:
            return f"{graph.nodes[r].name} [{h}]"
        return f"[{h}]"

    print(f"\n  Decoded:")
    print(f"    out ({len(out_hops)} hops): "
          + (" -> ".join(_hop_name(h, r)
                         for h, r in zip(out_hops, out_resolved))
             or "direct"))
    print(f"    in  ({len(in_hops)} hops): "
          + (" -> ".join(_hop_name(h, r)
                         for h, r in zip(in_hops, in_resolved))
             or "direct"))

    # Endpoint matching — use prefix matching to handle length mismatches
    def _is_endpoint(r):
        ru = r.upper()
        for ep in (companion, target):
            eu = ep.upper()
            if ru.startswith(eu) or eu.startswith(ru):
                return True
        return False

    def _build_flood_path(raw_hops, resolved, hlen):
        hop_hex = hlen * 2
        comp_short = companion[:hop_hex].lower()
        tgt_short = target[:hop_hex].lower()
        full_raw = ([comp_short] +
                    [h.lower() for h, r in zip(raw_hops, resolved)
                     if r is None or not _is_endpoint(r)] +
                    [tgt_short])
        res_intermediates = [(h.lower(), r) for h, r in
                             zip(raw_hops, resolved)
                             if r and not _is_endpoint(r)]
        full_resolved = ([companion] +
                         [r for _, r in res_intermediates] +
                         [target])
        full_raw_resolved = ([comp_short] +
                             [h for h, _ in res_intermediates] +
                             [tgt_short])
        return full_raw, full_resolved, full_raw_resolved

    out_all_raw, out_full_res, out_res_raw = _build_flood_path(
        out_hops, out_resolved, out_hlen)
    in_all_raw, in_full_res, in_res_raw = _build_flood_path(
        in_hops, in_resolved, in_hlen)

    print(f"\n  Flood paths:")
    print(f"    Out (all raw):      {' -> '.join(out_all_raw)}")
    print(f"    Out (resolved):     "
          f"{' -> '.join(p[:4] for p in out_full_res)}")
    print(f"    In  (all raw):      {' -> '.join(in_all_raw)}")
    print(f"    In  (resolved):     "
          f"{' -> '.join(p[:4] for p in in_full_res)}")

    has_unresolved = (len(out_all_raw) > len(out_res_raw) or
                      len(in_all_raw) > len(in_res_raw))
    if has_unresolved:
        print(f"\n  ** Unresolved hops present **")
        print(f"     out: {len(out_all_raw)} total vs {len(out_res_raw)} resolved")
        print(f"     in:  {len(in_all_raw)} total vs {len(in_res_raw)} resolved")

    # Check missing edges
    missing_pairs = set()
    for full_res in [out_full_res, in_full_res]:
        for j in range(len(full_res) - 1):
            a, b = full_res[j], full_res[j + 1]
            if a and b and a != b:
                if not graph.get_edge(a, b) and not graph.get_edge(b, a):
                    missing_pairs.add(tuple(sorted([a, b])))

    print(f"\n  Missing edges (resolved): {len(missing_pairs)}")
    for pa, pb in missing_pairs:
        na = graph.nodes.get(pa)
        nb = graph.nodes.get(pb)
        print(f"    {na.name if na else pa} [{pa[:4]}] "
              f"<-> {nb.name if nb else pb} [{pb[:4]}]")

    # Build trace paths
    print(f"\n{'='*60}")
    print(f"TRACE PLAN")
    print(f"{'='*60}")

    # Strategy 1: Asymmetric loop
    loop_trace = None
    if (len(out_all_raw) >= 2 and len(in_all_raw) >= 2 and
            out_all_raw != in_all_raw):
        loop_fwd = out_all_raw
        loop_ret = list(reversed(in_all_raw))
        loop_hops = loop_fwd + loop_ret[1:]
        loop_trace = ",".join(loop_hops)
        print(f"\n  Strategy 1 — Asymmetric loop trace:")
        print(f"    Forward: {' -> '.join(loop_fwd)}")
        print(f"    Return:  {' -> '.join(loop_ret)}")
        print(f"    Trace:   {loop_trace}")
        print(f"    Hops:    {len(loop_hops)}")
    elif out_all_raw == in_all_raw:
        fwd = out_all_raw
        bounce = fwd + list(reversed(fwd[:-1]))
        loop_trace = ",".join(bounce)
        print(f"\n  Strategy 1 — Symmetric bounce trace:")
        print(f"    Trace: {loop_trace}")
        print(f"    Hops:  {len(bounce)}")
    else:
        print(f"\n  Strategy 1 — Not applicable (paths too short)")

    # Strategy 2: Individual probes
    individual_probes = []
    still_missing = set(missing_pairs)
    for full_res, full_raw, label in [
        (out_full_res, out_res_raw, "out"),
        (in_full_res, in_res_raw, "in"),
    ]:
        for j in range(len(full_res) - 1):
            a, b = full_res[j], full_res[j + 1]
            if a and b and a != b:
                pair = tuple(sorted([a, b]))
                if pair in still_missing:
                    still_missing.discard(pair)
                    raw_route = full_raw[:j + 1]
                    raw_b = full_raw[j + 1]
                    fwd = list(raw_route) + [raw_b]
                    trace_addrs = fwd + list(reversed(fwd[:-1]))
                    forced = ",".join(trace_addrs)
                    na = graph.nodes.get(a)
                    nb = graph.nodes.get(b)
                    individual_probes.append((a, b, forced, label))
                    print(f"\n  Strategy 2 — Probe: "
                          f"{na.name if na else a} [{a[:4]}] "
                          f"<-> {nb.name if nb else b} [{b[:4]}]")
                    print(f"    Trace: {forced}  (via {label} path)")

    return loop_trace, individual_probes


async def execute_traces(mc, graph, companion, target, contact_map, contact,
                         loop_trace, individual_probes, timeout_s, delay,
                         infer_penalty):
    """Send trace(s) over radio and collect edges."""
    from meshcore_optimizer.discovery import _trace_repeater

    if loop_trace:
        print(f"\n  Sending loop trace...")
        loop_contact = contact_map.get(target) or contact
        ok, t_edges, err = await _trace_repeater(
            mc, loop_contact, companion, target,
            graph, timeout_s,
            forced_trace_path=loop_trace)

        if ok and t_edges > 0:
            print(f"\n  +{t_edges} edges from loop trace!")
            graph.infer_reverse_edges(infer_penalty)
        elif err:
            print(f"\n  Loop trace failed: {err}")

        await asyncio.sleep(delay)

    for pa, pb, forced, label in individual_probes:
        if graph.get_edge(pa, pb) or graph.get_edge(pb, pa):
            na = graph.nodes.get(pa)
            nb = graph.nodes.get(pb)
            print(f"\n  Skip {na.name if na else pa} <-> "
                  f"{nb.name if nb else pb} (already found by loop)")
            continue

        na = graph.nodes.get(pa)
        nb = graph.nodes.get(pb)
        print(f"\n  Probing: {na.name if na else pa} <-> "
              f"{nb.name if nb else pb}")

        probe_contact = contact_map.get(pb) or contact_map.get(pa)
        if not probe_contact:
            print(f"    No contact for either endpoint — skip")
            continue

        ok, t_edges, err = await _trace_repeater(
            mc, probe_contact, companion, pb,
            graph, timeout_s,
            forced_trace_path=forced)

        if ok and t_edges > 0:
            print(f"    +{t_edges} edges!")
            graph.infer_reverse_edges(infer_penalty)
        elif err:
            print(f"    Failed: {err}")

        await asyncio.sleep(delay)


async def run_flood_test(config_file, topology_file, target_prefix, do_trace,
                         timeout_s, manual_out=None, manual_in=None,
                         manual_hlen=1, send_trace_path=None):
    config = load_config(config_file)
    companion = config.companion_prefix
    if not companion:
        print("ERROR: companion_prefix not set in config")
        sys.exit(1)

    graph = NetworkGraph.load(topology_file)
    stats = graph.stats()
    print(f"Loaded topology: {stats['nodes']} nodes, {stats['edges']} edges")

    companion = normalize_companion(companion, graph)
    print(f"Companion: {companion} "
          f"({graph.nodes[companion].name if companion in graph.nodes else '?'})")

    # Connect to radio
    print(f"\nConnecting to radio...")
    mc = await connect_radio(config.radio)
    print(f"  Connected!")

    # Load contacts
    print(f"  Loading contacts...")
    await mc.ensure_contacts(follow=True)
    contact_map = {}
    name_map = {}
    for pub_key, ct in mc.contacts.items():
        if not isinstance(ct, dict):
            continue
        pfx = pub_key[:8].upper()
        if pfx:
            contact_map[pfx] = ct
            name_map[pfx] = ct.get('adv_name', '') or f"[{pfx}]"
    print(f"  {len(contact_map)} contacts loaded")

    # Pick target
    target = pick_target(graph, companion, contact_map, target_prefix)
    node = graph.nodes.get(target)
    print(f"\nTarget: {node.name if node else '?'} [{target}]")

    # Show current best path
    pr = widest_path(graph, companion, target)
    if pr.found:
        route = " -> ".join(f"{n} [{p[:4]}]"
                            for n, p in zip(pr.path_names, pr.path))
        print(f"  Current best: {route}")
        print(f"  Bottleneck: {pr.bottleneck_snr:+.1f} dB, "
              f"{pr.hop_count} hops")
        inferred = sum(1 for e in pr.edges if e.source == "inferred")
        if inferred:
            print(f"  Inferred edges: {inferred}")
    else:
        print(f"  No path known!")

    contact = contact_map[target]

    # ---------------------------------------------------------------
    # Mode 1: --send-trace  (skip flood, send a raw trace directly)
    # ---------------------------------------------------------------
    if send_trace_path:
        print(f"\n{'='*60}")
        print(f"DIRECT TRACE: {send_trace_path}")
        print(f"{'='*60}")

        from meshcore_optimizer.discovery import _trace_repeater
        ok, t_edges, err = await _trace_repeater(
            mc, contact, companion, target,
            graph, timeout_s,
            forced_trace_path=send_trace_path)

        if ok and t_edges > 0:
            print(f"\n  +{t_edges} edges!")
            graph.infer_reverse_edges(config.discovery_infer_penalty)
            graph.save(topology_file)
            stats = graph.stats()
            print(f"  Saved: {stats['nodes']} nodes, {stats['edges']} edges")
        elif err:
            print(f"\n  Failed: {err}")

        await mc.disconnect()
        print(f"\nDone!")
        return

    # ---------------------------------------------------------------
    # Mode 2: --out-path / --in-path  (replay captured flood response)
    # ---------------------------------------------------------------
    if manual_out is not None:
        out_path_hex = manual_out
        in_path_hex = manual_in or ""
        out_hlen = manual_hlen
        in_hlen = manual_hlen
        print(f"\n{'='*60}")
        print(f"REPLAY FLOOD (manual paths)")
        print(f"{'='*60}")
        print(f"  out_path: '{out_path_hex}' (hlen={out_hlen})")
        print(f"  in_path:  '{in_path_hex}' (hlen={in_hlen})")

    # ---------------------------------------------------------------
    # Mode 3: default — send disc_path flood
    # ---------------------------------------------------------------
    else:
        from meshcore import EventType
        path_queue = asyncio.Queue()

        def _on_path(event):
            path_queue.put_nowait(event)

        sub = mc.subscribe(EventType.PATH_RESPONSE, _on_path)

        print(f"\n{'='*60}")
        print(f"FLOOD: disc_path to {node.name if node else target}")
        print(f"{'='*60}")

        try:
            print(f"  TX: Sending path discovery...")
            res = await asyncio.wait_for(
                mc.commands.send_path_discovery(contact),
                timeout=timeout_s)
        except Exception as e:
            sub.unsubscribe()
            print(f"  TX: Send failed: {e}")
            await mc.disconnect()
            return

        if res is None or res.type == EventType.ERROR:
            sub.unsubscribe()
            err = res.payload if res else "None"
            print(f"  TX: Failed: {err}")
            await mc.disconnect()
            return

        print(f"  TX: Sent, waiting for response (timeout={timeout_s}s)...")

        try:
            ev = await asyncio.wait_for(path_queue.get(), timeout=timeout_s)
        except asyncio.TimeoutError:
            sub.unsubscribe()
            print(f"  RX: No response (timeout)")
            await mc.disconnect()
            return
        finally:
            sub.unsubscribe()

        out_path_hex = ev.payload.get("out_path", "")
        in_path_hex = ev.payload.get("in_path", "")
        out_hlen = ev.payload.get("out_path_hash_len", 1)
        in_hlen = ev.payload.get("in_path_hash_len", 1)

        print(f"\n  Raw response:")
        print(f"    out_path: '{out_path_hex}' (hlen={out_hlen})")
        print(f"    in_path:  '{in_path_hex}' (hlen={in_hlen})")

    # --- Analyze and plan traces ---
    loop_trace, individual_probes = analyze_flood(
        companion, target, graph,
        out_path_hex, in_path_hex, out_hlen, in_hlen)

    if not loop_trace and not individual_probes:
        print(f"\n  No missing edges — nothing to trace!")
        await mc.disconnect()
        return

    if not do_trace:
        print(f"\n{'='*60}")
        print(f"DRY RUN — add --trace to actually send traces")
        print(f"{'='*60}")
        await mc.disconnect()
        return

    # --- Execute ---
    print(f"\n{'='*60}")
    print(f"EXECUTING TRACES")
    print(f"{'='*60}")

    await execute_traces(
        mc, graph, companion, target, contact_map, contact,
        loop_trace, individual_probes, timeout_s,
        config.discovery_delay, config.discovery_infer_penalty)

    print(f"\n  Saving topology to {topology_file}...")
    graph.save(topology_file)
    stats = graph.stats()
    print(f"  Saved: {stats['nodes']} nodes, {stats['edges']} edges")

    await mc.disconnect()
    print(f"\nDone!")


def main():
    parser = argparse.ArgumentParser(
        description="Real radio test: flood probe discovery")
    parser.add_argument("--config", required=True,
                        help="Config JSON file")
    parser.add_argument("--topology", required=True,
                        help="Topology JSON file")
    parser.add_argument("--target", default=None,
                        help="Target node prefix (auto-picks worst if omitted)")
    parser.add_argument("--trace", action="store_true",
                        help="Actually send traces (default: dry run)")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="Timeout in seconds (default: 30)")

    # Skip flood — replay captured response
    parser.add_argument("--out-path", default=None,
                        help="Manual out_path hex (skip flood)")
    parser.add_argument("--in-path", default=None,
                        help="Manual in_path hex (skip flood)")
    parser.add_argument("--hlen", type=int, default=1,
                        help="Hop hash length in bytes for manual paths "
                             "(default: 1)")

    # Skip flood — send raw trace directly
    parser.add_argument("--send-trace", default=None,
                        help="Send this trace path directly, e.g. "
                             "'53,bb,b3,bb,9b,0d,53'")

    args = parser.parse_args()

    # --send-trace implies --trace
    do_trace = args.trace or (args.send_trace is not None)

    # --out-path implies --trace unless user wants dry run
    # (they can still omit --trace to just see the analysis)

    asyncio.run(run_flood_test(
        args.config, args.topology, args.target, do_trace,
        args.timeout, manual_out=args.out_path, manual_in=args.in_path,
        manual_hlen=args.hlen, send_trace_path=args.send_trace))


if __name__ == "__main__":
    main()
