"""Reads lineage/lineage.json and answers 'what is upstream / downstream of X?'."""
import json
from collections import deque
from functools import lru_cache

from common.config import ROOT

SILVER_NODE = {"customer_kyc": "silver_customer"}


@lru_cache(maxsize=1)
def load():
    data = json.loads((ROOT / "lineage" / "lineage.json").read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in data["nodes"]}
    down, up = {}, {}
    for a, b in data["edges"]:
        down.setdefault(a, []).append(b)
        up.setdefault(b, []).append(a)
    return nodes, down, up


def nodes():
    return load()[0]


def edges():
    _, down, _ = load()
    return [(a, b) for a, bs in down.items() for b in bs]


def _walk(start, graph):
    seen, queue = [], deque([start])
    while queue:
        node = queue.popleft()
        for nxt in graph.get(node, []):
            if nxt not in seen:
                seen.append(nxt)
                queue.append(nxt)
    return seen


def downstream(node):
    return _walk(node, load()[1])


def upstream(node):
    return _walk(node, load()[2])


def source_node(source):
    return f"src_{source}"


def bronze_node(source):
    return f"bronze_{source}"


def silver_node(source):
    return SILVER_NODE.get(source, f"silver_{source}")


def gold_nodes():
    return [n for n, d in nodes().items() if d["layer"] == "gold"]
