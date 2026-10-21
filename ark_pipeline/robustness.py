"""Offline common-cohort rank and hotspot comparisons with deterministic ties."""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd


def compare_scenarios(reference: Mapping[str, float | None], alternative: Mapping[str, float | None]) -> dict:
    def finite(value):
        return value is not None and math.isfinite(value)
    cohort = sorted(k for k in reference.keys() & alternative.keys()
                    if finite(reference[k]) and finite(alternative[k]))
    n = len(cohort)
    ranks = [pd.Series([values[k] for k in cohort]).rank(method='average') for values in (reference, alternative)]
    correlation = float(np.corrcoef(*ranks)[0, 1]) if n > 1 and all(r.nunique() > 1 for r in ranks) else None
    ordered = [sorted(cohort, key=lambda key: (-values[key], key)) for values in (reference, alternative)]
    overlap = {}
    for pct in (1, 5, 10):
        size = math.ceil(n * pct / 100)
        common = len(set(ordered[0][:size]) & set(ordered[1][:size]))
        overlap[str(pct)] = {'selected': size, 'common': common, 'fraction': common / size if size else None}
    return {'common_cohort': n, 'reference_population': len(reference), 'alternative_population': len(alternative),
            'excluded_reference': len(reference) - n, 'excluded_alternative': len(alternative) - n,
            'spearman_rank_correlation': correlation, 'top_percent_overlap': overlap,
            'rank_ties': 'average', 'hotspot_ties': 'score descending, stable identifier ascending'}
