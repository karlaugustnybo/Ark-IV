"""Compact stem-free trees and bounded, versioned server-side DNA scenarios."""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from heapq import nsmallest
from typing import Mapping

import numpy as np

from ark_pipeline.dna_evidence import DnaRule, evaluate_evidence
from ark_pipeline.runtime.provenance import identity_digest


@dataclass(frozen=True)
class CompactTree:
    """Preorder arrays: parent precedes child; root edge is explicitly zero."""

    parent: np.ndarray
    length: np.ndarray
    tip_nodes: np.ndarray
    tip_labels: tuple[str, ...]

    def __post_init__(self):
        if np.asarray(self.parent).dtype.kind not in 'iu' or np.asarray(self.tip_nodes).dtype.kind not in 'iu':
            raise ValueError('Tree node indexes must be integers')
        parent = np.array(self.parent, dtype=np.int64, copy=True)
        length = np.array(self.length, dtype=np.float64, copy=True)
        tips = np.array(self.tip_nodes, dtype=np.int64, copy=True)
        n = len(parent)
        if parent.ndim != 1 or length.shape != (n,) or not n or parent[0] != -1:
            raise ValueError('Invalid tree arrays/root')
        if np.any(parent[1:] < 0) or np.any(parent[1:] >= np.arange(1, n)):
            raise ValueError('Tree parents must precede their children')
        if length[0] != 0 or not np.isfinite(length).all() or np.any(length < 0):
            raise ValueError('Require finite nonnegative lengths and excluded crown stem')
        leaves = np.flatnonzero(np.bincount(parent[1:], minlength=n) == 0)
        if tips.ndim != 1 or len(tips) < 2 or len(tips) != len(self.tip_labels) or len(tips) != len(leaves) or set(tips) != set(leaves):
            raise ValueError('Tip indexes must identify all leaves exactly once')
        if len(set(self.tip_labels)) != len(tips) or any(not label for label in self.tip_labels):
            raise ValueError('Require unique nonempty tip labels')
        for name, value in (('parent', parent), ('length', length), ('tip_nodes', tips)):
            value.flags.writeable = False
            object.__setattr__(self, name, value)

    @classmethod
    def from_tree(cls, tree) -> CompactTree:
        nodes = list(tree.traverse('preorder'))
        indexes = {node: i for i, node in enumerate(nodes)}
        tips = [node for node in nodes if node.is_leaf]
        return cls(np.array([-1 if node is tree else indexes[node.up] for node in nodes]),
                   np.array([node.dist for node in nodes]),
                   np.array([indexes[node] for node in tips]), tuple(node.name for node in tips))

    def gaps(self, represented: set[str]) -> np.ndarray:
        """O(nodes + edges), no Pandas, distances or per-candidate traversal."""
        if not represented.issubset(self.tip_labels):
            raise ValueError('Represented baseline contains a tip outside tree')
        covered = np.zeros(len(self.parent), dtype=np.bool_)
        for label, node in zip(self.tip_labels, self.tip_nodes):
            covered[node] = label in represented
        for node in range(len(covered) - 1, 0, -1):
            covered[self.parent[node]] |= covered[node]
        gap = np.zeros(len(covered), dtype=np.float64)
        for node in range(1, len(gap)):
            if not covered[node]:
                gap[node] = gap[self.parent[node]] + self.length[node]
        if not np.isfinite(gap).all():
            raise ValueError('Tree path lengths overflow')
        return gap[self.tip_nodes]

    def nearest(self, represented: set[str]) -> list[tuple[float, str] | None]:
        """Optional linear two-pass distance diagnostic; lexical labels break ties."""
        if not represented.issubset(self.tip_labels):
            raise ValueError('Represented baseline contains a tip outside tree')
        children = [[] for _ in self.parent]
        for node in range(1, len(self.parent)):
            children[self.parent[node]].append(node)
        inside: list[tuple[float, str] | None] = [None] * len(self.parent)
        outside: list[tuple[float, str] | None] = [None] * len(self.parent)
        for label, node in zip(self.tip_labels, self.tip_nodes):
            if label in represented:
                inside[node] = (0., label)
        for node in range(len(self.parent) - 1, 0, -1):
            if inside[node] is not None:
                choice = (inside[node][0] + self.length[node], inside[node][1])
                parent = self.parent[node]
                if inside[parent] is None or choice < inside[parent]:
                    inside[parent] = choice
        for node, branch in enumerate(children):
            best = nsmallest(2, ((inside[c][0] + self.length[c], inside[c][1], c)
                                for c in branch if inside[c] is not None))
            for child in branch:
                choices = [outside[node]] if outside[node] is not None else []
                choices.extend((d, label) for d, label, c in best if c != child)
                other = min(choices, default=None)
                if other is not None:
                    outside[child] = (other[0] + self.length[child], other[1])
        return [min((v for v in (inside[node], outside[node]) if v is not None), default=None)
                for node in self.tip_nodes]

    def fair_proportion(self) -> np.ndarray:
        """ED allocates every branch equally among all descendant tips."""
        descendants = np.zeros(len(self.parent), dtype=np.int64)
        descendants[self.tip_nodes] = 1
        for node in range(len(self.parent) - 1, 0, -1):
            descendants[self.parent[node]] += descendants[node]
        ed = np.zeros(len(self.parent))
        for node in range(1, len(ed)):
            ed[node] = ed[self.parent[node]] + self.length[node] / descendants[node]
        return ed[self.tip_nodes]


class ScenarioEngine:
    """Read-only generation, full-relative baseline and a bounded completed LRU.

    Candidate/jurisdiction filtering happens after calculation. Tree identities
    and genomic transfer permissions are independent. The conservative review
    policy withholds priorities in lineages containing unresolved positive tips.
    """

    def __init__(self, tree: CompactTree, evidence: list[dict], species: list[dict],
                 generation: Mapping[str, object], max_scenarios: int = 8):
        if not 1 <= max_scenarios <= 64:
            raise ValueError('Scenario cache limit must be in [1,64]')
        required = {'tree', 'root', 'scope', 'units', 'stem', 'evidence', 'identities'}
        if not required.issubset(generation) or generation['units'] != 'Myr' or generation['stem'] != 'excluded':
            raise ValueError('Missing/incompatible scenario generation identity')
        safe_ids = [r['taxon_id'] for r in evidence if r.get('genomic_transfer_safe') is True]
        if len(set(safe_ids)) != len(safe_ids):
            raise ValueError('Duplicate safe evidence taxon IDs')
        if len({str(r['iucn_sis_id']) for r in species}) != len(species):
            raise ValueError('Duplicate species IDs')
        for rows, permission in ((evidence, 'genomic_transfer_safe'), (species, 'tree_identity_verified')):
            linked = [r['tree_tip'] for r in rows if r.get(permission) is True and r.get('tree_tip') is not None]
            if len(set(linked)) != len(linked):
                raise ValueError('Competing identities for a tree tip')
        self.tree, self.evidence, self.species = tree, tuple(dict(r) for r in evidence), tuple(dict(r) for r in species)
        self.generation, self.max_scenarios = dict(generation), max_scenarios
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()

    def calculate(self, rule: DnaRule, *, nearest: bool = False) -> dict:
        identity = {**self.generation, 'rule': rule.identity(), 'kernel': 'rooted-gap-v1',
                    'review_policy': 'withhold-unresolved-positive-genus-v1'}
        key = identity_digest(identity)
        cache_key = key + (":nearest" if nearest else ":gap")
        # One cold calculation per engine at a time bounds transient memory and
        # prevents concurrent identical requests from duplicating work.
        with self._lock:
            if cache_key in self._cache:
                self._cache.move_to_end(cache_key)
                return self._cache[cache_key]
            tips = set(self.tree.tip_labels)
            represented, missing_genera = set(), set()
            states_by_tip = {}
            unknown_lineage = False
            unresolved = unknown = 0
            for row in self.evidence:
                state = evaluate_evidence(row, rule)
                unknown += state.qualifies is None
                if row.get('genomic_transfer_safe') is True and row.get('tree_tip') in tips:
                    states_by_tip[row['tree_tip']] = state
                if state.qualifies is not True:
                    continue
                if row.get('genomic_transfer_safe') is True and row.get('tree_tip') in tips:
                    represented.add(row['tree_tip'])
                else:
                    unresolved += 1
                    genus = row.get('genus_taxid') or row.get('reviewed_genus_key')
                    if genus:
                        missing_genera.add(genus)
                    else:
                        unknown_lineage = True
            gaps = self.tree.gaps(represented)
            distances = self.tree.nearest(represented) if nearest else [None] * len(tips)
            values = dict(zip(self.tree.tip_labels, zip(gaps, distances)))
            rows = []
            for species in self.species:
                tip = species.get('tree_tip')
                reason = None
                if species.get('tree_identity_verified') is not True or tip not in values:
                    reason = 'unavailable_tree_identity'
                elif species.get('genomic_transfer_safe') is not True:
                    reason = 'unsafe_genomic_identity'
                incomplete = (species.get('genus_taxid') or species.get('reviewed_genus_key')) in missing_genera
                gap, distance = values[tip] if reason is None else (None, None)
                focal = states_by_tip.get(tip) if reason is None else None
                rows.append({'iucn_sis_id': str(species['iucn_sis_id']), 'tree_tip': tip,
                    'gap_myr': float(gap) if gap is not None else None,
                    'represented': tip in represented if reason is None else None,
                    'evidence_state': focal.state if focal else 'unavailable_focal_evidence' if reason is None else 'unsafe_identity',
                    'qualifying_evidence': focal.qualifies if focal else None,
                    **{name: getattr(focal, name) if focal else None for name in (
                        'sample_reported', 'project_reported', 'criteria_reported', 'assembly_reported', 'resampling_reported')},
                    'baseline_incomplete': incomplete,
                    'availability_reason': reason or ('baseline_incomplete' if incomplete and tip not in represented else None),
                    'nearest_tip': distance[1] if distance else None,
                    'nearest_distance_myr': float(distance[0]) if distance else None})
            result = {'scenario_id': key, 'identity': identity, 'rows': rows,
                      'represented_tips': len(represented), 'tree_tips': len(tips),
                      'unresolved_positive_records': unresolved, 'unknown_evidence_records': unknown, 'unresolved_without_genus': unknown_lineage}
            self._cache[cache_key] = result
            if len(self._cache) > self.max_scenarios:
                self._cache.popitem(last=False)
            return result

    def rank(self, rule: DnaRule, weights: Mapping[str, float], *, species_ids: set[str] | None = None,
             nearest: bool = False) -> dict:
        if any(not math.isfinite(v) or v < 0 for v in weights.values()):
            raise ValueError('Threat weights must be finite and nonnegative')
        scenario = self.calculate(rule, nearest=nearest)
        categories = {str(r['iucn_sis_id']): r.get('redlist_category') for r in self.species}
        rows = []
        for row in scenario['rows']:
            if species_ids is not None and row['iucn_sis_id'] not in species_ids:
                continue
            weight = weights.get(categories[row['iucn_sis_id']])
            reason = row['availability_reason'] or ('extinct' if categories[row['iucn_sis_id']] == 'EX' else 'unavailable_threat_weight' if weight is None else None)
            priority = row['gap_myr'] * weight if reason is None else None
            if priority is not None and not math.isfinite(priority):
                raise ValueError('Weighted priority overflows')
            rows.append({**row, 'priority': priority,
                         'availability_reason': reason})
        rows.sort(key=lambda row: (row['priority'] is None, -(row['priority'] or 0), row['iucn_sis_id']))
        return {**scenario, 'weighted_scenario_id': identity_digest({'scenario': scenario['scenario_id'], 'weights': dict(weights)}),
                'rows': rows}
