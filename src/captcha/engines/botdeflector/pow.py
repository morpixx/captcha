"""BotDeflector invisible proof of work (Cuckoo Cycle).

The widget's pow.js builds a deterministic graph from the session id and looks
for one simple cycle of a fixed length; the server re-runs the same
deterministic walk and compares the *first* cycle a browser would have found,
so a mathematically valid but differently ordered cycle is rejected.

Algorithm and the cross-language test vector are the public reverse from
glizzykingdreko/botdeflector-pow (MIT): every edge e in [0, 2**EDGES_BITS)
maps to endpoints derived from sha256(u32le(e) || seed), and a challenge hash
only picks the DFS start node. Nothing here talks to the network;
solvers/antibot/botdeflector owns the HTTP flow.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from captcha.domain.errors import SensorError

#: Guard rails for payload-supplied graph sizes: 2**20 edges is ~50 ms of work,
#: 2**28 is a task that never finishes. The server never ships more than 2**20.
MIN_BITS = 4
MAX_BITS = 22
MIN_CYCLE = 3
MAX_CYCLE = 32


@dataclass(frozen=True, slots=True)
class ChallengeParams:
    """The params blob of a /pow/get response."""

    nodes_bits: int
    edges_bits: int
    cycle_length: int
    graph_seed: str

    @property
    def nodes(self) -> int:
        return 1 << self.nodes_bits

    @property
    def edges(self) -> int:
        return 1 << self.edges_bits

    @classmethod
    def from_payload(cls, params: dict[str, Any], *, session_id: str | None = None) -> ChallengeParams:
        """Read the wire blob, falling back to sessionId as the graph seed.

        The payload never names the seed; the browser feeds sessionId in.
        """
        if not isinstance(params, dict):
            raise SensorError(
                "botdeflector pow/get returned no params blob",
                context={"got": type(params).__name__},
            )
        nodes_bits = _int(params.get("NODES_BITS"))
        edges_bits = _int(params.get("EDGES_BITS"))
        cycle_length = _int(params.get("CYCLE_LENGTH"))
        seed = params.get("GRAPH_SEED") or session_id
        if not seed:
            raise SensorError(
                "botdeflector pow/get carries neither GRAPH_SEED nor sessionId",
                context={"keys": sorted(params)},
            )
        if not MIN_BITS <= nodes_bits <= MAX_BITS or not MIN_BITS <= edges_bits <= MAX_BITS:
            raise SensorError(
                "botdeflector pow/get graph size is out of range",
                context={"NODES_BITS": nodes_bits, "EDGES_BITS": edges_bits},
            )
        if not MIN_CYCLE <= cycle_length <= MAX_CYCLE:
            raise SensorError(
                "botdeflector pow/get cycle length is out of range",
                context={"CYCLE_LENGTH": cycle_length},
            )
        if cycle_length > edges_bits:
            raise SensorError(
                "botdeflector cycle cannot be longer than the edge space",
                context={"CYCLE_LENGTH": cycle_length, "EDGES_BITS": edges_bits},
            )
        return cls(nodes_bits, edges_bits, cycle_length, str(seed))


@dataclass(frozen=True, slots=True)
class PackedGraph:
    """Undirected graph in CSR form: offsets[n]:offsets[n+1] slices node n."""

    nodes: int
    offsets: list[int]
    neighbors: list[int]
    edge_indices: list[int]


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SensorError(
            "botdeflector pow/get params are not integers",
            context={"value": repr(value)[:80]},
        ) from exc


def seed_bytes(seed: str | None) -> bytes:
    """The browser feeds TextEncoder().encode(sessionId); that is UTF-8."""
    return str(seed).encode() if seed else b""


def edge_endpoints(edge: int, num_nodes: int, seed: bytes) -> tuple[int, int]:
    """Both endpoints of one edge come from the same 16 bytes of one digest."""
    digest = hashlib.sha256(edge.to_bytes(4, "little") + seed).digest()
    return (
        int.from_bytes(digest[0:8], "little") % num_nodes,
        int.from_bytes(digest[8:16], "little") % num_nodes,
    )


def build_graph(params: ChallengeParams) -> PackedGraph:
    """Deterministic CSR graph for one session; build it once per session."""
    num_nodes = params.nodes
    num_edges = params.edges
    seed = seed_bytes(params.graph_seed)

    ends = [edge_endpoints(edge, num_nodes, seed) for edge in range(num_edges)]
    degree = [0] * num_nodes
    for u, v in ends:
        degree[u] += 1
        degree[v] += 1

    offsets = [0] * (num_nodes + 1)
    for node in range(num_nodes):
        offsets[node + 1] = offsets[node] + degree[node]

    neighbors = [0] * (2 * num_edges)
    edge_indices = [0] * (2 * num_edges)
    cursor = offsets[:num_nodes]
    for edge, (u, v) in enumerate(ends):
        slot = cursor[u]
        cursor[u] = slot + 1
        neighbors[slot] = v
        edge_indices[slot] = edge
        slot = cursor[v]
        cursor[v] = slot + 1
        neighbors[slot] = u
        edge_indices[slot] = edge

    return PackedGraph(num_nodes, offsets, neighbors, edge_indices)


def compute_start_index(challenge_hex: str, num_nodes: int) -> int:
    """A challenge hash only picks where the scan starts; digest the raw bytes."""
    try:
        raw = bytes.fromhex(challenge_hex)
    except (TypeError, ValueError) as exc:
        raise SensorError(
            "botdeflector challenge is not hex",
            context={"challenge": str(challenge_hex)[:80]},
        ) from exc
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[0:8], "little") % num_nodes


def find_cycle(graph: PackedGraph, cycle_length: int, start_offset: int) -> list[int] | None:
    """Iterative DFS for the browser's first simple cycle of exactly cycle_length.

    The neighbour order falls out of the CSR build order, which is what the
    server recomputes. Skipping the parent edge and closing only at
    depth + 1 == cycle_length is the whole contract.
    """
    offsets, neighbors, edge_indices, nodes = graph.offsets, graph.neighbors, graph.edge_indices, graph.nodes
    on_path = bytearray(nodes)

    def dfs(start: int) -> list[int] | None:
        on_path[start] = 1
        edge_path: list[int] = []
        # frame: [node, parent, via_edge, depth, iter_pos, iter_end]
        stack = [[start, -1, -1, 0, offsets[start], offsets[start + 1]]]
        while stack:
            top = stack[-1]
            it_pos, it_end = top[4], top[5]
            if it_pos >= it_end:
                on_path[top[0]] = 0
                if top[2] != -1:
                    edge_path.pop()
                stack.pop()
                continue
            top[4] = it_pos + 1
            neighbor = neighbors[it_pos]
            edge = edge_indices[it_pos]
            if neighbor == top[1]:
                continue
            depth = top[3]
            if on_path[neighbor]:
                if neighbor == start and depth + 1 == cycle_length:
                    return [*edge_path, edge]
            elif depth + 1 < cycle_length:
                on_path[neighbor] = 1
                edge_path.append(edge)
                stack.append([neighbor, top[0], edge, depth + 1, offsets[neighbor], offsets[neighbor + 1]])
        return None

    for step in range(nodes):
        found = dfs((start_offset + step) % nodes)
        if found is not None:
            return found
    return None


def solve_challenges(
    challenges: list[str],
    params: ChallengeParams,
    graph: PackedGraph | None = None,
) -> list[dict[str, Any]]:
    """Solve every challenge of one step, in the /pow/verify body shape."""
    if not challenges:
        raise SensorError("botdeflector pow/get returned no challenges", context={})
    graph = graph or build_graph(params)
    solutions = []
    for challenge in challenges:
        cycle = find_cycle(graph, params.cycle_length, compute_start_index(str(challenge), graph.nodes))
        if cycle is None:
            raise SensorError(
                "botdeflector proof of work found no cycle; the graph params drifted",
                context={"challenge": str(challenge)[:80], "CYCLE_LENGTH": params.cycle_length},
            )
        solutions.append({"challenge": str(challenge), "edgeIndices": cycle})
    return solutions
