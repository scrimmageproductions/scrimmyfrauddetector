"""Officer / entity / address graph (networkx) and cluster detection.

Tripartite graph:

    (person: normalized name) --[officer / authorized official / registered agent]-- (entity) --[address of record]-- (address)

"Unrelated" means not in the same *affiliation group*. Entities are affiliated when
they share a UEI or parent UEI, or have near-identical names. Co-location and shared
officers among affiliates usually have a lawful explanation, so they are never
counted as anomalies.

Commercial registered-agent services (which legitimately represent thousands of
companies) are excluded from person nodes.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

import networkx as nx

from .models import AwardeeEntity, NetworkSummary
from .normalize import names_related

COMMERCIAL_AGENT_RE = re.compile(
    r"\b(REGISTERED\s+AGENT|CORPORATION\s+SERVICE|CT\s+CORPORATION|C\s*T\s+CORP|NORTHWEST\s+REGISTERED|INCORP\s+SERVICES|"
    r"LEGALZOOM|NATIONAL\s+REGISTERED|COGENCY|UNITED\s+STATES\s+CORPORATION|ZENBUSINESS|HARBOR\s+COMPLIANCE|"
    r"BUSINESS\s+FILINGS|REGISTERED\s+AGENTS?\s+INC|CSC\b|INCORPORATING\s+SERVICES)\b",
    re.I,
)


def _e(key: str) -> tuple[str, str]:
    return ("entity", key)


def _p(name: str) -> tuple[str, str]:
    return ("person", name)


def _a(key: str) -> tuple[str, str]:
    return ("address", key)


@dataclass
class EntityGraph:
    graph: nx.Graph = field(default_factory=nx.Graph)
    entities: dict[str, AwardeeEntity] = field(default_factory=dict)
    affiliation_threshold: float = 88.0
    _group: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls, entities: list[AwardeeEntity], affiliation_threshold: float = 88.0) -> EntityGraph:
        g = cls(entities={e.entity_key: e for e in entities}, affiliation_threshold=affiliation_threshold)
        for e in entities:
            g.graph.add_node(_e(e.entity_key), kind="entity", label=e.name, total=e.total_awarded)
            if e.address.location_key:
                g.graph.add_node(_a(e.address.location_key), kind="address", label=e.address.one_line,
                                 locality=e.address.locality_key, po_box=e.address.is_po_box)
                g.graph.add_edge(_e(e.entity_key), _a(e.address.location_key), role="address_of_record")
            for off in e.officers:
                if len(off.normalized_name.split()) < 2 or COMMERCIAL_AGENT_RE.search(off.name):
                    continue
                pn = _p(off.normalized_name)
                g.graph.add_node(pn, kind="person", label=off.name)
                existing = g.graph.get_edge_data(_e(e.entity_key), pn)
                roles = set(existing["roles"]) if existing else set()
                g.graph.add_edge(_e(e.entity_key), pn, roles=sorted(roles | {off.role_source}), listed_as=off.name,
                                 source_record_ids=list(off.source_record_ids))
        g._build_affiliations()
        return g

    # -- affiliation ------------------------------------------------------- #

    def _build_affiliations(self) -> None:
        aff = nx.Graph()
        ents = list(self.entities.values())
        aff.add_nodes_from(e.entity_key for e in ents)
        by_anchor: dict[str, list[str]] = defaultdict(list)
        for e in ents:
            for anchor in {e.uei, e.parent_uei} - {None, ""}:
                by_anchor[anchor].append(e.entity_key)  # type: ignore[index]
        for members in by_anchor.values():
            aff.add_edges_from((members[0], m) for m in members[1:])
        for i, a in enumerate(ents):
            for b in ents[i + 1 :]:
                if names_related(a.normalized_name, b.normalized_name, self.affiliation_threshold):
                    aff.add_edge(a.entity_key, b.entity_key)
        for comp in nx.connected_components(aff):
            root = min(comp)
            for k in comp:
                self._group[k] = root

    def affiliation_group(self, entity_key: str) -> str:
        return self._group.get(entity_key, entity_key)

    # -- queries used by the scorer ---------------------------------------- #

    def colocated(self, entity_key: str) -> tuple[list[str], int]:
        """(all entity keys at this entity's exact address, number of unrelated affiliation groups among them)."""
        e = self.entities[entity_key]
        if not e.address.location_key:
            return [entity_key], 1
        members = sorted(k for _, k in (n for n in self.graph.neighbors(_a(e.address.location_key)) if n[0] == "entity"))
        return members, len({self.affiliation_group(k) for k in members})

    def shared_people(self, entity_key: str, same_locality: bool = True) -> dict[str, list[str]]:
        """person normalized name -> unrelated entity keys (same locality) that also list that person."""
        e = self.entities[entity_key]
        own = self.affiliation_group(entity_key)
        out: dict[str, list[str]] = {}
        for node in self.graph.neighbors(_e(entity_key)):
            if node[0] != "person":
                continue
            others = []
            for other in self.graph.neighbors(node):
                if other[0] != "entity" or other[1] == entity_key:
                    continue
                ok = self.entities[other[1]]
                if self.affiliation_group(ok.entity_key) == own:
                    continue
                if same_locality and e.address.locality_key and ok.address.locality_key != e.address.locality_key:
                    continue
                others.append(ok.entity_key)
            if others:
                out[node[1]] = sorted(others)
        return out

    def edge_sources(self, entity_key: str, person: str) -> list[str]:
        data = self.graph.get_edge_data(_e(entity_key), _p(person)) or {}
        return list(data.get("source_record_ids") or [])

    def listed_as(self, entity_key: str, person: str) -> str | None:
        return (self.graph.get_edge_data(_e(entity_key), _p(person)) or {}).get("listed_as")

    # -- summaries --------------------------------------------------------- #

    def summary(self, entity_key: str, max_nodes: int = 60) -> NetworkSummary:
        comp = nx.node_connected_component(self.graph, _e(entity_key))
        sub = self.graph.subgraph(list(comp)[:max_nodes] if len(comp) > max_nodes else comp)
        ent_keys = sorted(n[1] for n in sub if n[0] == "entity")
        shared_addr = []
        for n in sub:
            if n[0] == "address":
                ents = [x[1] for x in sub.neighbors(n) if x[0] == "entity"]
                if len(ents) > 1:
                    shared_addr.append({"address": sub.nodes[n]["label"], "location_key": n[1],
                                        "entities": [self.entities[k].name for k in sorted(ents)]})
        shared_ppl = []
        for n in sub:
            if n[0] == "person":
                ents = [x[1] for x in sub.neighbors(n) if x[0] == "entity"]
                if len(ents) > 1:
                    shared_ppl.append({"person": sub.nodes[n]["label"], "normalized": n[1],
                                       "entities": [self.entities[k].name for k in sorted(ents)]})
        return NetworkSummary(
            component_entities=ent_keys, shared_addresses=shared_addr, shared_people=shared_ppl,
            component_total_awarded=round(sum(self.entities[k].total_awarded for k in ent_keys), 2),
            dot=self.to_dot(sub, focus=entity_key),
        )

    @staticmethod
    def _q(*lines: str) -> str:
        """DOT-quote one or more label lines (joined with DOT's \\n line break)."""
        esc = [str(t).replace("\\", "\\\\").replace('"', '\\"') for t in lines]
        return '"' + "\\n".join(esc) + '"'

    def to_dot(self, sub: nx.Graph | None = None, focus: str | None = None) -> str:
        sub = sub if sub is not None else self.graph
        ids = {n: f"n{i}" for i, n in enumerate(sorted(sub.nodes, key=str))}
        lines = ["graph scrimmy {", '  graph [rankdir=LR, bgcolor="transparent", fontname="Helvetica"];',
                 '  node [fontname="Helvetica", fontsize=10, style="rounded,filled", color="#C7C7CC", fontcolor="#1C1C1E"];',
                 '  edge [color="#AEAEB2"];']
        for n, nid in ids.items():
            kind, attrs = n[0], sub.nodes[n]
            if kind == "entity":
                fill = "#FF3B30" if n[1] == focus else "#E5F0FF"
                label = self._q(attrs["label"], f"${attrs.get('total', 0):,.0f}")
                lines.append(f'  {nid} [shape=box, fillcolor="{fill}", fontcolor="{"#FFFFFF" if n[1] == focus else "#1C1C1E"}", label={label}];')
            elif kind == "person":
                lines.append(f'  {nid} [shape=ellipse, fillcolor="#FFF4E5", label={self._q(attrs["label"])}];')
            else:
                lines.append(f'  {nid} [shape=note, fillcolor="#F2F2F7", label={self._q(attrs["label"])}];')
        for u, v in sub.edges:
            lines.append(f"  {ids[u]} -- {ids[v]};")
        lines.append("}")
        return "\n".join(lines)
