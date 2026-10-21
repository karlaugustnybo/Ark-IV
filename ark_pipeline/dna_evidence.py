"""Versioned DNA evidence rules, independent of tree and candidate filters.

Taxon-level BUSCO and assembly summaries are an aggregate proxy, not evidence
that one assembly satisfies every criterion, and never EBP certification.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Literal, Mapping

RuleName = Literal['website', 'any-assembly', 'taxon-quality-proxy']
FIELDS = ('sample_acquired', 'in_progress', 'ebp_standard_criteria',
          'assembly_level', 'busco_completeness', 'resampling_required')


def evidence_table(rows: list[dict]):
    """Preserve every evidence/identity field when rows have missing keys.

    Arrow's first-row schema inference otherwise silently drops later fields,
    causing persisted and dynamic scenarios to disagree after reloading.
    """
    import pyarrow as pa

    columns = dict.fromkeys(key for row in rows for key in row)
    return pa.table({key: [row.get(key) for row in rows] for key in columns})


def present(value: object) -> bool:
    # Compatibility: even whitespace and false-like nonempty strings count.
    return isinstance(value, str) and value != ''


def numeric(value: object) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class DnaRule:
    name: RuleName = 'website'
    version: int = 1
    min_busco: float = 90
    assembly_levels: tuple[str, ...] = ('chromosome', 'complete genome')

    def __post_init__(self):
        if self.name not in ('website', 'any-assembly', 'taxon-quality-proxy') or self.version != 1:
            raise ValueError('Unsupported DNA rule/version')
        if not math.isfinite(self.min_busco) or not 0 <= self.min_busco <= 100:
            raise ValueError('BUSCO threshold must be in [0, 100]')
        if not self.assembly_levels or any(level not in ('contig', 'scaffold', 'chromosome', 'complete genome') for level in self.assembly_levels):
            raise ValueError('Unsupported assembly levels')
        if self.name != 'taxon-quality-proxy' and (self.min_busco != 90 or self.assembly_levels != ('chromosome', 'complete genome')):
            raise ValueError('Threshold parameters apply only to taxon-quality-proxy')

    def identity(self) -> dict:
        return {**asdict(self), 'assembly_levels': list(self.assembly_levels)}


@dataclass(frozen=True)
class EvidenceState:
    qualifies: bool | None
    state: str
    sample_reported: bool
    project_reported: bool
    criteria_reported: bool
    assembly_reported: bool
    resampling_reported: bool


def evaluate_evidence(row: Mapping[str, object], rule: DnaRule) -> EvidenceState:
    sample, project, criteria = (present(row.get(k)) for k in FIELDS[:3])
    assembly = row.get('assembly_level')
    level = assembly.strip().lower() if isinstance(assembly, str) else ''
    busco = numeric(row.get('busco_completeness'))
    if rule.name == 'website':
        # Deliberately no trim or upper BUSCO bound: matches the website exactly.
        try:
            legacy_busco = float(row.get('busco_completeness'))
        except (TypeError, ValueError):
            legacy_busco = None
        qualifies = sample or project or criteria or (
            isinstance(assembly, str) and assembly.lower() in ('chromosome', 'complete genome')
            and legacy_busco is not None and (math.isnan(legacy_busco) or legacy_busco >= 90))
        known = any(row.get(k) is not None for k in FIELDS[:5])
    elif rule.name == 'any-assembly':
        qualifies = bool(level)
        known = assembly is not None
    else:
        qualifies = level in rule.assembly_levels and busco is not None and rule.min_busco <= busco <= 100
        known = assembly is not None and (not level or busco is not None)
    result = True if qualifies else False if known else None
    return EvidenceState(result, 'qualifying' if result else 'no_qualifying_record' if result is False else 'unknown',
                         sample, project, criteria, bool(level), present(row.get('resampling_required')))


def website_sql() -> str:
    """The production SQL and scenario evaluator share this compatibility rule."""
    return """(nullif(sample_acquired, '') IS NOT NULL
        OR nullif(in_progress, '') IS NOT NULL
        OR nullif(ebp_standard_criteria, '') IS NOT NULL
        OR (lower(coalesce(assembly_level, '')) IN ('chromosome', 'complete genome')
            AND coalesce(try_cast(busco_completeness AS DOUBLE), 0) >= 90))"""


def lineage_representation(evidence: list[dict], targets: list[dict], rule: DnaRule) -> list[dict]:
    """Use independently validated rank IDs and full relative evidence.

    Callers supply current NCBI kingdom/family/genus IDs, never unreviewed name
    matches. A tree-only identity does not permit DNA transfer. Missing rank IDs
    produce unavailable representation, rather than an unrepresented lineage.
    """
    represented_family, represented_genus = set(), set()
    for row in evidence:
        if row.get('genomic_transfer_safe') is not True or evaluate_evidence(row, rule).qualifies is not True:
            continue
        kingdom, family, genus = (row.get(k) for k in ('kingdom_taxid', 'family_taxid', 'genus_taxid'))
        if kingdom and family:
            represented_family.add((kingdom, family))
            if genus:
                represented_genus.add((kingdom, family, genus))
    result = []
    for row in targets:
        kingdom, family, genus = (row.get(k) for k in ('kingdom_taxid', 'family_taxid', 'genus_taxid'))
        result.append({**row,
            'family_represented': (kingdom, family) in represented_family if kingdom and family else None,
            'genus_represented': (kingdom, family, genus) in represented_genus if kingdom and family and genus else None})
    return result
