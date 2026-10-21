"""Release-Newick arrays and bounded complete-tree descriptive EDGE arithmetic."""

from __future__ import annotations

import csv
import re
from array import array
from collections import Counter
from pathlib import Path

import numpy as np

from ark_pipeline.edge import original_edge
from ark_pipeline.phylogenetic_scenarios import CompactTree


def read_complete_tree(path: Path) -> tuple[CompactTree, dict]:
    """Keep every released occurrence; ambiguous labels cannot seed identities.

    The release is unquoted Newick with explicit non-root branch lengths.
    Occurrence suffixes distinguish repeated labels without changing topology
    or lengths. Identity inputs continue to reject every repeated source label.
    """
    text = path.read_text().strip()
    if not text.startswith('(') or not text.endswith(';') or text.count(';') != 1 or any(c in text for c in "'\"[]"):
        raise ValueError('Require one unquoted release-style Newick tree')
    parents, lengths, tips = array('q'), array('d'), array('q')
    labels, stack = [], []
    pending = None
    expect_child = True
    root_label = ''
    for match in re.finditer(r'[(),;]|[^(),;]+', text):
        token = match[0].strip()
        if not token:
            continue
        if token == '(':
            if not expect_child:
                raise ValueError('Unexpected subtree')
            node = len(parents)
            parents.append(stack[-1] if stack else -1)
            lengths.append(float('nan'))
            stack.append(node)
            pending = None
        elif token == ')':
            if expect_child or not stack:
                raise ValueError('Empty or unbalanced subtree')
            pending = stack.pop()
            expect_child = False
        elif token == ',':
            if expect_child or not stack:
                raise ValueError('Unexpected Newick comma')
            if pending is not None and pending != 0 and np.isnan(lengths[pending]):
                raise ValueError('Missing internal branch length')
            pending, expect_child = None, True
        elif token == ';':
            if stack or expect_child:
                raise ValueError('Incomplete Newick tree')
        else:
            name, separator, distance = token.rpartition(':')
            if not separator:
                name, distance = token, '0' if pending == 0 else 'nan'
            length = float(distance)
            if not np.isfinite(length) or length < 0:
                raise ValueError('Require finite nonnegative branch lengths')
            if expect_child:
                if not stack or not re.fullmatch(r'[^\s:;(),]+_ott\d+', name):
                    raise ValueError('Malformed release tip')
                tips.append(len(parents))
                parents.append(stack[-1])
                lengths.append(length)
                labels.append(name)
                expect_child = False
            elif pending is not None:
                lengths[pending] = length
                if pending == 0:
                    root_label = name
                pending = None
            else:
                raise ValueError('Unexpected Newick label')
    if not parents:
        raise ValueError('Empty tree')
    # The source root's incoming stem is outside the selected rooted scope.
    lengths[0] = 0.
    counts = Counter(labels)
    duplicate_labels = {label for label, count in counts.items() if count > 1}
    occurrences = Counter()
    for index, label in enumerate(labels):
        if label in duplicate_labels:
            occurrences[label] += 1
            labels[index] = f'{label}:occurrence:{occurrences[label]}'
    tree = CompactTree(np.asarray(parents), np.asarray(lengths), np.asarray(tips), tuple(labels))
    return tree, {'root_label': root_label or 'released-global-root',
                  'source_tips': len(labels), 'nodes': len(parents),
                  'repeated_labels': len(duplicate_labels),
                  'ambiguous_occurrences': sum(counts[label] for label in duplicate_labels),
                  'stem': 'excluded', 'units': 'Myr'}


class TreePasses:
    """Depth groups let NumPy traverse all nodes without recursive objects."""

    def __init__(self, tree: CompactTree):
        self.tree = tree
        depth = np.zeros(len(tree.parent), dtype=np.int32)
        for node in range(1, len(depth)):
            depth[node] = depth[tree.parent[node]] + 1
        order = np.argsort(depth, kind='stable')
        self.levels = np.split(order, np.flatnonzero(np.diff(depth[order])) + 1)

    def counts(self, retained: np.ndarray) -> np.ndarray:
        counts = np.zeros(len(self.tree.parent), dtype=np.int64)
        counts[self.tree.tip_nodes] = retained
        for level in self.levels[:0:-1]:
            np.add.at(counts, self.tree.parent[level], counts[level])
        return counts

    def fair_proportion(self, counts: np.ndarray) -> np.ndarray:
        values = np.zeros(len(counts))
        for level in self.levels[1:]:
            contribution = np.divide(self.tree.length[level], counts[level],
                                     out=np.zeros(len(level)), where=counts[level] > 0)
            values[level] = values[self.tree.parent[level]] + contribution
        return values[self.tree.tip_nodes]

    def edge2(self, risks: np.ndarray, retained: np.ndarray) -> np.ndarray:
        tree = self.tree
        products = np.ones(len(tree.parent))
        products[tree.tip_nodes] = np.where(retained, risks, 1.)
        for level in self.levels[:0:-1]:
            np.multiply.at(products, tree.parent[level], products[level])
        values = np.zeros(len(tree.parent))
        for level in self.levels[1:]:
            values[level] = values[tree.parent[level]] + tree.length[level] * products[level]
        # Excluded leaves are never selected by callers. Their neutral products
        # remove them from risk arithmetic without altering retained path lengths.
        return values[tree.tip_nodes]


def score_complete_tree(tree: CompactTree, species: list[dict], samples: int, seed: int) -> tuple[list[dict], dict]:
    """Same pinned risk model as score_clade; keep draws only for assessed tips.

    Unknown/unassessed relatives remain in the complete extant-input crown with
    the engine's pooled risk distribution. Only verified IUCN EX removes a tip.
    All components describe this one released tree, with risk-only uncertainty.
    """
    from dated_complete_tree.tree_fixing_utils import open_config_csv

    if samples < 2:
        raise ValueError('Use at least two risk samples')
    by_tip = {row['tree_tip']: row for row in species if row.get('tree_identity_verified') is True}
    retained = np.array([by_tip.get(label, {}).get('redlist_category') != 'EX' for label in tree.tip_labels])
    if retained.sum() < 2:
        raise ValueError('Fewer than two extant tips')
    passes = TreePasses(tree)
    counts = passes.counts(retained)
    ed = passes.fair_proportion(counts)
    pd = float(tree.length[counts > 0].sum())
    if not np.isclose(ed[retained].sum(), pd, rtol=1e-10):
        raise ValueError('ED does not reconcile with retained phylogenetic diversity')
    with open_config_csv(None, 'p_extinction.csv') as stream:
        raw = list(csv.reader(stream))[1:]
    pooled = np.array([float(row[1]) for row in raw])
    if not np.isfinite(pooled).all() or np.any(pooled <= 0) or np.any(pooled > 1):
        raise ValueError('Risk model must contain finite probabilities in (0,1]')
    lookup = {category: np.array([float(row[1]) for row in raw if row[0] == category]) for category in {row[0] for row in raw}}
    categories = np.array([by_tip.get(label, {}).get('redlist_category') or 'NE' for label in tree.tip_labels])
    groups = [(np.flatnonzero(retained & (categories == category)), lookup.get(category, pooled)) for category in np.unique(categories)]
    selected = np.array([i for i, label in enumerate(tree.tip_labels) if retained[i] and label in by_tip], dtype=np.int64)
    # Exactly the original stable-tip RNG assignment, excluding EX tips.
    stable = np.array(sorted(np.flatnonzero(retained), key=lambda i: tree.tip_labels[i]), dtype=np.int64)
    rng = np.random.default_rng(seed)

    def calculate(randomise):
        risks = np.ones(len(retained))
        uniforms = np.empty(len(retained)) if randomise else None
        if randomise:
            uniforms[stable] = rng.random(len(stable))
        for indexes, values in groups:
            risks[indexes] = values[(uniforms[indexes] * len(values)).astype(np.int64)] if randomise else np.median(values)
        risks[retained & (categories == 'EW')] = .97
        result = passes.edge2(risks, retained)[selected]
        if not np.isfinite(result).all() or np.any(result < 0):
            raise ValueError('Invalid complete-tree EDGE2 values')
        return result

    point = calculate(False)
    draws = np.empty((samples, len(selected)))
    for index in range(samples):
        draws[index] = calculate(True)
    quantiles = np.quantile(draws, [.025, .5, .975], axis=0)
    output = []
    for column, index in enumerate(selected):
        label = tree.tip_labels[index]
        output.append({'tree_tip': label, 'ark_ed_myr': float(ed[index]),
            'ark_edge1': original_edge(float(ed[index]), categories[index]),
            'ark_edge2_myr_point': float(point[column]),
            'ark_edge2_myr_p025': float(quantiles[0, column]),
            'ark_edge2_myr_median': float(quantiles[1, column]),
            'ark_edge2_myr_p975': float(quantiles[2, column])})
    return output, {'method': 'compact-complete-tree-risk-only-v1',
        'original_tree_tips': len(retained), 'retained_tree_tips': int(retained.sum()),
        'excluded_extinct_tips': int((~retained).sum()), 'assessed_scored_tips': len(selected),
        'sum_ed_myr': float(ed[retained].sum()), 'clade_pd_myr': pd,
        'risk_draw_order': 'sorted_tip_name', 'risk_samples': samples,
        'unassessed_or_unjoined_tree_tips': int(retained.sum()) - len(selected),
        'extinction_status_policy': 'verified_iucn_overrides_ott_extinction_flags'}
