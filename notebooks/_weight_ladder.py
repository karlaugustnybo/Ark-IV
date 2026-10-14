"""Single source of truth for the priority-model weight ladder.

`ark_pipeline/builders/coarse_cache.py` owns `DEFAULT_WEIGHTS`; the marimo
notebooks inline SQL that must reproduce those numbers exactly. This module
is the tested bridge between the two: notebooks import the generated
SQL fragments from here, and `tests/test_weight_ladder.py` locks both this
module and the generated SQL to the pipeline's `DEFAULT_WEIGHTS` so the
ladders can be compared on fixtures, including NULL and contradictory flags.

The mapping used everywhere:
`redlist_category`
    Pipeline codes `{'cr': 4.0, 'en': 3.0, 'vu': 2.0, 'nt': 1.0, 'dd': 2.0,
    'lc': 0.1}`; the IUCN-side bookkeeping states (legacy ``Lower Risk/*``
    rows and ``Not Assessed`` non-ranks) match no serving predicate and get
    0.0.
DNA buckets, evaluated in this exact predicate order (first match wins).
A NULL GoaT state matches no production bucket and contributes zero, without
claiming that its evidence is sampled or absent:
    ``gdd``  4.0 - GoaT Data Deficient (evidence state unknown)
    ``fam``  4.0 - no family-level coverage
    ``gen``  3.0 - genus missed, family covered
    ``sp``   2.0 - species missed, genus and family covered
    ``samp`` 0.0 - remaining production bucket (zero weight; not a quality claim)
"""

THREAT_WEIGHTS = {
    "Critically Endangered": 4.0,
    "Endangered": 3.0,
    "Vulnerable": 2.0,
    "Near Threatened": 1.0,
    "Data Deficient": 2.0,
    "Least Concern": 0.1,
    "Lower Risk/*": 0.0,
    "Not Assessed": 0.0,
}

CATEGORY_KEYS = {
    "Critically Endangered": "cr",
    "Endangered": "en",
    "Vulnerable": "vu",
    "Near Threatened": "nt",
    "Data Deficient": "dd",
    "Least Concern": "lc",
}

DNA_WEIGHTS = {
    "gdd": 4.0,
    "fam": 4.0,
    "gen": 3.0,
    "sp": 2.0,
    "samp": 0.0,
}

DNA_PRECEDENCE = (
    "goat_data_deficient",
    "family_has_dna",
    "genus_has_dna",
    "has_dna_species_level",
)


def threat_weight_sql(category_col: str = "redlist_category") -> str:
    """SQL CASE reproducing DEFAULT_WEIGHTS threat weights, ELSE 0.0."""
    lines = [f"CASE {category_col}"]
    for category, weight in THREAT_WEIGHTS.items():
        if category in CATEGORY_KEYS:
            lines.append(f"  WHEN '{category}' THEN {weight}")
    lines.append("  ELSE 0.0")
    lines.append("END")
    return "\n".join(lines)


def dna_weight_sql(
    gdd_col: str = "goat_data_deficient",
    family_col: str = "family_has_dna",
    genus_col: str = "genus_has_dna",
    species_col: str = "has_dna_species_level",
) -> str:
    """SQL CASE reproducing DEFAULT_WEIGHTS DNA weights and predicate order."""
    return "\n".join(
        [
            "CASE",
            f"  WHEN {gdd_col} THEN {DNA_WEIGHTS['gdd']}",
            f"  WHEN {gdd_col} = false AND NOT {family_col} THEN {DNA_WEIGHTS['fam']}",
            f"  WHEN {gdd_col} = false AND {family_col} IS DISTINCT FROM false AND NOT {genus_col} THEN {DNA_WEIGHTS['gen']}",
            f"  WHEN {gdd_col} = false AND {family_col} IS DISTINCT FROM false AND {genus_col} IS DISTINCT FROM false AND NOT {species_col} THEN {DNA_WEIGHTS['sp']}",
            f"  ELSE {DNA_WEIGHTS['samp']}",
            "END",
        ]
    )
