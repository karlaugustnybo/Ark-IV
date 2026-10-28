# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "marimo>=0.19",
#     "altair>=6,<7",
#     "duckdb>=1.5,<2",
#     "pyarrow>=21",
#     "polars>=1.30",
#     "h3>=4.4,<5",
#     "numpy>=2.2",
#     "scipy>=1.15",
# ]
# ///

import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")


@app.cell
def _():
    import hashlib
    import itertools
    import json
    import math
    import os
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import altair as alt
    import duckdb
    import h3
    import marimo as mo
    import numpy as np
    import polars as pl
    import scipy.sparse as sparse
    from _weight_ladder import DNA_WEIGHTS, THREAT_WEIGHTS

    alt.data_transformers.disable_max_rows()
    return (
        DNA_WEIGHTS,
        THREAT_WEIGHTS,
        alt,
        duckdb,
        h3,
        hashlib,
        itertools,
        json,
        math,
        mo,
        np,
        os,
        pl,
        sparse,
    )


@app.cell
def _(duckdb, hashlib, json, os):
    # One pipeline run: its res-3 serving exports plus the phylogenetic generation
    # built from the same assessments. Both paths are explicit; nothing is acquired.
    _exports = os.environ.get("ARK_NOTEBOOK_EXPORTS")
    _phylogenetic = os.environ.get("ARK_NOTEBOOK_PHYLOGENETIC")
    if not _exports or not _phylogenetic:
        raise ValueError(
            "Set ARK_NOTEBOOK_EXPORTS (res-3 exports directory) and "
            "ARK_NOTEBOOK_PHYLOGENETIC (analysis/phylogenetic/generations/<id>) for one run"
        )
    _required = {
        f"{_exports}/species.parquet": {
            "gbif_accepted_id",
            "iucn_sis_id",
            "redlist_category",
            "goat_data_deficient",
            "family_has_dna",
            "genus_has_dna",
            "has_dna_species_level",
            "iucn_assessment_date",
        },
        f"{_exports}/cell_species_lists.parquet": {"h3_index", "resolution", "species_ids"},
        f"{_exports}/cell_boundaries.parquet": {"h3_index", "resolution", "admin0"},
        f"{_phylogenetic}/species.parquet": {"iucn_sis_id", "genomic_transfer_safe"},
        f"{_phylogenetic}/species_phylogenetic.parquet": {
            "iucn_sis_id",
            "tree_tip",
            "gap_myr",
            "availability_reason",
            "edge2_myr_point",
            "generation_id",
        },
        f"{_phylogenetic}/tree.parquet": {"parent", "length", "tip_label"},
        f"{_phylogenetic}/evidence.parquet": {
            "tree_tip",
            "genomic_transfer_safe",
            "sample_acquired",
            "in_progress",
            "ebp_standard_criteria",
            "assembly_level",
            "busco_completeness",
        },
    }
    _identity = hashlib.sha256()
    for _path, _columns in _required.items():
        _relation = duckdb.read_parquet(_path)
        if _missing := _columns - set(_relation.columns):
            raise ValueError(f"{_path} lacks columns: {', '.join(sorted(_missing))}")
        if (
            "resolution" in _columns
            and _relation.filter("resolution IS DISTINCT FROM 3").count("*").fetchone()[0]
        ):
            raise ValueError(f"{_path} must contain resolution-3 rows only")
        with open(_path, "rb") as _stream:
            _identity.update(_stream.read())
    SPECIES_P, LISTS_P, BOUND_P, TRANSFER_P, PHYLO_P, TREE_P, EVIDENCE_P = (
        p.replace("'", "''") for p in _required
    )
    # The tree recomputation below re-applies the generation's DNA rule.
    with open(f"{_phylogenetic}/receipt.json") as _stream:
        _rule = json.load(_stream)["identity"]["default_rule"]
    if (_rule["name"], _rule["version"]) != ("website", 1):
        raise ValueError(f"Unsupported phylogenetic DNA rule: {_rule}")
    INPUT_ID = _identity.hexdigest()
    PHYLO_GENERATION = (
        duckdb.read_parquet(f"{_phylogenetic}/species_phylogenetic.parquet")
        .project("generation_id")
        .distinct()
        .fetchall()
    )
    if len(PHYLO_GENERATION) != 1:
        raise ValueError("Phylogenetic species table must come from exactly one generation")
    PHYLO_GENERATION = PHYLO_GENERATION[0][0]
    return (
        BOUND_P,
        EVIDENCE_P,
        INPUT_ID,
        LISTS_P,
        PHYLO_GENERATION,
        PHYLO_P,
        SPECIES_P,
        TRANSFER_P,
        TREE_P,
    )


@app.cell
def _(INPUT_ID, PHYLO_GENERATION, mo):
    mo.md(f"""
    # Hotspot robustness diagnostic

    **Question** *(roadmap §6, P0)*: which of today's top priority cells survive
    every reasonable alternative specification of the *existing* model, and which
    depend on one particular choice?

    `score_surfaces_investigation.py` §3 already moves one choice at a time away
    from the baseline. That cannot show interactions: a cell can survive each
    single change and still fall out when two changes are combined. This notebook
    scores the **full factorial grid** of the alternatives the res-3 exports can
    express, and serves the roadmap's proposed surface

    `robustness(c)` = share of specifications in which cell `c` is in the top 5%.

    The baseline reproduces `DEFAULT_WEIGHTS` through `_weight_ladder.py`.
    Everything is a resolution-3 preview: any-touch res-3 membership is neither
    occupancy nor the canonical res-7 surface. Input identity
    `{INPUT_ID[:16]}…` (SHA-256 of the consumed files, not a provider
    release date); phylogenetic generation `{PHYLO_GENERATION[:16]}…`. Nothing is
    written or served.
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Specification grid

    | Factor | Levels (baseline first) | What it tests |
    | --- | --- | --- |
    | IUCN Data Deficient threat weight | **2**, 0, 1, 4 | how much DD species drive hotspots |
    | DNA measure | **default** bucket (fam 4 · gen 3 · sp 2), flat (all gaps 4), exact-species (sp 4 · gen 2 · fam 1); gap scored only, gap upper bound, fair-share upper bound (Myr) | lineage buckets versus unsequenced branch length, and what missing evidence and shared branches do to the gap |
    | Range rarity | **off**, on (`w_s / N_s`) | widespread versus restricted species |
    | Taxonomic transfer | **all registered**, safe transfer only, matched only | how much the score rests on unsafe or absent matches |
    | Assessment window | **all**, 2015+ only | stale assessments |

    4 × 6 × 2 × 3 × 2 = **288 specifications**, each weighted equally. Equal
    weighting is itself a choice: this grid is a set of plausible alternatives, not
    a probability distribution over models.

    **Phylogenetic gap.** The run's opportunity score is `threat weight × gap_myr`:
    the branch length (Myr) of the dated complete tree that the focal species
    would add to the set of species with qualifying DNA evidence (website rule:
    sample, project or EBP-criteria evidence, or a chromosome/complete assembly
    with BUSCO ≥ 90). The three gap levels differ in two choices:

    - *Gap, scored only* is the pipeline's score. Species with an
      `availability_reason` (unsafe genomic identity, no tree identity,
      baseline-incomplete) are unscored and add nothing. Under the bucket ladders
      the same species mostly sit in the maximal `gdd` bucket, so the two measure
      families treat missing evidence in opposite ways.
    - *Gap, upper bound* assumes every species with a tree tip is unsequenced
      unless it is in the represented set, and keeps the baseline-incomplete
      upper bound. It is the gap if all uncertain evidence turns out absent.
      Species without a tree identity still add nothing.
    - *Fair-share upper bound* splits each uncovered branch equally among the
      unsequenced assessed species below it. A marginal gap credits a shared deep
      branch to every species under it, so cell sums count it repeatedly; the
      shares sum exactly to the uncovered branch length. Marginal gaps answer
      "what does sequencing this one species add"; shares answer "how much of
      the remaining gap is this species responsible for", which is what a cell
      sum needs.

    The bucket ladders use the taxonomy factor below. The gap measures carry their
    own identity rule (scored only = safe matches; upper bounds = every tree
    tip), so the three taxonomy levels coincide for them.

    **Taxonomic transfer levels.** `goat_data_deficient` means "no checked GoaT/NCBI
    identity", not a GoaT verdict; it receives the maximum bucket weight. *Safe
    transfer only* uses the run's own `genomic_transfer_safe` decision: species with
    transferred DNA flags but an unsafe identity are treated as unknown evidence
    (`gdd`). *Matched only* removes every unknown or unsafe species from the score
    instead of treating missing evidence as a maximal gap.

    **Not testable from these files.** Polygon-only evidence (the exports keep no
    evidence type), alternative rasterization rules and unsimplified geometry (need
    res-7 geometry; the spatial-test inputs are archived off this machine), and
    per-class scoring (a decomposition rather than an alternative total; see
    `score_surfaces_investigation.py`). The roadmap's other P0 item, the
    rasterization sensitivity test, covers the geometry choices.
    """)
    return


@app.cell
def _(EVIDENCE_P, PHYLO_P, TREE_P, duckdb, np, pl):
    # Upper-bound gaps from the run's own tree. Tree rows are nodes; `parent` is a
    # row index and precedes its children, so ascending row order is a preorder.
    _con = duckdb.connect()
    _tree = _con.sql(f"SELECT parent, length FROM '{TREE_P}'").fetchnumpy()
    _parent = _tree["parent"].astype(np.int64)
    _length = _tree["length"].astype(np.float64)
    _n = len(_parent)
    _con.sql(f"""
        CREATE TEMP TABLE tips AS
        SELECT file_row_number AS node, tip_label AS tree_tip
        FROM read_parquet('{TREE_P}', file_row_number = true)
        WHERE tip_label IS NOT NULL AND tip_label <> ''
    """)
    # Represented baseline: safe identities meeting the website rule
    # (`dna_evidence.website_sql`), as in `phylogenetic_scenarios.calculate`.
    _represented = _con.sql(f"""
        SELECT DISTINCT t.node FROM '{EVIDENCE_P}' e JOIN tips t USING (tree_tip)
        WHERE e.genomic_transfer_safe
          AND (nullif(e.sample_acquired, '') IS NOT NULL
               OR nullif(e.in_progress, '') IS NOT NULL
               OR nullif(e.ebp_standard_criteria, '') IS NOT NULL
               OR (lower(coalesce(e.assembly_level, '')) IN ('chromosome', 'complete genome')
                   AND coalesce(try_cast(e.busco_completeness AS DOUBLE), 0) >= 90))
    """).fetchnumpy()["node"]
    _assessed = _con.sql(f"""
        SELECT p.iucn_sis_id, p.gap_myr AS stored_gap, t.node
        FROM '{PHYLO_P}' p JOIN tips t USING (tree_tip)
    """).pl()
    _nodes = _assessed["node"].to_numpy().astype(np.int64)

    def _with_ancestors(nodes: np.ndarray) -> np.ndarray:
        marked = np.zeros(_n, dtype=bool)
        for node in nodes:
            while node >= 0 and not marked[node]:
                marked[node] = True
                node = _parent[node]
        return marked

    _covered = _with_ancestors(_represented)
    _order = np.flatnonzero(_with_ancestors(_nodes))
    # Unsequenced assessed species below each node (species, not tips, so two
    # species sharing a tip split its branch).
    _count = np.zeros(_n)
    np.add.at(_count, _nodes[~_covered[_nodes]], 1.0)
    for _node in _order[::-1]:
        if _parent[_node] >= 0:
            _count[_parent[_node]] += _count[_node]
    _gap = np.zeros(_n)
    _share = np.zeros(_n)
    for _node in _order:
        _up = _parent[_node]
        if _up >= 0 and not _covered[_node]:
            _gap[_node] = _gap[_up] + _length[_node]
            _share[_node] = _share[_up] + _length[_node] / _count[_node]
    tree_gaps = _assessed.select(
        "iucn_sis_id",
        gap_upper=pl.Series(_gap[_nodes]),
        share_upper=pl.Series(_share[_nodes]),
    )
    _stored = _assessed["stored_gap"].to_numpy()
    _check = ~np.isnan(_stored)
    _drift = float(np.abs(_gap[_nodes][_check] - _stored[_check]).max())
    if _drift > 1e-6:
        raise ValueError(f"Recomputed gaps differ from the generation by {_drift} Myr")
    _uncovered = _order[~_covered[_order] & (_parent[_order] >= 0)]
    gap_totals = {
        "represented_tips": len(_represented),
        "marginal_sum": float(_gap[_nodes].sum()),
        "union_pd": float(_length[_uncovered].sum()),
        "share_sum": float(_share[_nodes].sum()),
    }
    return gap_totals, tree_gaps


@app.cell
def _(PHYLO_P, SPECIES_P, TRANSFER_P, duckdb, tree_gaps):
    con = duckdb.connect()
    con.register("tree_gaps", tree_gaps.to_arrow())
    species = con.sql(f"""
        SELECT s.gbif_accepted_id AS sid,
               s.redlist_category,
               s.goat_data_deficient,
               s.family_has_dna,
               s.genus_has_dna,
               s.has_dna_species_level,
               YEAR(TRY_CAST(s.iucn_assessment_date AS TIMESTAMPTZ)) AS assessed,
               t.genomic_transfer_safe,
               CASE WHEN p.availability_reason IS NULL THEN p.gap_myr END AS gap_myr,
               CASE WHEN p.iucn_sis_id IS NULL THEN 'not_in_generation'
                    ELSE p.availability_reason END AS gap_reason,
               p.edge2_myr_point AS edge2,
               g.gap_upper,
               g.share_upper
        FROM '{SPECIES_P}' s
        LEFT JOIN '{TRANSFER_P}' t USING (iucn_sis_id)
        LEFT JOIN '{PHYLO_P}' p USING (iucn_sis_id)
        LEFT JOIN tree_gaps g USING (iucn_sis_id)
        ORDER BY sid
    """).pl()
    return con, species


@app.cell
def _(LISTS_P, con, np, sparse, species):
    # Cells x species incidence. Row/column order is fixed by these sorts and is
    # shared by every later array.
    con.register("species_order", species.select("sid").with_row_index("j").to_arrow())
    pairs = con.sql(f"""
        WITH cells AS (
            SELECT h3_index AS cell, row_number() OVER (ORDER BY h3_index) - 1 AS i, species_ids
            FROM '{LISTS_P}'
        )
        SELECT c.i, o.j, c.cell
        FROM (SELECT cell, i, UNNEST(species_ids) AS sid FROM cells) c
        JOIN species_order o USING (sid)
    """).fetchnumpy()
    cell_keys = con.sql(f"SELECT h3_index FROM '{LISTS_P}' ORDER BY h3_index").fetchnumpy()[
        "h3_index"
    ]
    incidence = sparse.csr_matrix(
        (np.ones(len(pairs["i"]), dtype=np.float32), (pairs["i"], pairs["j"])),
        shape=(len(cell_keys), species.height),
    )
    range_cells = np.asarray(incidence.sum(axis=0)).ravel()
    return cell_keys, incidence, range_cells


@app.cell
def _(DNA_WEIGHTS, THREAT_WEIGHTS, np, pl, species):
    # Per-species components that the factor levels combine. Bucket order mirrors
    # `_weight_ladder.dna_weight_sql`: first match wins, NULL flags fall through.
    def _not(col: str) -> pl.Expr:
        return pl.col(col).eq(False).fill_null(False)

    def _not_false(col: str) -> pl.Expr:
        return pl.col(col).ne(False).fill_null(True)

    gdd = pl.col("goat_data_deficient").fill_null(False)
    known = pl.col("goat_data_deficient").eq(False).fill_null(False)
    components = species.select(
        threat=pl.col("redlist_category").replace_strict(
            THREAT_WEIGHTS, default=0.0, return_dtype=pl.Float64
        ),
        is_dd=pl.col("redlist_category").eq("Data Deficient").fill_null(False),
        bucket=pl.when(gdd)
        .then(pl.lit("gdd"))
        .when(known & _not("family_has_dna"))
        .then(pl.lit("fam"))
        .when(known & _not_false("family_has_dna") & _not("genus_has_dna"))
        .then(pl.lit("gen"))
        .when(
            known
            & _not_false("family_has_dna")
            & _not_false("genus_has_dna")
            & _not("has_dna_species_level")
        )
        .then(pl.lit("sp"))
        .otherwise(pl.lit("samp")),
        strict_match=pl.col("genomic_transfer_safe").fill_null(False),
        recent=pl.col("assessed").ge(2015).fill_null(False),
        gap=pl.col("gap_myr").fill_null(0.0),
        gap_upper=pl.col("gap_upper").fill_null(0.0),
        share_upper=pl.col("share_upper").fill_null(0.0),
    )
    LADDERS = {
        "default": DNA_WEIGHTS,
        "flat": {"gdd": 4.0, "fam": 4.0, "gen": 4.0, "sp": 4.0, "samp": 0.0},
        "exact-species": {"gdd": 4.0, "fam": 1.0, "gen": 2.0, "sp": 4.0, "samp": 0.0},
    }
    bucket = components["bucket"].to_numpy()
    threat_base = components["threat"].to_numpy()
    is_dd = components["is_dd"].to_numpy()
    strict_match = components["strict_match"].to_numpy()
    recent = components["recent"].to_numpy()
    # Gap measures in Myr; species without a tree identity add zero to each.
    GAPS = {
        "gap: scored only": components["gap"].to_numpy(),
        "gap: upper bound": components["gap_upper"].to_numpy(),
        "gap: fair-share upper": components["share_upper"].to_numpy(),
    }
    bucket_counts = components.group_by("bucket").len().sort("bucket")
    return GAPS, LADDERS, bucket, bucket_counts, is_dd, recent, strict_match, threat_base


@app.cell
def _(GAPS, LADDERS, itertools, pl):
    FACTORS = {
        "dd_weight": [2.0, 0.0, 1.0, 4.0],
        "dna_measure": [*LADDERS, *GAPS],
        "rarity": ["off", "on"],
        "taxonomy": ["all registered", "safe transfer only", "matched only"],
        "assessments": ["all", "2015+"],
    }
    specs = pl.DataFrame(
        [dict(zip(FACTORS, levels)) for levels in itertools.product(*FACTORS.values())]
    ).with_row_index("spec")
    BASELINE = 0  # first level of every factor
    return BASELINE, FACTORS, specs


@app.cell
def _(
    GAPS,
    LADDERS,
    bucket,
    incidence,
    is_dd,
    np,
    range_cells,
    recent,
    specs,
    strict_match,
    threat_base,
):
    def species_weights(spec: dict) -> np.ndarray:
        threat = np.where(is_dd, spec["dd_weight"], threat_base)
        if spec["dna_measure"] in GAPS:
            # Each gap measure carries its own identity rule; taxonomy levels coincide.
            dna = GAPS[spec["dna_measure"]]
        else:
            if spec["taxonomy"] == "safe transfer only":
                effective_bucket = np.where(strict_match, bucket, "gdd")
            else:
                effective_bucket = bucket
            ladder = LADDERS[spec["dna_measure"]]
            dna = np.select([effective_bucket == key for key in ladder], list(ladder.values()))
        weight = threat * dna
        if spec["taxonomy"] == "matched only" and spec["dna_measure"] in LADDERS:
            weight = np.where(strict_match & (bucket != "gdd"), weight, 0.0)
        if spec["assessments"] == "2015+":
            weight = np.where(recent, weight, 0.0)
        if spec["rarity"] == "on":
            weight = weight / np.maximum(range_cells, 1.0)
        return weight

    # cells x specs, one column at a time to avoid a species x specs matrix;
    # float64 keeps rarity sums of tiny terms comparable.
    scores = np.column_stack(
        [incidence @ species_weights(row) for row in specs.iter_rows(named=True)]
    )
    return (scores,)


@app.cell
def _(BASELINE, cell_keys, math, np, pl, scores, specs):
    from scipy.stats import rankdata

    n_cells = scores.shape[0]
    TOP_SHARES = (0.01, 0.05, 0.10)

    def top_mask(column: np.ndarray, share: float) -> np.ndarray:
        # Descending score, ties broken by descending cell key (as in score_surfaces).
        k = max(1, math.ceil(share * n_cells))
        order = np.lexsort((cell_keys, column))[::-1][:k]
        mask = np.zeros(n_cells, dtype=bool)
        mask[order] = True
        return mask

    top = {
        s: np.column_stack([top_mask(scores[:, j], s) for j in range(scores.shape[1])])
        for s in TOP_SHARES
    }
    ranks = np.column_stack([rankdata(-scores[:, j]) for j in range(scores.shape[1])])
    base_rank = ranks[:, BASELINE]

    def jaccard(a: np.ndarray, b: np.ndarray) -> float:
        return float((a & b).sum() / (a | b).sum())

    spec_metrics = specs.with_columns(
        spearman=pl.Series(
            [float(np.corrcoef(base_rank, ranks[:, j])[0, 1]) for j in range(scores.shape[1])]
        ),
        **{
            f"jaccard_top{round(s * 100)}": pl.Series(
                [jaccard(top[s][:, BASELINE], top[s][:, j]) for j in range(scores.shape[1])]
            )
            for s in TOP_SHARES
        },
        zero_in_top5=pl.Series(
            [int((top[0.05][:, j] & (scores[:, j] == 0)).sum()) for j in range(scores.shape[1])]
        ),
    )
    return base_rank, jaccard, n_cells, rankdata, spec_metrics, top, top_mask


@app.cell
def _(
    bucket_counts,
    gap_totals,
    incidence,
    mo,
    n_cells,
    np,
    pl,
    scores,
    spec_metrics,
    species,
):
    _mapped = np.asarray(incidence.sum(axis=0)).ravel() > 0
    _gap_status = (
        species.filter(pl.Series(_mapped)).group_by("gap_reason").len().sort("len", descending=True)
    )
    _gap_lines = "\n    ".join(
        f"| {row['gap_reason'] or 'scored'} | {row['len']:,} |" for row in _gap_status.to_dicts()
    )
    _gdd = bucket_counts.filter(bucket="gdd")["len"].sum()
    _total = bucket_counts["len"].sum()
    _zero = int(spec_metrics["zero_in_top5"].max())
    mo.md(f"""
    ## Grid summary

    {scores.shape[1]} specifications over **{n_cells:,}** res-3 cells. The top 1%
    is {round(0.01 * n_cells):,} cells; the top 5% is {round(0.05 * n_cells):,}.
    Of {_total:,} registered species, **{_gdd:,} ({_gdd / _total:.0%})** are in the
    unknown (`gdd`) DNA bucket. The baseline gives these species the maximum DNA
    weight, which is why the taxonomy factor matters below.
    {"" if _zero == 0 else f"⚠ Up to {_zero} zero-score cells enter a top 5% under some specification; there the tie-break is arbitrary."}

    Across all specifications, the median Spearman rank correlation with the
    baseline is **{spec_metrics["spearman"].median():.3f}** (min
    {spec_metrics["spearman"].min():.3f}). The median top-5% Jaccard overlap is
    **{spec_metrics["jaccard_top5"].median():.2f}** (min
    {spec_metrics["jaccard_top5"].min():.2f}), and the median top-1% overlap is
    {spec_metrics["jaccard_top1"].median():.2f} (min {spec_metrics["jaccard_top1"].min():.2f}).

    Phylogenetic-gap status of the {int(_mapped.sum()):,} mapped species
    (unscored species add zero under the gap measure):

    | Status | Species |
    | --- | --- |
    {_gap_lines}

    **Shared branches.** Against {gap_totals["represented_tips"]:,} represented
    tips, the marginal upper-bound gaps of all assessed species sum to
    {gap_totals["marginal_sum"]:,.0f} Myr, while the uncovered branches they
    reach total {gap_totals["union_pd"]:,.0f} Myr
    (×{gap_totals["marginal_sum"] / gap_totals["union_pd"]:.1f}). Fair shares sum
    to {gap_totals["share_sum"]:,.0f} Myr.
    """)
    return


@app.cell
def _(base_rank, incidence, jaccard, mo, np, rankdata, species, top, top_mask):
    # Is the baseline hotspot pattern mostly species richness? Compare it with
    # weight-free counts and summed EDGE2 over the same incidence matrix.
    _threatened = (
        species["redlist_category"]
        .is_in(["Critically Endangered", "Endangered", "Vulnerable"])
        .to_numpy()
    )
    _references = {
        "all registered species": np.asarray(incidence.sum(axis=1)).ravel(),
        "CR/EN/VU species": np.asarray(incidence @ _threatened.astype(np.float32)).ravel(),
        # EDGE2 already contains extinction risk; no threat weight and no DNA term.
        "Σ EDGE2 (no DNA term)": np.asarray(
            incidence @ species["edge2"].fill_null(0.0).to_numpy()
        ).ravel(),
    }
    _rows = "\n".join(
        f"| {name} | {np.corrcoef(base_rank, rankdata(-count))[0, 1]:.3f} | "
        f"{jaccard(top[0.05][:, 0], top_mask(count, 0.05)):.2f} | "
        f"{jaccard(top[0.01][:, 0], top_mask(count, 0.01)):.2f} |"
        for name, count in _references.items()
    )
    mo.md(f"""
    ### Reference: how much is richness?

    How closely the baseline score tracks plain species counts. High overlap means
    the weights mostly rescale richness rather than reorder cells. Summed EDGE2
    (threatened evolutionary history, no sequencing term) shows how far a pure
    phylogenetic-conservation surface sits from the sequencing-priority score.

    | Count | Spearman vs baseline | top-5% Jaccard | top-1% Jaccard |
    | --- | --- | --- | --- |
    {_rows}
    """)
    return


@app.cell
def _(FACTORS, alt, pl, spec_metrics):
    # Main effect: mean over every specification at that level, so each factor is
    # judged across all combinations of the others rather than at baseline only.
    main_effects = pl.concat(
        [
            spec_metrics.group_by(factor)
            .agg(
                pl.len().alias("specs"),
                pl.col("jaccard_top5").mean().alias("mean_jaccard_top5"),
                pl.col("jaccard_top5").min().alias("min_jaccard_top5"),
                pl.col("jaccard_top1").mean().alias("mean_jaccard_top1"),
                pl.col("spearman").mean().alias("mean_spearman"),
            )
            .select(
                pl.lit(factor).alias("factor"),
                pl.col(factor).cast(pl.String).alias("level"),
                pl.all().exclude(factor),
            )
            for factor in FACTORS
        ]
    )
    main_effect_chart = (
        alt.Chart(main_effects)
        .mark_bar()
        .encode(
            x=alt.X(
                "mean_jaccard_top5:Q",
                title="Mean top-5% Jaccard vs baseline",
                scale=alt.Scale(domain=[0, 1]),
            ),
            y=alt.Y("level:N", title=None, sort=None),
            row=alt.Row("factor:N", title=None, sort=list(FACTORS)),
            tooltip=[
                "factor",
                "level",
                alt.Tooltip("mean_jaccard_top5:Q", format=".2f"),
                alt.Tooltip("min_jaccard_top5:Q", format=".2f"),
                alt.Tooltip("mean_spearman:Q", format=".3f"),
            ],
        )
        .properties(width=420, title="Which choice moves the top 5% most?")
        .resolve_scale(y="independent")
    )
    main_effect_chart
    return (main_effects,)


@app.cell
def _(FACTORS, main_effects, pl):
    # Spread of mean overlap across a factor's levels: the largest spread is the
    # choice the hotspot pattern depends on most.
    factor_spread = (
        main_effects.group_by("factor")
        .agg(
            (pl.col("mean_jaccard_top5").max() - pl.col("mean_jaccard_top5").min()).alias(
                "spread_top5"
            ),
            pl.col("level").sort_by("mean_jaccard_top5").first().alias("most_disruptive_level"),
            pl.col("mean_jaccard_top5").min().alias("lowest_mean_top5"),
        )
        .sort("spread_top5", descending=True)
    )
    assert set(factor_spread["factor"]) == set(FACTORS)
    factor_spread
    return (factor_spread,)


@app.cell
def _(mo, specs):
    _density = f"Priority density only (rarity off, {specs.height // 2} specs)"
    family = mo.ui.radio(
        options={
            _density: "density",
            f"Full grid (rarity on and off, {specs.height} specs)": "full",
        },
        value=_density,
        label="Specifications counted in robustness(c)",
    )
    mo.vstack(
        [
            mo.md(
                "Rarity weighting changes the top set far more than any other choice. "
                "It turns the score into a different question (irreplaceability rather "
                "than priority density), so robustness is shown within the current score "
                "by default."
            ),
            family,
        ]
    )
    return (family,)


@app.cell
def _(family, pl, spec_metrics):
    selected = (
        spec_metrics.filter(pl.col("rarity") == "off")
        if family.value == "density"
        else spec_metrics
    )
    selected_idx = selected["spec"].to_numpy()
    return selected, selected_idx


@app.cell
def _(BASELINE, cell_keys, h3, np, pl, selected_idx, top):
    robustness = pl.DataFrame(
        {
            "cell": cell_keys,
            "robust_top5": top[0.05][:, selected_idx].mean(axis=1),
            "robust_top1": top[0.01][:, selected_idx].mean(axis=1),
            "baseline_top5": top[0.05][:, BASELINE],
            "baseline_top1": top[0.01][:, BASELINE],
        }
    )
    _latlng = np.array([h3.cell_to_latlng(h3.int_to_str(int(c))) for c in cell_keys])
    robustness = robustness.with_columns(lat=pl.Series(_latlng[:, 0]), lng=pl.Series(_latlng[:, 1]))
    return (robustness,)


@app.cell
def _(mo, pl, robustness, selected_idx):
    _base = robustness.filter("baseline_top5")
    _core = _base.filter(pl.col("robust_top5") >= 0.95).height
    _fragile = _base.filter(pl.col("robust_top5") < 0.5).height
    _entrants = robustness.filter(~pl.col("baseline_top5") & (pl.col("robust_top5") >= 0.5)).height
    _ever = robustness.filter(pl.col("robust_top5") > 0).height
    robustness_summary = {
        "baseline_top5": _base.height,
        "core": _core,
        "fragile": _fragile,
        "entrants": _entrants,
        "ever": _ever,
    }
    mo.md(f"""
    ## robustness(c) over {len(selected_idx)} specifications

    Of the **{_base.height:,}** baseline top-5% cells:

    - **{_core:,} ({_core / _base.height:.0%})** stay in the top 5% under ≥95% of
      specifications. This is the robust core.
    - **{_fragile:,} ({_fragile / _base.height:.0%})** stay in under fewer than half.
      These hotspots depend on a choice.
    - {_entrants:,} cells outside the baseline top 5% are in it under at least half
      of the specifications. The baseline misses them.
    - {_ever:,} distinct cells reach the top 5% under at least one specification.
    """)
    return (robustness_summary,)


@app.cell
def _(alt, pl, robustness):
    _land = robustness.filter(pl.col("robust_top5") > 0)
    robustness_map = (
        alt.Chart(_land)
        .mark_circle(size=6, opacity=0.8)
        .encode(
            longitude="lng:Q",
            latitude="lat:Q",
            color=alt.Color(
                "robust_top5:Q",
                title="share of specs in top 5%",
                scale=alt.Scale(scheme="viridis", domain=[0, 1]),
            ),
            tooltip=[alt.Tooltip("robust_top5:Q", format=".2f"), "baseline_top5:N"],
        )
        .project("equalEarth")
        .properties(width=720, height=380, title="robustness(c): cells ever in a top 5%")
    )
    robustness_map
    return


@app.cell
def _(BASELINE, FACTORS, pl, selected, selected_idx, top):
    # Why do fragile cells fall out? For baseline top-5% cells, retention under each
    # factor level averaged over all other factors.
    _base_rows = top[0.05][:, BASELINE]
    _retained = top[0.05][_base_rows][:, selected_idx]
    _position = {spec: i for i, spec in enumerate(selected_idx)}
    _fragile = _retained.mean(axis=1) < 0.5
    attribution = pl.DataFrame(
        [
            {
                "factor": factor,
                "level": str(level),
                "retention_all_baseline_top5": float(_retained[:, idx].mean()),
                "retention_fragile": float(_retained[_fragile][:, idx].mean())
                if _fragile.any()
                else float("nan"),
            }
            for factor in FACTORS
            for level in FACTORS[factor]
            for idx in [[_position[s] for s in selected.filter(pl.col(factor) == level)["spec"]]]
            if idx
        ]
    )
    attribution
    return


@app.cell
def _(BOUND_P, con, pl, robustness):
    # admin0 contact lists overlap (a cell can touch several countries and EEZs);
    # counts are contacts, not a partition of cells.
    _admin = con.sql(f"SELECT h3_index AS cell, UNNEST(admin0) AS country FROM '{BOUND_P}'").pl()
    countries = (
        _admin.join(robustness, on="cell")
        .group_by("country")
        .agg(
            pl.col("baseline_top5").sum().alias("baseline_top5_cells"),
            (pl.col("baseline_top5") & (pl.col("robust_top5") >= 0.95)).sum().alias("robust_core"),
            (pl.col("baseline_top5") & (pl.col("robust_top5") < 0.5)).sum().alias("fragile"),
            (~pl.col("baseline_top5") & (pl.col("robust_top5") >= 0.5)).sum().alias("entrants"),
        )
        .filter(pl.col("baseline_top5_cells") + pl.col("entrants") > 0)
        .with_columns((pl.col("robust_core") / pl.col("baseline_top5_cells")).alias("core_share"))
        .sort("baseline_top5_cells", descending=True)
    )
    countries.head(25)
    return (countries,)


@app.cell
def _(countries, factor_spread, mo, pl, robustness_summary):
    _top = factor_spread.row(0, named=True)
    _second = factor_spread.row(1, named=True)
    _fragile_countries = (
        countries.filter(pl.col("baseline_top5_cells") >= 10)
        .sort("core_share")
        .head(5)["country"]
        .to_list()
    )
    _solid_countries = (
        countries.filter(pl.col("baseline_top5_cells") >= 10)
        .sort(["core_share", "baseline_top5_cells"], descending=True)
        .head(5)["country"]
        .to_list()
    )
    mo.md(f"""
    ## Reading

    - The choice the hotspot pattern depends on most is **{_top["factor"]}**
      (mean top-5% overlap ranges over {_top["spread_top5"]:.2f} across levels;
      lowest under *{_top["most_disruptive_level"]}*), followed by
      **{_second["factor"]}** (spread {_second["spread_top5"]:.2f}).
    - {robustness_summary["core"]:,} of {robustness_summary["baseline_top5"]:,}
      baseline top-5% cells form a core that survives ≥95% of the selected specifications, and
      {robustness_summary["fragile"]:,} are fragile.
    - Countries with ≥10 baseline top-5% cell contacts and the highest robust-core share:
      {", ".join(_solid_countries)}. Lowest: {", ".join(_fragile_countries)}.

    These are res-3 any-touch results on one export snapshot. They show how
    sensitive the hotspots are to the model's choices; they do not validate any
    choice. The geometry specifications listed above remain untested, and the
    choice of which alternatives count as "reasonable" belongs to the biology
    review.
    """)
    return


if __name__ == "__main__":
    app.run()
