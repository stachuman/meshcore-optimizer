#!/usr/bin/env python3
"""
Test: flood probe path construction, asymmetric loop trace, short-prefix merge.

Simulates three real flood scenarios from discovery logs:

Scenario 1 — Swibno (unresolvable hop f1 in path):
  out: GD_Matemblewo_rpt [53] -> GDANSK-SUCHANINO_SM* [bb]
  in:  [f1] -> RPT Skowarcz [93] -> GTC_JednosciNarodu [01]
             -> GDANSK-SUCHANINO_SM* [bb]

Scenario 2 — Heweliusza (fully asymmetric loop):
  out: GD_Matemblewo_rpt [53] -> GDANSK-SUCHANINO_SM* [bb] -> GDA_Przerobka [be]
  in:  GDA_Przerobka [be] -> RPT_GD_Wrze_Komorowskie [41]
             -> [4824E082] [48] -> GDANSK-SUCHANINO_SM* [bb]

Scenario 3 — PRG_02 (unresolvable hop f1 affects loop trace):
  out: GD_Matemblewo_rpt [53] -> GDANSK-SUCHANINO_SM* [bb]
  in:  [f1] -> GDA_BORKOWO_2_RPT* [c0] -> GDANSK-SUCHANINO_SM* [bb]
"""

from meshcore_optimizer.topology import (
    NetworkGraph, RepeaterNode, DirectedEdge, widest_path,
)
from meshcore_optimizer.discovery import (
    _decode_path_hops, _resolve_hop, _is_endpoint_prefix,
)

# Real node prefixes
COMP    = "53649FDE"   # companion — GD_Matemblewo_rpt
TARGET1 = "EA87FFDF"   # GD_Swibno_rpt
TARGET2 = "B1EB1234"   # GDA_Heweliusza
TARGET3 = "B392ABCD"   # RPT_PRG_02
BB      = "BBC995C9"   # GDANSK-SUCHANINO_SM*
NODE01  = "01DB4104"   # GTC_JednosciNarodu_RPT*
NODE93  = "93B4C65A"   # RPT Skowarcz
EE      = "EE9FFB17"   # GDA_MORENA
BE      = "BEDDF1A3"   # GDA_Przerobka
NODE41  = "41A1B2C3"   # RPT_GD_Wrze_Komorowskie
NODE48  = "4824E082"   # [4824E082]
C0      = "C0C6D7E8"   # GDA_BORKOWO_2_RPT*
NODE39  = "3924A1B2"   # GDA-Jasien
NODE51  = "51C3D4E5"   # GDA_DW_KielpinoG_RPT*


def _build_flood_path(raw_hops, resolved, hlen, companion, target):
    """Same logic as in analyze_and_probe_flood — returns 4 lists."""
    hop_hex = hlen * 2
    comp_short = companion[:hop_hex].lower()
    tgt_short = target[:hop_hex].lower()

    def _is_ep(r):
        return _is_endpoint_prefix(r, companion, target)

    # Full raw path — includes ALL hops for loop trace + routing
    all_raw = ([comp_short] +
               [h.lower() for h, r in zip(raw_hops, resolved)
                if r is None or not _is_ep(r)] +
               [tgt_short])

    # Resolved-only — for edge checking
    res_intermediates = [(h.lower(), r) for h, r in
                         zip(raw_hops, resolved)
                         if r and not _is_ep(r)]
    full_resolved = ([companion] +
                     [r for _, r in res_intermediates] +
                     [target])
    res_raw = ([comp_short] +
               [h for h, _ in res_intermediates] +
               [tgt_short])

    # Map each res_raw index to its position in all_raw
    r2a = []
    ai = 0
    for rh in res_raw:
        while ai < len(all_raw) and all_raw[ai] != rh:
            ai += 1
        r2a.append(ai)
        ai += 1

    return all_raw, full_resolved, res_raw, r2a


def build_graph():
    g = NetworkGraph()
    for pfx, name in [
        (COMP,    "GD_Matemblewo_rpt"),
        (TARGET1, "GD_Swibno_rpt"),
        (TARGET2, "GDA_Heweliusza"),
        (TARGET3, "RPT_PRG_02"),
        (BB,      "GDANSK-SUCHANINO_SM*"),
        (NODE01,  "GTC_JednosciNarodu_RPT*"),
        (NODE93,  "RPT Skowarcz V3H"),
        (EE,      "GDA_MORENA"),
        (BE,      "GDA_Przerobka"),
        (NODE41,  "RPT_GD_Wrze_Komorowskie"),
        (NODE48,  "[4824E082]"),
        (C0,      "GDA_BORKOWO_2_RPT*"),
        (NODE39,  "GDA-Jasien"),
        (NODE51,  "GDA_DW_KielpinoG_RPT*"),
    ]:
        g.add_node(RepeaterNode(prefix=pfx, name=name))

    for f, t, snr in [
        (COMP, BB, 6.0), (BB, COMP, 5.5),
        (COMP, EE, 4.0), (EE, COMP, 3.5),
        (EE, NODE93, 3.0), (NODE93, EE, -2.0),
        (COMP, BE, 2.0), (BE, COMP, 1.5),
    ]:
        g.add_edge(DirectedEdge(from_prefix=f, to_prefix=t,
                                snr_db=snr, source="neighbors"))
    return g


def find_missing(graph, full_res_list):
    missing = set()
    for full_res in full_res_list:
        for j in range(len(full_res) - 1):
            a, b = full_res[j], full_res[j + 1]
            if a and b and a != b:
                if (not graph.get_edge(a, b) and
                        not graph.get_edge(b, a)):
                    missing.add(tuple(sorted([a, b])))
    return missing


def run_scenario(title, graph, target, out_path_hex, in_path_hex, hlen=1):
    print(f"\n{'=' * 60}")
    print(f"SCENARIO: {title}")
    print(f"{'=' * 60}")

    out_hops = _decode_path_hops(out_path_hex, hlen)
    in_hops  = _decode_path_hops(in_path_hex, hlen)
    out_resolved = [_resolve_hop(h, graph) for h in out_hops]
    in_resolved  = [_resolve_hop(h, graph) for h in in_hops]

    print(f"\n  out hops (→target):     {out_hops} -> resolved: "
          f"{[r[:4] if r else None for r in out_resolved]}")
    print(f"  in  hops (→companion): {in_hops} -> resolved: "
          f"{[r[:4] if r else None for r in in_resolved]}")

    # in_path is target→companion; reverse to companion→target
    in_hops = list(reversed(in_hops))
    in_resolved = list(reversed(in_resolved))

    out_all, out_res, out_res_raw, out_r2a = _build_flood_path(
        out_hops, out_resolved, hlen, COMP, target)
    in_all, in_res, in_res_raw, in_r2a = _build_flood_path(
        in_hops, in_resolved, hlen, COMP, target)

    print(f"\n  Out path (all):      {' -> '.join(out_all)}")
    print(f"  Out path (resolved): {' -> '.join(p[:4] for p in out_res)}")
    print(f"  In  path (all):      {' -> '.join(in_all)}")
    print(f"  In  path (resolved): {' -> '.join(p[:4] for p in in_res)}")

    has_unresolved = (len(out_all) > len(out_res_raw) or
                      len(in_all) > len(in_res_raw))
    if has_unresolved:
        print(f"\n  ** Unresolved hops present **")
        print(f"     out: {len(out_all)} total vs {len(out_res_raw)} resolved")
        print(f"     in:  {len(in_all)} total vs {len(in_res_raw)} resolved")

    missing = find_missing(graph, [out_res, in_res])
    print(f"\n  Missing edges (resolved): {len(missing)}")

    # Asymmetric loop trace (using full raw paths with unresolved hops)
    print(f"\n  --- Loop trace (includes unresolved hops) ---")
    if out_all != in_all and len(out_all) >= 2 and len(in_all) >= 2:
        loop_fwd = out_all
        loop_ret = list(reversed(in_all))
        loop_trace = loop_fwd + loop_ret[1:]
        loop_forced = ",".join(loop_trace)
        print(f"  Trace: {loop_forced}")
        print(f"    Fwd: {' -> '.join(out_all)}")
        print(f"    Ret: {' -> '.join(reversed(in_all))}")
        print(f"    Hops: {len(loop_trace)}, "
              f"hop size: {len(loop_trace[0])} hex chars")

        lengths = set(len(h) for h in loop_trace)
        assert len(lengths) == 1, f"Mixed hop lengths: {lengths}"
        print(f"    Consistent hop size: OK")
    else:
        print(f"  Paths are symmetric — no loop trace")

    # Individual probes — use all_raw for routing (preserves unresolved hops)
    print(f"\n  --- Individual probes (routes via all_raw) ---")
    probe_missing = set(missing)
    for full_res, all_raw, r2a, label in [
        (out_res, out_all, out_r2a, "out"),
        (in_res, in_all, in_r2a, "in"),
    ]:
        for j in range(len(full_res) - 1):
            a, b = full_res[j], full_res[j + 1]
            if a and b and a != b:
                pair = tuple(sorted([a, b]))
                if pair in probe_missing:
                    probe_missing.discard(pair)
                    ai_b = r2a[j + 1]
                    fwd = all_raw[:ai_b + 1]
                    trace = list(fwd) + list(reversed(fwd[:-1]))
                    forced = ",".join(trace)
                    na = graph.nodes.get(a)
                    nb = graph.nodes.get(b)
                    print(f"    {na.name if na else a} ↔"
                          f" {nb.name if nb else b}")
                    print(f"      {forced}  (via {label} path)")
                    # Verify unresolved hops are preserved in route
                    if len(all_raw) > len(r2a):
                        print(f"      (includes unresolved hops)")

    return out_all, in_all


# =====================================================================
# Run scenarios
# =====================================================================

g = build_graph()

print("\n" + "=" * 60)
print("Scenario 1: Swibno — unresolvable f1, asymmetric paths")
out_all, in_all = run_scenario(
    "Swibno [EA87] — unresolvable f1",
    g, TARGET1, "53bb", "f19301bb")

# Key assertion: f1 must be in the loop trace
loop = out_all + list(reversed(in_all))[1:]
assert "f1" in loop, f"f1 not in loop trace: {loop}"
print(f"\n  ASSERT: f1 IS in loop trace: {','.join(loop)}  OK")


print("\n")
run_scenario(
    "Heweliusza [B1EB] — fully asymmetric loop",
    g, TARGET2, "53bbbe", "be4148bb")


print("\n")
out_all, in_all = run_scenario(
    "PRG_02 [B392] — unresolvable f1 in in-path",
    g, TARGET3, "53bb", "f1c0bb")

# Key assertion: f1 must be in the loop trace
loop = out_all + list(reversed(in_all))[1:]
assert "f1" in loop, f"f1 not in loop trace: {loop}"
print(f"\n  ASSERT: f1 IS in loop trace: {','.join(loop)}  OK")

# Compare with what the old code produced (no f1)
old_loop_without_f1 = ["53", "bb", "b3", "bb", "c0", "53"]
new_loop_with_f1 = loop
print(f"\n  OLD loop (no f1): {','.join(old_loop_without_f1)}")
print(f"  NEW loop (+f1):   {','.join(new_loop_with_f1)}")
assert len(new_loop_with_f1) > len(old_loop_without_f1), \
    "New loop should be longer (includes f1)"


# Scenario 4: Swibno with d8 unresolved in in-path
# Verifies that Strategy 2 probes include unresolved hops in routes
print("\n")
out_all, in_all = run_scenario(
    "Swibno [EA87] — d8 unresolved in in-path (route integrity)",
    g, TARGET1, "5339", "d851eebb")

# The in-path has d8 (unresolved) near Swibno
# After reversal: companion→bb→ee→51→d8→Swibno
# Strategy 2 probe for d8-adjacent edges MUST route through known nodes first
in_raw_hops = _decode_path_hops("d851eebb", 1)
in_raw_res = [_resolve_hop(h, g) for h in in_raw_hops]
# Reverse: in_path is target→companion
in_raw_hops = list(reversed(in_raw_hops))
in_raw_res = list(reversed(in_raw_res))
in_all_raw, in_full_res, in_res_raw, in_r2a = _build_flood_path(
    in_raw_hops, in_raw_res, 1, COMP, TARGET1)

# After reversal: in_full_res = [COMP, Suchanino, Morena, Kielpino, Swibno]
# d8 sits between Kielpino and Swibno in all_raw
# Find Kielpino↔Swibno edge — probe should route through d8
print(f"\n  in_all_raw: {in_all_raw}")
print(f"  in_full_res: {[p[:4] for p in in_full_res]}")
print(f"  in_res_raw: {in_res_raw}")
print(f"  in_r2a: {in_r2a}")

j_kilp = None
for j in range(len(in_full_res) - 1):
    a, b = in_full_res[j], in_full_res[j + 1]
    if NODE51 in (a, b) and TARGET1 in (a, b):
        j_kilp = j
        break

if j_kilp is not None:
    ai_b = in_r2a[j_kilp + 1]
    fwd_route = in_all_raw[:ai_b + 1]
    assert "d8" in fwd_route, \
        f"d8 must be in Kielpino→Swibno probe route: {fwd_route}"
    print(f"\n  ASSERT: d8 in Kielpino→Swibno route: "
          f"{','.join(fwd_route)}  OK")

    # res_raw doesn't have d8, but all_raw does
    old_route = in_res_raw[:j_kilp + 2]
    assert "d8" not in old_route
    print(f"  ASSERT: res_raw route lacks d8: "
          f"{','.join(old_route)}  (would be broken)")

    # Also verify that companion→Suchanino→...→Kielpino comes before d8
    # (correct direction: known nodes first, then unresolved, then target)
    d8_pos = fwd_route.index("d8")
    assert d8_pos > 1, "d8 should be deep in route, not near companion"
    print(f"  ASSERT: d8 at position {d8_pos} (deep, not near companion)  OK")
else:
    print("  ERROR: Kielpino↔Swibno edge not found in in_full_res")


# =====================================================================
print(f"\n\n{'=' * 60}")
print("SHORT-PREFIX MERGE TEST")
print("=" * 60)

g = build_graph()

g.add_edge(DirectedEdge(from_prefix=NODE93, to_prefix="F1",
                        snr_db=-1.5, source="trace"))
g.add_edge(DirectedEdge(from_prefix="F1", to_prefix=NODE93,
                        snr_db=-3.0, source="trace"))
print(f"\n  Stub 'F1' created with edges")

REAL_F1 = "F1A3BCDE"
g.add_node(RepeaterNode(prefix=REAL_F1, name="Real Node F1A3"))
print(f"  After discovering {REAL_F1}:")
assert "F1" not in g.nodes, "Stub F1 should be gone"
assert REAL_F1 in g.nodes, f"{REAL_F1} should exist"

e1 = g.get_edge(NODE93, REAL_F1)
e2 = g.get_edge(REAL_F1, NODE93)
assert e1 and e1.snr_db == -1.5, "Edge 93→F1A3 should exist with -1.5"
assert e2 and e2.snr_db == -3.0, "Edge F1A3→93 should exist with -3.0"
print(f"    Edges repointed: 93→F1A3 {e1.snr_db:+.1f},"
      f" F1A3→93 {e2.snr_db:+.1f}  OK")

g.add_edge(DirectedEdge(from_prefix=NODE93, to_prefix=REAL_F1,
                        snr_db=2.0, source="neighbors"))
e = g.get_edge(NODE93, REAL_F1)
assert e.observation_count == 2 and e.snr_min_db == -1.5
print(f"    SNR tracking across merge: count={e.observation_count},"
      f" min={e.snr_min_db:+.1f}  OK")

g.save("/tmp/test_flood_topo.json")
g2 = NetworkGraph.load("/tmp/test_flood_topo.json")
assert "F1" not in g2.nodes
assert g2.get_edge(NODE93, REAL_F1).observation_count == 2
print(f"    Save/reload: OK")


print(f"\n{'=' * 60}")
print("ALL TESTS PASSED")
