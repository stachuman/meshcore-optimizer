"""
Interactive topology discovery — manual data entry, no radio required.
"""

from meshcore_optimizer.topology import (
    NetworkGraph, RepeaterNode,
    widest_path, all_pairs_widest,
    print_topology_report, print_path_result, print_all_pairs_report,
)
from meshcore_optimizer.config import RepeaterAccess


def interactive_discovery():
    """
    Simulate progressive discovery by entering neighbors/trace data manually.
    """
    from meshcore_optimizer.discovery import plan_discovery

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
