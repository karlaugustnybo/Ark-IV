# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "marimo>=0.19",
#     "altair>=6,<7",
#     "duckdb>=1.5,<2",
#     # pyarrow is not imported directly but is required transitively by the
#     # DuckDB→polars bridge (polars' from_arrow path). Trim only with a fresh
#     # headless run to prove it.
#     "pyarrow>=21",
#     "polars>=1.30",
#     "h3>=4.4,<5",
# ]
# ///

import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")


@app.cell
def _():
    import math
    import os
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import altair as alt
    import duckdb
    import h3
    import marimo as mo
    import polars as pl
    from _notebook_inputs import configured_inputs

    return alt, configured_inputs, duckdb, h3, math, mo, os, pl


@app.cell
def _(EXPORT_STAMP, RES3_AREA, RES3_SPAN, RES7_AREA, RES7_SPAN, mo):
    mo.md(f"""
    # Score surfaces from the same pairs

    Four roadmap items share one data plane — the res-3 serving pairs joined to
    the threat × DNA weight ladder — so this notebook works through them together
    (companion to `range_dna_investigation.py`, which established the per-species
    range size `N_s` these items reuse):

    1. **Range-rarity weighting** *(roadmap §6, P1)* — compute
       `R_c = Σ over species s in cell c of w_s / N_s` and report rank changes
       against the current score, the first thing the item asks for.
    2. **Hotspot robustness diagnostic, mini version** *(roadmap §6, P0)* — vary
       the Data-Deficient weight, the DNA weighting, the range-rarity
       on/off switch, and per-class scoring; measure Spearman rank
       correlation and Jaccard overlap of top-{1, 5, 10}% hotspots per
       specification, and report a per-cell "remains top-1% under x of n
       specifications" surface.
    3. **Stewardship fractions** *(second half of the same P1 item)* — the share
       of each species' mapped distribution that a country, ADM1, or
       municipality holds, turned into a country-level work order for the
       previously computed dark-spot cohort.
    4. **Priority-score components** *(roadmap §2, P2)* — is a high cell threat
       driven, genomic-gap driven, or just rich? The per-cell joint
       threat × DNA composition.

    Everything below is computed live against the consumed input identity
    **{EXPORT_STAMP}** (SHA-256 of the consumed files; not a provider release date); conclusions interpolate those
    live numbers instead of quoting earlier runs.

    **Honest-epistemics framing.** Nothing here is written, scored server-side,
    or treated as validated. All weights are mirrored exactly from
    `ark_pipeline/builders/coarse_cache.py` (`DEFAULT_WEIGHTS`: threat cr 4 ·
    en 3 · vu 2 · nt 1 · dd 2 · lc 0.1; DNA gdd 4 · fam 4 · gen 3 · sp 2 ·
    samp 0), including the consequence that legacy `Lower Risk/*` rows and
    `Not Assessed` rows score zero. The canonical roadmap work runs at
    resolution 7 over computational tiles; every number here is a res-3
    *preview* of those rules. For grain: one res-3 hexagon averages
    ~{RES3_AREA:,.0f} km² and ~{RES3_SPAN:,.0f} km vertex-to-vertex — about
    {RES3_AREA / RES7_AREA:,.0f}× the res-7 fine cell (~{RES7_AREA:.1f} km²,
    ~{RES7_SPAN:.1f} km across), which is never loaded
    here; everything below is computed on res-3 exports. The exports expose
    only coarse membership; evidence types are not retained, while
    assessment dates are joined from the crosswalk, so several canonical specifications cannot be tested from these
    files at all — §3 lists them rather than hiding the gap.

    **Inputs**

    | Source | Used for |
    | --- | --- |
    | `data/global/exports/cell_species_lists.parquet` | res-3 cells and their species lists |
    | `data/global/exports/species.parquet` | registered species; Red List and DNA flags |
    | `data/global/exports/species_cells_res3.parquet` | per-species mapped range size `N_s` |
    | `data/global/exports/species_systems.parquet` | ecosystem systems (terrestrial / freshwater / marine) |
    | `data/global/exports/cell_boundaries.parquet` | country / ADM1 / municipality memberships per res-3 cell |
    | `data/exports/iucn_goat_global/iucn_goat_crosswalk.parquet` | GoaT lineage classes (IUCN class fallback) for the per-class specification |
    """)
    return


@app.cell
def _(configured_inputs, duckdb, h3, os):
    import sys as _sys
    from pathlib import Path as _Path

    # Shared weight-ladder module (the executed-SQL test compares production predicates;
    # see tests/test_weight_ladder.py and notebooks/_weight_ladder.py).
    _here = str(_Path(__file__).resolve().parent)
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    import _weight_ladder

    inputs = configured_inputs()
    EXPORTS = inputs.exports
    SPECIES_P = f"{EXPORTS}/species.parquet"
    SYSTEMS_P = f"{EXPORTS}/species_systems.parquet"
    CELLS_P = f"{EXPORTS}/species_cells_res3.parquet"
    LISTS_P = f"{EXPORTS}/cell_species_lists.parquet"
    BOUND_P = f"{EXPORTS}/cell_boundaries.parquet"
    CROSSWALK_P = inputs.crosswalk

    # Canonical, resolution-level H3 statistics instead of estimates from
    # sampled head cells (the sample was latitude-biased). The longest axis of
    # an average hexagon is twice the average edge length (vertex-to-vertex).
    RES3_AREA = float(h3.average_hexagon_area(3, unit="km^2"))
    RES3_SPAN = 2 * float(h3.average_hexagon_edge_length(3, unit="km"))
    RES7_AREA = float(h3.average_hexagon_area(7, unit="km^2"))
    RES7_SPAN = 2 * float(h3.average_hexagon_edge_length(7, unit="km"))
    EXPORT_STAMP = inputs.identity
    AS_OF_YEAR = int(os.environ.get("ARK_NOTEBOOK_AS_OF_YEAR", "2026"))
    if AS_OF_YEAR < 1:
        raise ValueError("ARK_NOTEBOOK_AS_OF_YEAR must be a positive calendar year")
    con = duckdb.connect()
    # Generated from _weight_ladder.py; equals coarse_cache.DEFAULT_WEIGHTS
    # by the executed-SQL fixture tests. `{WL_T}`-style fragments below are interpolation
    # points, not new ladders.
    WL_T = _weight_ladder.threat_weight_sql()
    WL_DNA = _weight_ladder.dna_weight_sql()
    return (
        AS_OF_YEAR,
        BOUND_P,
        CELLS_P,
        CROSSWALK_P,
        EXPORT_STAMP,
        LISTS_P,
        RES3_AREA,
        RES3_SPAN,
        RES7_AREA,
        RES7_SPAN,
        SPECIES_P,
        SYSTEMS_P,
        WL_DNA,
        WL_T,
        con,
    )


@app.cell
def _(
    BOUND_P,
    CELLS_P,
    LISTS_P,
    SPECIES_P,
    SYSTEMS_P,
    WL_DNA,
    WL_T,
    con,
    mo,
    pl,
):
    n_registered = con.sql(f"SELECT COUNT(*) FROM '{SPECIES_P}'").fetchone()[0]
    pair_level = con.sql(f"""
        WITH lists AS (
            SELECT h3_index AS cell, UNNEST(species_ids) AS sid
            FROM '{LISTS_P}'
        ),
        base AS (
            SELECT gbif_accepted_id AS sid,
                   species_name,
                   family,
                   redlist_category,
                   has_dna_species_level AS has_dna,
                   genus_has_dna,
                   family_has_dna,
                   goat_data_deficient AS goat_dd,
                   {WL_T} AS tw,
                   {WL_DNA} AS dw
                   FROM '{SPECIES_P}'
        ),
        ranges AS (
            SELECT gbif_accepted_id AS sid, len(h3_indexes) AS ncells
            FROM '{CELLS_P}'
        )
        SELECT p.cell, b.*, r.ncells
        FROM lists p
        JOIN base b USING (sid)
        LEFT JOIN ranges r USING (sid)
    """).pl()

    systems_df = con.sql(f"""
        SELECT gbif_accepted_id AS sid, string_agg(DISTINCT system, '/' ORDER BY system) AS systems
        FROM '{SYSTEMS_P}'
        GROUP BY 1
    """).pl()
    boundaries_df = con.sql(f"""
        SELECT h3_index AS cell,
               len(admin0) AS country_n,
               len(admin1) AS adm1_n,
               len(municipality) AS mun_n
        FROM '{BOUND_P}'
    """).pl()

    coverage = (
        pair_level.select(pl.len()).item(),
        pair_level["sid"].n_unique(),
        pair_level["cell"].n_unique(),
    )
    overview = mo.md(f"""
    **Volumes.** {coverage[0]:,} res-3 cell/species pairs across
    {coverage[1]:,} species and {coverage[2]:,} cells.
    Species without a mapped range (`ncells` null) can still be registered in
    the table — but every pair in the lists carries weights directly.
    """)
    return boundaries_df, n_registered, overview, pair_level, systems_df


@app.cell
def _(SPECIES_P, RES3_AREA, RES3_SPAN, RES7_AREA, RES7_SPAN, con, mo):
    _lr = con.sql(
        f"SELECT COUNT(*) FILTER (redlist_category LIKE 'Lower Risk%') FROM '{SPECIES_P}'"
    ).fetchone()[0]
    _na = con.sql(
        f"SELECT COUNT(*) FILTER (redlist_category = 'Not Assessed') FROM '{SPECIES_P}'"
    ).fetchone()[0]
    mo.md(f"""
    ## 1. Reproducing the current score at resolution 3

    The serving model multiplies each species' threat weight and DNA weight and
    sums over the cell: `C_c = Σ over species in cell of w_threat × w_DNA`.
    The weight ladder and the mutually exclusive DNA predicates are copied
    verbatim from `coarse_cache.py`; one consequence is worth surfacing:
    legacy `Lower Risk/*` rows ({_lr:,}) and `Not Assessed` rows ({_na:,})
    match no threat predicate and therefore contribute zero. These are both
    IUCN-side bookkeeping states — pre-2001 legacy ranks and IUCN-side
    non-ranks — not GoaT gaps, and they are distinct from GoaT Data
    Deficient, which is a genomic-data state. Every later section conditions
    on this definition, so it doubles as the audit baseline.

    - one res-3 hexagon averages ~{RES3_AREA:,.0f} km², ~{RES3_SPAN:,.0f} km
      vertex-to-vertex; the canonical rule is evaluated at res 7
      (not loaded here), where a cell is ~{RES7_AREA:.1f} km²,
      ~{RES7_SPAN:.1f} km across.
    """)
    return


@app.cell
def _(alt):
    alt.data_transformers.disable_max_rows()
    return


@app.cell
def _(AS_OF_YEAR, BOUND_P, CELLS_P, CROSSWALK_P, LISTS_P, SPECIES_P, WL_DNA, WL_T, con, mo, pl):
    # One pass emits the baseline ladder plus every export-supported
    # specification §2/§3 read from these processing columns:
    #   recent15      - only assessments dated 2015+ (crosswalk date)
    #   decayed       - per-assessment age decay, 10-year half-life
    #   exact_only    - only CURRENT_NAME_EXACT crosswalk rows contribute
    #   genus_partial - EBP-style categorical ladder: genus-represented is a
    #                   partially-covered state (weight 1), below family-only
    #                   (2) and family-absent/gdd (4)
    #   edge_pilot    - EDGE-cohort dummy (available where labelled, 0 else)
    #   framework     - restricted to cells bearing ≥1 RESOLVE 2017 ecoregion
    WEIGHT_SQL = f"""
    WITH lists AS (
        SELECT h3_index AS cell, UNNEST(species_ids) AS sid
        FROM '{LISTS_P}'
    ),
    cw AS (
        SELECT iucn_sis_id AS sis,
               YEAR(TRY_CAST(iucn_assessment_date AS TIMESTAMP)) AS ayear,
               taxonomic_concept_relation AS crel,
               safe_for_automatic_species_trait_transfer AS csafe
        FROM '{CROSSWALK_P}'
    ),
    base AS (
        SELECT gbif_accepted_id AS sid,
               redlist_category,
               goat_data_deficient,
               family_has_dna,
               genus_has_dna,
               has_dna_species_level,
               TRY_CAST(iucn_sis_id AS BIGINT) AS sis,
               edge_group_name,
               {WL_T}::DOUBLE AS tw,
               {WL_DNA}::DOUBLE AS dw
        FROM '{SPECIES_P}'
    ),
    base_cw AS (
        SELECT b.*, cw.ayear, cw.crel, cw.csafe
        FROM base b LEFT JOIN cw USING (sis)
    ),
    framework_cells AS (
        SELECT DISTINCT h3_index AS cell
        FROM '{BOUND_P}' WHERE len(conservation_framework) > 0
    ),
    pairs AS (
        SELECT l.cell, b.tw, b.dw, b.redlist_category, b.goat_data_deficient,
               b.family_has_dna, b.genus_has_dna, b.has_dna_species_level,
               b.ayear, b.crel, b.edge_group_name,
               (fw.cell IS NOT NULL) AS fwall,
               r.ncells
        FROM lists l
        JOIN base_cw b USING (sid)
        LEFT JOIN (SELECT gbif_accepted_id AS sid, len(h3_indexes) AS ncells
                   FROM '{CELLS_P}') r USING (sid)
        LEFT JOIN framework_cells fw USING (cell)
    )
    SELECT cell,
           COUNT(*) AS n_species,
           SUM(tw * dw) AS baseline,
           SUM(CASE WHEN redlist_category = 'Data Deficient' THEN 0.0 ELSE tw END * dw) AS dd0,
           SUM(CASE WHEN redlist_category = 'Data Deficient' THEN 1.0 ELSE tw END * dw) AS dd1,
           SUM(CASE WHEN redlist_category = 'Data Deficient' THEN 4.0 ELSE tw END * dw) AS dd4,
           SUM(tw * CASE WHEN goat_data_deficient THEN 4.0
                         WHEN goat_data_deficient = false AND NOT family_has_dna THEN 4.0
                         WHEN goat_data_deficient = false AND family_has_dna IS DISTINCT FROM false AND NOT genus_has_dna THEN 4.0
                         WHEN goat_data_deficient = false AND family_has_dna IS DISTINCT FROM false AND genus_has_dna IS DISTINCT FROM false AND NOT has_dna_species_level THEN 4.0
                         ELSE 0.0 END) AS flat_dna,
           SUM(tw * CASE WHEN goat_data_deficient THEN 4.0
                         WHEN goat_data_deficient = false AND NOT family_has_dna THEN 1.0
                         WHEN goat_data_deficient = false AND family_has_dna IS DISTINCT FROM false AND NOT genus_has_dna THEN 2.0
                         WHEN goat_data_deficient = false AND family_has_dna IS DISTINCT FROM false AND genus_has_dna IS DISTINCT FROM false AND NOT has_dna_species_level THEN 4.0
                         ELSE 0.0 END) AS exact_species,
           SUM(tw * dw / GREATEST(ncells, 1.0)) AS rarity,
           SUM(CASE WHEN ayear >= 2015 THEN tw * dw ELSE 0.0 END) AS recent15,
           SUM(tw * dw * CASE WHEN ayear IS NOT NULL
                              THEN POW(0.5, GREATEST({AS_OF_YEAR}.0 - ayear, 0.0) / 10.0)
                              ELSE 1.0 END) AS decayed,
           SUM(CASE WHEN crel = 'CURRENT_NAME_EXACT' THEN tw * dw ELSE 0.0 END) AS exact_only,
           SUM(tw * CASE WHEN goat_data_deficient THEN 4.0
                         WHEN goat_data_deficient = false AND NOT family_has_dna THEN 4.0
                         WHEN goat_data_deficient = false AND family_has_dna IS DISTINCT FROM false AND NOT genus_has_dna THEN 2.0
                         WHEN goat_data_deficient = false AND family_has_dna IS DISTINCT FROM false AND genus_has_dna IS DISTINCT FROM false AND NOT has_dna_species_level THEN 1.0
                         ELSE 0.0 END) AS genus_partial,
           SUM(CASE WHEN edge_group_name IS NOT NULL THEN tw * dw ELSE 0.0 END) AS edge_pilot,
           SUM(CASE WHEN fwall THEN tw * dw ELSE 0.0 END) AS framework
    FROM pairs
    GROUP BY cell
    """
    cell_scores = con.sql(WEIGHT_SQL).pl()
    mo.stop(
        cell_scores.height == 0,
        mo.md("No joined cell/species pairs; score surfaces are unavailable."),
    )
    return (cell_scores,)


@app.cell
def _(cell_scores, mo, pl):
    n_cells = cell_scores.height
    n_zero = cell_scores.filter(pl.col("baseline") == 0.0).height
    mo.md(f"""
    {n_cells:,} cells rebuilt; {n_zero:,} ({n_zero / n_cells:.0%}) carry a
    zero baseline score even though they host species — these are cells where
    the pair ladder is LC × sampled (weight 0.1 × 0) or legacy/Not-Assessed
    categories. They will matter for the robustness reading in §3: ranks
    among them are pure ties.

    """)
    return


@app.cell
def _(cell_scores, mo, pl):
    top10 = (
        cell_scores.sort("baseline", descending=True)
        .head(10)
        .select(
            "cell",
            pl.col("baseline").round(1).alias("score"),
            pl.col("rarity").round(2).alias("rarity"),
            "n_species",
        )
    )
    mo.ui.table(top10.to_dicts(), label="Ten highest baseline res-3 cells")
    return


@app.cell
def _(cell_scores, h3, pl):
    center = {c: h3.cell_to_latlng(h3.int_to_str(c)) for c in list(cell_scores["cell"])}
    cell_points = cell_scores.with_columns(
        pl.col("cell")
        .map_elements(lambda c: round(center[c][0], 4), return_dtype=pl.Float64)
        .alias("lat"),
        pl.col("cell")
        .map_elements(lambda c: round(center[c][1], 4), return_dtype=pl.Float64)
        .alias("lon"),
        pl.col("cell").map_elements(h3.int_to_str, return_dtype=pl.String).alias("cell_index"),
    )
    return (cell_points,)


@app.cell
def _(alt, cell_points, mo, pl):
    def score_map(frame, metric: str, title: str, legend_title: str):
        # Vega-Lite embeds the *whole* source frame into the spec, so pass
        # only the plotted columns — the full score matrix would push every
        # panel past marimo's output-size limit.
        _frac = frame.select(
            "lon",
            "lat",
            pl.col(metric).sqrt().alias("sqrt_" + metric),
            pl.col(metric).round(3),
        )
        _dom = [0.0, float(_frac["sqrt_" + metric].quantile(0.99))]
        return (
            alt.Chart(_frac)
            .mark_square(size=13, opacity=1.0)
            .encode(
                x=alt.X("lon:Q", scale=alt.Scale(domain=[-180, 190]), axis=None),
                y=alt.Y("lat:Q", scale=alt.Scale(domain=[-60, 85]), axis=None),
                color=alt.Color(
                    f"sqrt_{metric}:Q",
                    scale=alt.Scale(scheme="inferno", domain=_dom),
                    legend=alt.Legend(
                        title=f"{legend_title} (√-scale, clipped at cell p99)",
                        symbolType="square",
                    ),
                ),
                tooltip=[alt.Tooltip(f"{metric}:Q", format=".1f", title="score")],
            )
            .properties(width=620, height=290, title=title)
        )

    surface = mo.ui.dropdown(
        options=[
            "baseline",
            "rarity",
            "dd0",
            "dd1",
            "dd4",
            "flat_dna",
            "exact_species",
            "recent15",
            "decayed",
            "exact_only",
            "genus_partial",
            "edge_pilot",
            "framework",
        ],
        value="baseline",
        label="colour the map with",
    )
    surface
    return (score_map, surface)


@app.cell
def _(cell_points, score_map, surface):
    chosen = surface.value
    current_map = score_map(
        cell_points,
        chosen,
        f"Res-3 seen through '{chosen}'",
        "score (sqrt)",
    ).configure_view(stroke=None)
    current_map
    return (current_map,)


@app.cell
def _(mo):
    mo.md(r"""
    *Cartography note for every cell-priority scatter here:* the frame is
    equirectangular (plate carrée) with latitude cropped at −60° — Antarctica
    is truncated by the crop, and the projection inflates the areal impression
    of high-latitude cells. For publication-quality hotspot maps use an
    Equal-Earth projection instead. Also note the legend: colour encodes the
    *square-rooted* cell score clipped at the cell-quartile-99 ceiling, so top
    one-percentile cells all share the same colour — apparent density in the
    map is √-scaled score, not area-weighted priority.
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 2. Range-rarity weighting *(roadmap P1)*

    The roadmap item proposes a second global score that *distributes* each
    species' weight over its mapped range instead of repeating it per cell:

    $$R_c = \sum_{s\,in\,cell\,c} w_s \over N_s$$

    where `N_s` is the number of occupied cells for species `s` (the same
    `ncells` the pipeline already stores). Every species then contributes a
    conserved global budget — exactly conserved, whenever the export's cell
    lists and per-species range sizes agree (verified below as an explicit
    invariant test) — so range-restricted species dominate locally. The
    contrast the current score cannot express: today a critically
    endangered micro-endemic and a critically endangered species spread across
    thousands of cells add the *same* per-cell value.

    This section is the item's own first requirement — "report rank changes
    against the existing score" — evaluated directly on the res-3 preview.
    """)
    return


@app.cell
def _(math, pl):
    def rank_set(scores, col: str, share: float) -> set:
        k = max(1, math.ceil(share * scores.height))
        return set(scores.sort([col, "cell"], descending=[True, True]).head(k)["cell"])

    def jaccard(a: set, b: set) -> float:
        union = len(a | b)
        return len(a & b) / union if union else 0.0

    def spearman(scores, base_col: str, other_col: str) -> float:
        ranks = scores.select(
            "cell",
            pl.col(base_col).rank(method="average", descending=True).alias("round"),
        ).join(
            scores.select(
                "cell",
                pl.col(other_col).rank(method="average", descending=True).alias("other"),
            ),
            on="cell",
        )
        value = ranks.select(pl.corr("round", "other").cast(float)).item()
        return value if value is not None and math.isfinite(value) else float("nan")

    return jaccard, rank_set, spearman


@app.cell
def _(cell_scores, jaccard, mo, pair_level, pl, rank_set, spearman):
    top_b = {s: rank_set(cell_scores, "baseline", s) for s in (0.01, 0.05, 0.10)}
    top_r = {s: rank_set(cell_scores, "rarity", s) for s in (0.01, 0.05, 0.10)}
    rho_all = spearman(cell_scores, "baseline", "rarity")
    rho_nz = spearman(cell_scores.filter(pl.col("baseline") > 0.0), "baseline", "rarity")
    # Conserved-budget invariant: summing R_c over all cells must return
    # Σ_s w_s once per species when every declared range cell is listed.
    # Checking the independent range lengths is essential: repeating the pair
    # calculation on both sides would be a tautology. Each pair appears in exactly
    # one cell's list. Any deviation here means the lists export and the
    # per-species ranges export disagree — i.e. the pipeline handoff broke.
    budget_species_side = float(
        pair_level.unique(subset="sid").select((pl.col("tw") * pl.col("dw")).sum()).item()
    )
    declared = pair_level.group_by("sid").agg(
        pl.col("cell").n_unique().alias("listed"),
        pl.col("ncells").first().alias("declared"),
        pl.len().alias("pairs"),
    )
    range_mismatches = declared.filter(
        pl.col("declared").is_null()
        | (pl.col("listed") != pl.col("declared"))
        | (pl.col("pairs") != pl.col("listed"))
    ).height
    budget_cells_side = float(cell_scores["rarity"].sum())
    budget_rel_diff = (
        abs(budget_cells_side - budget_species_side) / abs(budget_species_side)
        if budget_species_side
        else abs(budget_cells_side)
    )
    budget_note = (
        "exact to float rounding — the lists and ranges exports agree"
        if budget_rel_diff < 1e-6 and range_mismatches == 0
        else "WARNING: the exports disagree; investigate the pipeline handoff"
    )
    rarity_metrics = mo.md(f"""
    | cells above | Spearman ρ (all cells) | Jaccard overlap vs baseline |
    | --- | --- | --- |
    | top 1% ({len(top_r[0.01])}) | — | **{jaccard(top_b[0.01], top_r[0.01]):.2f}** |
    | top 5% | — | **{jaccard(top_b[0.05], top_r[0.05]):.2f}** |
    | top 10% | — | **{jaccard(top_b[0.10], top_r[0.10]):.2f}** |
    | all cells | **{rho_all:.2f}** | — |
    | baseline>0 only | **{rho_nz:.2f}** | — |

    The overlap numbers are not an error signal: the two metrics answer
    different questions (per-cell summed weight vs per-cell weight shared over
    range). The reading that matters is *which regions leave the top set* —
    the expected direction is archipelagos and other presence-rich,
    endemism-heavy places rising while breadth-dominated regions sink.

    **Conserved-budget invariant (tested, not assumed).** Σ_c R_c over all
    cells = **{budget_cells_side:,.1f}** vs Σ_s w_s once per listed species =
    **{budget_species_side:,.1f}** — relative difference {budget_rel_diff:.1e}
    ({budget_note}; {range_mismatches:,} inconsistent species ranges).
    """)
    return rarity_metrics, top_r, top_b, rho_all, rho_nz


@app.cell
def _(alt, cell_points, mo, pl, top_b, top_r):
    baseline_top = set(top_b[0.01])
    rarity_top = set(top_r[0.01])
    membership = (
        pl.when(pl.col("cell").is_in(list(baseline_top & rarity_top)))
        .then(pl.lit("both"))
        .when(pl.col("cell").is_in(list(baseline_top)))
        .then(pl.lit("baseline only"))
        .when(pl.col("cell").is_in(list(rarity_top)))
        .then(pl.lit("rarity only"))
        .otherwise(pl.lit("neither"))
        .alias("membership")
    )
    rarity_scatter = (
        cell_points.select("cell", "cell_index", "baseline", "rarity", "n_species")
        .with_columns(membership)
        .drop("cell")
    )
    ORDER = ["baseline only", "rarity only", "both", "neither"]
    COLORS = {
        "baseline only": "#2b5b8c",
        "rarity only": "#b0503f",
        "both": "#234d20",
        "neither": "#dcdcdc",
    }
    # Chart on a lean source; cell ids stay reachable through the tables.
    rarity_xy = (
        alt.Chart(rarity_scatter.select("baseline", "rarity", "membership"))
        .transform_calculate(
            bx="log10(datum.baseline + 1)",
            ry="log10(datum.rarity + 1)",
        )
        .mark_circle(size=16, opacity=0.3)
        .encode(
            x=alt.X("bx:Q", title="baseline score (log10 + 1)"),
            y=alt.Y("ry:Q", title="range-rarity score (log10 + 1)"),
            color=alt.Color(
                "membership:N",
                sort=ORDER,
                legend=alt.Legend(title="top-1% membership"),
                scale=alt.Scale(domain=ORDER, range=[COLORS[c] for c in ORDER]),
            ),
            tooltip=[
                alt.Tooltip("baseline:Q", format=".1f"),
                alt.Tooltip("rarity:Q", format=".2f"),
                alt.Tooltip("membership:N"),
            ],
        )
        .properties(width=420, height=380, title="Same pairs, two budgets")
    )
    rare_arrivals = mo.ui.table(
        rarity_scatter.filter(pl.col("membership") == "rarity only")
        .sort("rarity", descending=True)
        .head(12)
        .select("cell_index", "baseline", "rarity", "n_species")
        .with_columns(pl.col("baseline").round(1), pl.col("rarity").round(2))
        .to_dicts(),
        label="Highest rarity-only cells (sample of the new top-1%)",
    )
    mo.vstack([rarity_xy, rare_arrivals])
    return (rarity_xy,)


@app.cell
def _(alt, cell_points, pl, score_map):
    rarity_maps = alt.hconcat(
        score_map(cell_points, "baseline", "Baseline: Σ w_threat × w_DNA", "score (sqrt)"),
        score_map(cell_points, "rarity", "Range rarity: Σ w / N_s", "score (sqrt)"),
    ).configure_view(stroke=None)
    rarity_maps
    return (rarity_maps,)


@app.cell
def _(mo):
    mo.md(r"""
    ## 3. Hotspot robustness, mini version *(roadmap P0)*

    Before trusting a new score surface — or a new data source — the roadmap's
    P0 diagnostic asks how stable the top hotspots are under plausible
    alternative specifications of the *existing* model. The canonical list
    has more entries than these serving exports can express; §3 covers the
    ones the exports represent faithfully:

    - Data-Deficient treated with weight 0, 1, and 4 (reference: 2) — and per
      the roadmap, an alternative that removes a region from the top set is
      as much a finding as one that survives it;
    - alternative DNA weighing: *flat* (all four gap buckets weight 4) and
      *exact-species emphasis* (sp 4 · gen 2 · fam 1, gdd 4 unchanged);
    - range-rarity on (the §2 score becomes a specification, not a
      replacement);
    - each taxonomic class scored separately (needs the GoaT↔IUCN
      crosswalk lineage classes);
    - plus the un-augmented baseline for comparison.

    Recall of the roadmap's own framing: an alternative that shifts a region
    out of the top set is *not* a failure — each report is an honest estimate
    of how much of the headline pattern is over the model's weight choices.
    That finding is phrased that way rather than suppressed.

    §3b then lifts what this notebook had flagged as untestable: the *serving
    exports* still expose no evidence-type column and no unsimplified
    geometries, but the crosswalk carries assessment dates and
    taxonomic-name relations, `cell_boundaries` carries the RESOLVE 2017
    ecoregion membership, and the species table carries the exact/flat DNA
    strokes behind the ladder — so recent-assessments-only, exact-name-only,
    framework-cell-restricted, EDGE-cohort and the EBP-style partially-covered
    ladder all turn into live specifications in §3b instead of prose
    caveats.
    """)
    return


@app.cell
def _(cell_scores, jaccard, mo, pl, rank_set, spearman):
    alternates = [
        ("DD weight 0", "dd0"),
        ("DD weight 1", "dd1"),
        ("DD weight 4", "dd4"),
        ("Flat DNA ladder", "flat_dna"),
        ("Exact-species ladder", "exact_species"),
        ("Range rarity on", "rarity"),
    ]
    sweep_rows = []
    for label, col in alternates:
        sweep_rows.append(
            {
                "specification": label,
                "spearman_all": spearman(cell_scores, "baseline", col),
                "jaccard_1pct": jaccard(
                    rank_set(cell_scores, "baseline", 0.01), rank_set(cell_scores, col, 0.01)
                ),
                "jaccard_5pct": jaccard(
                    rank_set(cell_scores, "baseline", 0.05), rank_set(cell_scores, col, 0.05)
                ),
                "jaccard_10pct": jaccard(
                    rank_set(cell_scores, "baseline", 0.10), rank_set(cell_scores, col, 0.10)
                ),
            }
        )
    sweep_df = pl.DataFrame(sweep_rows)
    robust_members = {label: rank_set(cell_scores, col, 0.01) for label, col in alternates}
    retention = (
        pl.DataFrame({"cell": cell_scores["cell"]})
        .with_columns(
            [
                pl.col("cell").is_in(list(members)).alias(name)
                for name, members in robust_members.items()
            ]
        )
        .with_columns(
            pl.sum_horizontal([pl.col(name).cast(pl.Int8) for name in robust_members.keys()]).alias(
                "retained"
            )
        )
    )
    sweep_table = mo.ui.table(
        sweep_df.to_dicts(),
        label="Specification-level stability vs the baseline score",
    )
    return alternates, retention, robust_members, sweep_df, sweep_table


@app.cell
def _(alternates, cell_points, pl, retention, robust_members, alt):
    _n_specs = len(alternates)
    retained_hist = retention.group_by("retained").agg(n=pl.len()).sort("retained")
    _bars = (
        alt.Chart(retained_hist)
        .mark_bar(color="#2b6a9c")
        .encode(
            x=alt.X(
                "retained:O",
                title=f"top-1% under n of the {_n_specs} specifications",
                scale=alt.Scale(domain=list(range(_n_specs + 1))),
            ),
            y=alt.Y("n:Q", title="cells"),
            tooltip=[alt.Tooltip("n:Q", title="cells")],
        )
        .properties(
            width=440,
            height=240,
            title="How many cells are robust to specification choice?",
        )
        .configure_axis(labelFontSize=11, titleFontSize=12)
        .configure_title(anchor="start", fontSize=13)
    )
    _bars
    return (_bars,)


@app.cell
def _(alternates, cell_points, pl, retention, alt):
    _n_specs = len(alternates)
    retention_points = cell_points.join(
        retention.select("cell", "retained"), on="cell", how="left"
    ).select("lon", "lat", "retained")
    retention_map = (
        alt.Chart(retention_points)
        .mark_square(size=13, opacity=1.0)
        .encode(
            x=alt.X("lon:Q", scale=alt.Scale(domain=[-180, 190]), axis=None),
            y=alt.Y("lat:Q", scale=alt.Scale(domain=[-60, 85]), axis=None),
            color=alt.Color(
                "retained:O",
                scale=alt.Scale(scheme="viridis", domain=[0, _n_specs]),
                legend=alt.Legend(title="retained in top-1% (n specs)", symbolType="square"),
            ),
            tooltip=[
                alt.Tooltip("retained:Q", format="d", title="specifications passed"),
            ],
        )
        .properties(
            width=620,
            height=290,
            title="Top-1% retention across the six live specifications",
        )
        .configure_view(stroke=None)
    )
    retention_map
    return (retention_map,)


@app.cell
def _(mo, pl, retention, robust_members):
    _n_specs = len(robust_members)
    _members = [set(m) for m in robust_members.values()]
    _all_n = len(set.intersection(*_members)) if _members else 0
    _never_cells = int((retention["retained"] == 0).sum())
    _min_kept = int(retention["retained"].min())
    retention_note = mo.md(f"""
    **{_all_n:,}** cells stay top-1% under all {_n_specs} specifications;
    {_never_cells:,} appear in none of them (counts run down to {_min_kept}
    in this export). Cells with a low count are not proof-of-error
    candidates — per the roadmap framing, a hotspot that drops when the
    Data-Deficient weight moves is an informative finding — but this is
    the surface shape the roadmap asks to serve as `robustness(c)`.
    """)
    return (retention_note,)


@app.cell
def _(CROSSWALK_P, LISTS_P, SPECIES_P, WL_DNA, WL_T, con, jaccard, mo, os, pl, rank_set, top_b):
    if os.path.exists(CROSSWALK_P.replace("''", "'")):
        class_scores = con.sql(f"""
            WITH lists AS (
                SELECT h3_index AS cell, UNNEST(species_ids) AS sid FROM '{LISTS_P}'
            ),
            base AS (
                SELECT gbif_accepted_id AS sid, TRY_CAST(iucn_sis_id AS BIGINT) AS sis,
                       {WL_T}::DOUBLE AS tw,
                       {WL_DNA}::DOUBLE AS dw
                 FROM '{SPECIES_P}'
            ),
            classes AS (
                SELECT iucn_sis_id AS sis,
                       -- Same class synonym map as notebook 2: GoaT/NCBI
                       -- lineage names and title-cased IUCN fallbacks are one
                       -- clade per bucket here, otherwise ray-finned fish and
                       -- reptiles split into two specifications each.
                       CASE COALESCE(ncbi_class,
                                     upper(substr(iucn_class, 1, 1))
                                     || lower(substr(iucn_class, 2)))
                         WHEN 'Actinopteri'    THEN 'Actinopterygii'
                         WHEN 'Actinopterygii' THEN 'Actinopterygii'
                         WHEN 'Lepidosauria'   THEN 'Reptilia'
                         WHEN 'Reptilia'       THEN 'Reptilia'
                         ELSE COALESCE(ncbi_class,
                                       upper(substr(iucn_class, 1, 1))
                                       || lower(substr(iucn_class, 2)))
                       END AS class_name
                FROM '{CROSSWALK_P}'
                WHERE class_name IS NOT NULL
            ),
            pairs AS (
                SELECT lists.cell, classes.class_name, base.tw * base.dw AS w
                FROM lists
                JOIN base USING (sid)
                JOIN classes USING (sis)
                WHERE classes.class_name IS NOT NULL
            )
            SELECT cell, class_name, SUM(w)::DOUBLE AS score
            FROM pairs
            GROUP BY cell, class_name
        """).pl()
        drift = []
        for class_name in class_scores["class_name"].unique().to_list():
            frame = class_scores.filter(pl.col("class_name") == class_name)
            drift.append(
                {
                    "class": class_name,
                    "k_own": len(rank_set(frame, "score", 0.01)),
                    "jaccard_1pct": jaccard(top_b[0.01], rank_set(frame, "score", 0.01)),
                }
            )
        class_df = pl.DataFrame(
            drift, schema={"class": pl.String, "k_own": pl.Int64, "jaccard_1pct": pl.Float64}
        ).sort("jaccard_1pct")
        class_mean = float(class_df["jaccard_1pct"].mean()) if class_df.height else float("nan")
        class_panel = mo.vstack(
            [
                mo.md(f"""
            **Per-class scoring.** Each class' species are scored alone: its
            top-1% is 1% of *its own* occupied cells (`k_own` — just a handful
            of cells for narrow classes), compared against the *global*
            baseline top-1% ({len(top_b[0.01]):,} cells). Mean Jaccard is
            **{class_mean:.2f}**. Read against the size asymmetry: a small
            overlap is structurally favoured when k_own is smaller than the baseline set, so treat the
            per-class exact values as directional and compare their occupied-cell denominators explicitly.
            Compare each class specification on its own occupied-cell support
            before attributing a rank change to taxonomic composition.
            """),
                mo.ui.table(class_df.to_dicts(), label="Per-class hotspots vs global baseline"),
            ]
        )
    else:
        class_scores = None
        class_df = None
        class_mean = None
        class_panel = mo.md(
            "`iucn_goat_crosswalk.parquet` not found under "
            "`data/exports/iucn_goat_global/` — the per-class specification is skipped."
        )
    return class_df, class_mean, class_panel, class_scores


@app.cell
def _(cell_scores, mo, rank_set, top_b):
    dd0_set = rank_set(cell_scores, "dd0", 0.01)
    dd1_set = rank_set(cell_scores, "dd1", 0.01)
    dd4_set = rank_set(cell_scores, "dd4", 0.01)
    flat_set = rank_set(cell_scores, "flat_dna", 0.01)
    exact_set = rank_set(cell_scores, "exact_species", 0.01)
    rarity_set = rank_set(cell_scores, "rarity", 0.01)
    baseline = set(top_b[0.01])
    survive_weights = len(baseline.intersection(dd0_set, dd1_set, dd4_set, flat_set, exact_set))
    survive_every = len(
        baseline.intersection(dd0_set, dd1_set, dd4_set, flat_set, exact_set, rarity_set)
    )
    robustness_findings = mo.md(f"""
    **What the weight specifications say.** Of the {len(baseline)} baseline
    top-1% cells, {len(baseline & dd0_set)} survive DD→0, {len(baseline & dd1_set)} DD→1 and
    {len(baseline & dd4_set)} DD→4; **{survive_weights}** survive every weight-ladder
    specification at once (DD 0/1/4, flat DNA, exact-species), and
    **{survive_every}** of {len(baseline)} also survive range-rarity. Per the
    roadmap's own framing, a hotspot that drops when the Data-Deficient weight
    moves from 2 to 1 is an equally informative finding — and §2 already showed
    the rarity question is a different map instead of a wobble of this one.
    """)
    return (robustness_findings,)


@app.cell
def _(alt, cell_points, mo, pl, score_map, sweep_df, top_b):
    jaccard_long = sweep_df.unpivot(
        ["jaccard_1pct", "jaccard_5pct", "jaccard_10pct"],
        index=["specification"],
        variable_name="threshold",
        value_name="jaccard",
    )
    spec_order = sweep_df.sort("jaccard_1pct")["specification"].to_list()
    j_bars = (
        alt.Chart(jaccard_long)
        .mark_bar()
        .encode(
            x=alt.X("jaccard:Q", title="Jaccard vs baseline"),
            y=alt.Y("specification:N", sort=spec_order, title=None),
            color=alt.Color("threshold:N", title="top cells"),
            tooltip=["jaccard:Q"],
        )
        .properties(width=480, height=220, title="How stable is the grid under each specification?")
        .configure_axis(labelFontSize=11, titleFontSize=12)
    )
    _ladder = sweep_df.filter(pl.col("specification") != "Range rarity on")
    _min_rho = float(_ladder["spearman_all"].min())
    _min_j1 = float(_ladder["jaccard_1pct"].min())
    _rarity_rho = float(
        sweep_df.filter(pl.col("specification") == "Range rarity on")["spearman_all"].item()
    )
    retention_panel = mo.vstack(
        [
            j_bars,
            mo.md(f"""
        Read live from the sweep table above: the Data-Deficient variants and
        DNA-ladder variants have the following rank changes — across those five
        weight-ladder specifications Spearman ρ ≥ **{_min_rho:.2f}** and the
        top-1% Jaccard stays ≥ **{_min_j1:.2f}**. Range-rarity stands apart in
        the same table (ρ = **{_rarity_rho:.2f}** all cells) — not a mere
        adjustment but a distribution of the species budgets (§2). Compare the per-class specification below on its stated support.

        On Spearman: cells the rank correlation carries a large tie
        mass of identical scores; average-rank ties sit in the *lower* tail,
        while the specification changes that matter are reshuffles near the
        top — so the top-k Jaccards are the operative statistics and ρ is a
        conservative aggregate.
        """),
        ]
    )
    retention_panel
    return (retention_panel,)


@app.cell
def _(mo):
    mo.md(r"""
    ## 3b. Specifications the crosswalk and boundary columns unlock

    §3's roadmap framing said these could not be evaluated from the serving
    exports. That was itself stale: the export *does* carry the inputs for
    three of them —

    - `iucn_assessment_date` covers every crosswalk-matched species
      where available → "assessments dated 2015+" and a 10-year-half-life
      age-decay weight;
    - `taxonomic_concept_relation` classifies name matches; the
      `CURRENT_NAME_EXACT` share powers an exact-name-only rescore, and the
      unclassified remainder is a coverage question, not a weight choice;
    - `cell_boundaries[].conservation_framework` (RESOLVE 2017 terrestrial
      ecoregions, CC BY 4.0 — a biogeographic partition, *not* protected
      areas) restricts scores to ecoregion-bearing cells, an ecoregion-contact specification rather than an independent terrestrial mask;

    plus the EBP-style partially-covered DNA ladder and an EDGE-cohort
    pilot mass map. Each is scored live below and compared against the
    baseline exactly as §3 does.
    """)
    return


@app.cell
def _(CROSSWALK_P, SPECIES_P, WL_DNA, WL_T, con, mo, pl):
    relation_df = con.sql(f"""
        WITH base AS (
            SELECT gbif_accepted_id AS sid, TRY_CAST(iucn_sis_id AS BIGINT) AS sis,
                   {WL_T}::DOUBLE AS tw, {WL_DNA}::DOUBLE AS dw
            FROM '{SPECIES_P}'
        ),
        cw AS (
            SELECT iucn_sis_id AS sis, taxonomic_concept_relation AS crel
            FROM '{CROSSWALK_P}'
        )
        SELECT COALESCE(crel, '(unclassified)') AS relation,
               COUNT(*) AS species,
               ROUND(SUM(tw * dw), 1) AS weight_mass
        FROM base LEFT JOIN cw USING (sis)
        GROUP BY 1 ORDER BY weight_mass DESC
    """).pl()
    _uncl = relation_df.filter(pl.col("relation") == "(unclassified)")
    _total_mass = float(relation_df["weight_mass"].fill_null(0.0).sum())
    _uncl_mass = float(_uncl["weight_mass"].fill_null(0.0).sum()) if _uncl.height else 0.0
    cp_uncl_n = int(_uncl["species"].item() if _uncl.height else 0)
    cp_uncl_share = _uncl_mass / _total_mass if _total_mass else 0.0
    relation_panel = mo.vstack(
        [
            mo.md(f"""
        **The unclassified bucket is a coverage question, not silence.**
        {cp_uncl_n:,} species ({cp_uncl_share:.0%} of total weight mass) sit
        in the null/unclassified relation bucket — the crosswalk either resolved their
        lineage without a same-name IUCN record or has no row at all. The
        exact-only specification below scores them zero, the conservative
        reading of "only keep names we can defend"; the complementary
        reading — transfer with the synonymy/near-match evidence intact —
        belongs to the roadmap's crosswalk-validation item, not this
        notebook's weight rules.
        """),
            mo.ui.table(
                relation_df.to_dicts(),
                label="Registered weight mass by crosswalk taxonomy-concept bucket",
            ),
        ]
    )
    relation_panel
    return cp_uncl_n, cp_uncl_share, relation_panel


@app.cell
def _(cell_scores, cp_uncl_n, cp_uncl_share, jaccard, mo, pl, rank_set, spearman):
    extra_alternates = [
        ("Assessments dated 2015+", "recent15"),
        ("Assessment-age decay (10-year half-life)", "decayed"),
        ("Exact-name matches only", "exact_only"),
        ("Partially covered ladder (EBP style)", "genus_partial"),
        ("EDGE cohort mass (availability pilot)", "edge_pilot"),
        ("Ecoregion-contact score (zero outside contacts)", "framework"),
    ]
    cp_sweep_rows = []
    for _label, _col in extra_alternates:
        cp_sweep_rows.append(
            {
                "specification": _label,
                "spearman_all": spearman(cell_scores, "baseline", _col),
                "jaccard_1pct": jaccard(
                    rank_set(cell_scores, "baseline", 0.01), rank_set(cell_scores, _col, 0.01)
                ),
                "jaccard_5pct": jaccard(
                    rank_set(cell_scores, "baseline", 0.05), rank_set(cell_scores, _col, 0.05)
                ),
            }
        )
    cp_sweep_df = pl.DataFrame(cp_sweep_rows)
    cp_min_rho = float(cp_sweep_df["spearman_all"].min())
    cp_min_j = float(cp_sweep_df["jaccard_1pct"].min())
    framework_positive_cells_n = cell_scores.filter(pl.col("framework") > 0.0).height
    edge_positive_cells_n = cell_scores.filter(pl.col("edge_pilot") > 0.0).height
    cp_sweep_table = mo.ui.table(
        cp_sweep_df.to_dicts(),
        label="Crosswalk- and boundary-enabled specifications vs baseline: all selected occupied cells",
    )
    cp_panel = mo.vstack(
        [
            cp_sweep_table,
            mo.md(f"""
        Reading notes. **Exact-name-only** excludes every non-exact relation; the null/unclassified
        bucket alone contains {cp_uncl_share:.0%} of registered weight mass
        ({cp_uncl_n:,} species) — the
        honest-epistemics claim this notebook previously *asserted* could not
        be tested. **Recent-2015+** keeps only assessments 2015 onward (this is the "has anything moved since the assessments aged"
        question, answered without a re-acquisition). **Age decay** halves
        each species' weight per decade since its assessment instead of
        cutting, interpolating between the base score and a strictly
        recent-only view. **Partially covered ladder** replaces the flat
        species-gap ladder with the EBP-style categorical one —
        genus-represented species count as partially covered (weight 1),
        family-represented-only as 2 and family-absent/gdd as 4. **EDGE
        cohort mass** is deliberately a pilot: the serving layer stores no ED
        values (the ZSL lists are restricted), so the spec re-ranks with a 0/1
        availability dummy and has {edge_positive_cells_n:,} cells with positive
        pilot score — a rank-movement statement, not an EVE ranking.
        **Ecoregion-contact score** retains baseline scores on cells carrying
        ≥1 RESOLVE 2017 ecoregion and sets other cells to zero (biogeographic
        contacts — attribution and naming caveat in notebook 1 §2b).
        {framework_positive_cells_n:,} cells have positive framework score;
        neither positive-score count measures all boundary or labelled-species contacts.
        Every top set and correlation in this table uses the same
        {cell_scores.height:,} selected occupied cells, including outside-support
        zeros. Zero-score ties can enter top sets through deterministic cell-key
        tie breaking; these comparisons are not restricted to positive-score cells.
        """),
        ]
    )
    cp_panel
    return (
        cp_min_j,
        cp_min_rho,
        cp_panel,
        cp_sweep_df,
        extra_alternates,
    )


@app.cell
def _(mo):
    mo.md(r"""
    ## 3c. Statistical hygiene for every ρ and Jaccard above

    Two readings the tables above have not said yet — and both change what
    the numbers mean:

    1. **What counts as a *big* overlap?** Two *random* top-1% subsets of
       the selected cell universe share `k/(2n−k)` ≈ 0.5% of their members. The unblocked null
       below reports an approximate analytic reference and a Monte-Carlo check so
       the Jaccard columns can be read as *excess* overlap.
    2. **Cells are not independent** (the roadmap's A17 audit point).
       Neighbouring res-3 hexes share species presence, so spatially
       structured scores carry inflated reproduction confidence. Moran's I
       with a permutation reference on the baseline, the rarity score, and
       the rarity-vs-baseline residual tells whether range-rarity supplies
       *new spatial structure* or mostly re-draws contiguity the baseline
       already has.
    """)
    return


@app.cell
def _(cell_scores, con, jaccard, math, mo, rank_set, spearman):
    con.register("cs", cell_scores.to_arrow())
    _n = cell_scores.height
    _k = math.ceil(0.01 * _n)
    expected_j = _k / (2 * _n - _k)
    mc_rows = []
    for _i in range(30):
        _a = con.sql(
            f"SELECT cell FROM cs ORDER BY md5(concat(cell::VARCHAR, '{_i}-a')) LIMIT {_k}"
        ).fetchall()
        _b = con.sql(
            f"SELECT cell FROM cs ORDER BY md5(concat(cell::VARCHAR, '{_i}-b')) LIMIT {_k}"
        ).fetchall()
        mc_rows.append(jaccard({r[0] for r in _a}, {r[0] for r in _b}))
    mc_mean = float(sum(mc_rows) / len(mc_rows))
    mc_lo = float(min(mc_rows))
    mc_hi = float(max(mc_rows))

    def _rho_null(col: str) -> float:
        return float(
            con.sql(f"""
            WITH f AS (
                SELECT ROW_NUMBER() OVER (ORDER BY baseline) AS rn,
                       RANK() OVER (ORDER BY baseline)
                           + (COUNT(*) OVER (PARTITION BY baseline) - 1) / 2.0 AS r1
                FROM cs
            ),
            o AS (
                SELECT ROW_NUMBER() OVER (ORDER BY RANDOM()) AS rn,
                       RANK() OVER (ORDER BY {col})
                           + (COUNT(*) OVER (PARTITION BY {col}) - 1) / 2.0 AS r2
                FROM cs
            )
            SELECT CORR(r1, r2) FROM f JOIN o USING (rn)
        """).fetchone()[0]
        )

    _nulls = {c: _rho_null(c) for c in ("rarity", "exact_only", "genus_partial")}
    _rho_rarity = spearman(cell_scores, "baseline", "rarity")
    _j_rarity = jaccard(
        rank_set(cell_scores, "baseline", 0.01), rank_set(cell_scores, "rarity", 0.01)
    )
    null_panel = mo.vstack(
        [
            mo.md(f"""
        **Null references (tested, not assumed).** For top-1% sets (k =
        {_k:,} of n = {_n:,}) the ratio-of-expectations approximation is J ≈ **{expected_j:.4f}**
        with a 30-draw Monte Carlo comparison (mean {mc_mean:.4f}, range
        {mc_lo:.4f}–{mc_hi:.4f}; subsets drawn by salted hash over the cell
        key, so the null mirrors the ranking procedure). Permuted Spearman
        references under the existing tie structure: rarity
        {_nulls["rarity"]:+.3f}, exact_only {_nulls["exact_only"]:+.3f},
        genus_partial {_nulls["genus_partial"]:+.3f}; a single permutation is a diagnostic, not a confidence band. Applied here: the §2 headline
        baseline↔rarity J = {_j_rarity:.2f} sits ~{(_j_rarity / expected_j):.0f}×
        above this random-assignment approximation (interpret the observed overlap on the stated support), and ρ = {_rho_rarity:.2f} can be compared with these individual null draws —
        quote σ₁ [the tie-structured null] first, the excess second.
        """),
        ]
    )
    null_panel
    return (null_panel,)


@app.cell
def _(cell_scores, con, h3, mo, pl):
    # Edges of the res-3 adjacency graph restricted to occupied cells
    sorted_cells = sorted(cell_scores["cell"].to_list())
    idx_of = {c: i for i, c in enumerate(sorted(cell_scores["cell"].to_list()))}
    edge_set: set[tuple[int, int]] = set()
    for _c in list(sorted_cells):
        _s = h3.int_to_str(_c)
        for _nb in h3.grid_disk(_s, 1):
            _o = h3.str_to_int(_nb)
            if _o in idx_of and _o != _c:
                _i, _j = idx_of[_c], idx_of[_o]
                edge_set.add((_i, _j) if _i < _j else (_j, _i))
    _pairs = sorted(edge_set)
    edges = pl.DataFrame({"a": [x[0] for x in _pairs], "b": [x[1] for x in _pairs]})
    # Rarity residual on baseline: slope = cov / var (the intercept cancels
    # inside Moran's I, so only the slope is needed).
    _bm, _rm, _br, _vb = cell_scores.select(
        pl.col("baseline").mean().alias("bm"),
        pl.col("rarity").mean().alias("rm"),
        (pl.col("baseline") * pl.col("rarity")).mean().alias("br"),
        ((pl.col("baseline") - pl.col("baseline").mean()) ** 2).mean().alias("vb"),
    ).row(0)
    _slope = (_br - _bm * _rm) / _vb if _vb else 0.0
    vals = cell_scores.with_columns(
        pl.col("cell").replace_strict(idx_of).alias("ri"),
        (pl.col("rarity") - _slope * pl.col("baseline")).alias("resid"),
    )
    con.register("m_v", vals.select("ri", "baseline", "rarity", "resid").to_arrow())
    con.register("m_e", edges.to_arrow())
    _n = cell_scores.height
    S0 = 2 * edges.height

    def _moran(col: str) -> tuple[float, float]:
        if S0 == 0:
            return float("nan"), float("nan")
        _m = con.sql(f"SELECT AVG({col}) FROM m_v").fetchone()[0]
        _den = con.sql(f"SELECT SUM((({col}) - {_m}) * (({col}) - {_m})) FROM m_v").fetchone()[0]
        if not _den:
            return float("nan"), float("nan")
        _num = con.sql(f"""
            SELECT SUM((x.{col} - {_m}) * (y.{col} - {_m}))
            FROM m_e e JOIN m_v x ON x.ri = e.a JOIN m_v y ON y.ri = e.b
        """).fetchone()[0]
        _perm = []
        for _ in range(60):
            _pn = con.sql(f"""
                WITH P AS (
                    SELECT ROW_NUMBER() OVER (ORDER BY RANDOM()) - 1 AS ri, {col} AS v FROM m_v
                )
                SELECT SUM((x.v - {_m}) * (y.v - {_m}))
                FROM m_e e JOIN P x ON x.ri = e.a JOIN P y ON y.ri = e.b
            """).fetchone()[0]
            _perm.append((_n / S0) * (2 * (_pn or 0.0) / _den) if _den and S0 else 0.0)
        _i_obs = (_n / S0) * (2 * (_num or 0.0) / _den) if _den and S0 else 0.0
        _mu = sum(_perm) / len(_perm)
        _sd = (sum((p - _mu) ** 2 for p in _perm) / (len(_perm) - 1)) ** 0.5
        return _i_obs, (_i_obs - _mu) / _sd if _sd else 0.0

    moran_rows = []
    for _label, _col in [
        ("baseline", "baseline"),
        ("range-rarity", "rarity"),
        ("rarity residual", "resid"),
    ]:
        _i, _z = _moran(_col)
        moran_rows.append({"score": _label, "morans_i": _i, "permutation_z": _z})
    moran_df = pl.DataFrame(moran_rows)
    spatial_panel = mo.vstack(
        [
            mo.md(f"""
        **Spatial dependence (Moran's I on the res-3 rook graph, 60-draw
        permutation z).** {_n:,} cells, {edges.height:,} adjacency edges —
        everything reported live. Reading: the *residual* row is the one that
        matters — if rarity leaves spatially independent residuals (I near
        the −1/(n−1) reference ≈ 0), its novelty is not just a re-plotting of
        contiguity; if the residual is itself strongly clustered, the "new
        map" is still spatially entangled with the baseline's autocorrelation
        structure (block-bootstrap correction left to the canonical P0 run).
        """),
            mo.ui.table(
                moran_df.with_columns(
                    pl.col("morans_i").round(3), pl.col("permutation_z").round(1)
                ).to_dicts(),
                label="Moran's I per surface (60 permutations)",
            ),
        ]
    )
    spatial_panel
    return (spatial_panel,)


@app.cell
def _(cell_scores, class_scores, jaccard, mo, pl, rank_set, top_b):
    mo.stop(
        class_scores is None or class_scores.height == 0,
        mo.md("No resolved classes; class jackknife is unavailable."),
    )
    _pos = class_scores.pivot(on="class_name", index="cell", values="score")
    _cols = sorted(set(_pos.columns) - {"cell"})
    _pos = _pos.with_columns([pl.col(_c).fill_null(0.0) for _c in _cols])
    _universe = _pos.height
    _pos = _pos.with_columns(pl.sum_horizontal(_cols).alias("_total"))
    _ref = rank_set(_pos.select("cell", pl.col("_total").alias("score")), "score", 0.01)
    rows = []
    for _c in _cols:
        _excl = _pos.with_columns((pl.col("_total") - pl.col(_c)).alias("score"))
        rows.append(
            {
                "class": _c,
                "jaccard_vs_class_ref": jaccard(_ref, rank_set(_excl, "score", 0.01)),
                "jaccard_vs_global_baseline": jaccard(top_b[0.01], rank_set(_excl, "score", 0.01)),
            }
        )
    jack_df = pl.DataFrame(rows).sort("jaccard_vs_class_ref", descending=True)
    jack_panel = mo.vstack(
        [
            mo.md(f"""
        **Class jackknife (drop-one-class stability).** For each class, the
        cell score is recomputed *without its species* and re-ranked to its
        own top-1% (universe: the {_universe:,} cells with crosswalk-classed
        species; `_ref` is the same universe with all classes present, so
        the left column is the universe-faithful comparison and the right
        column reads against the global baseline for scale). This is the
        vocabulary-problem complement to per-class scoring above: vocabulary
        near-matches inside one class cannot manufacture a hotspot
        here, because every drop-one re-ranking uses the same synonym map.
        Rows run most-stable-first — the class whose silence rearranges the
        map most is at the bottom (lowest Jaccard).
        """),
            mo.ui.table(
                jack_df.with_columns(
                    pl.col("jaccard_vs_class_ref").round(3),
                    pl.col("jaccard_vs_global_baseline").round(3),
                ).to_dicts(),
                label="Drop-one-class stability vs the all-classes reference",
            ),
        ]
    )
    jack_panel
    return (jack_panel,)


@app.cell
def _(mo):
    mo.md(r"""
    ## 4. Stewardship fractions *(second half of the P1 item)*

    The same P1 item also asks for per-species distribution fractions: the
    share of each species' total mapped distribution that a given country,
    ADM area, or municipality holds. Species range cells are already in the
    pipeline, so this mostly becomes a join against the boundary-pair
    memberships:

    $$\text{share(species } s, \text{area } a) =
    \text{(range cells of } s \text{ inside } a) \over
    \text{(all country-contacted range cells of } s)$$

    Reading note: the shares are *cell-contact* counts, not area weights, so a
    cell where a species' polygon merely touches two countries contributes to
    both. Country-contacted cells have nonempty `admin0` membership. The serving
    country scope unions land with matching EEZs, so this denominator includes
    possible ocean-only contacts and is not a terrestrial mask. Cells without
    country-scope contacts are excluded; shares are conditional on that support.

    As a first use, this turns the dark-spot cohort — Endangered or Critically
    Endangered species on ≤ 25 res-3 cells with no species-level evidence and
    valid GoaT status — from a species list into a country-level work order.
    """)
    return


@app.cell
def _(BOUND_P, CELLS_P, SPECIES_P, con, mo, pl):
    steward = (
        con.sql(f"""
        WITH ranges AS (
            SELECT gbif_accepted_id AS sid, UNNEST(h3_indexes) AS cell FROM '{CELLS_P}'
        ),
        bounds AS (
            SELECT h3_index AS cell, UNNEST(admin0) AS iso
            FROM '{BOUND_P}' WHERE len(admin0) > 0
        ),
        country_contact AS (
            SELECT r.sid, COUNT(*) AS country_contact_cells
            FROM ranges r JOIN (SELECT DISTINCT cell FROM bounds) ub USING (cell)
            GROUP BY 1
        ),
        s2c AS (
            SELECT r.sid, b.iso, COUNT(*) AS du
            FROM ranges r JOIN bounds b USING (cell)
            GROUP BY 1, 2
        ),
        base AS (
            SELECT gbif_accepted_id AS sid, species_name, redlist_category,
                   has_dna_species_level AS has_dna, goat_data_deficient AS gdd
            FROM '{SPECIES_P}'
        ),
        sizes AS (
            SELECT gbif_accepted_id AS sid, len(h3_indexes) AS ncells FROM '{CELLS_P}'
        )
        SELECT base.species_name, s2c.iso, s2c.du, country_contact.country_contact_cells, sizes.ncells
        FROM base
        JOIN sizes ON base.sid = sizes.sid
        JOIN country_contact ON base.sid = country_contact.sid
        JOIN s2c ON base.sid = s2c.sid
        WHERE NOT base.gdd AND NOT base.has_dna
          AND base.redlist_category IN ('Critically Endangered', 'Endangered')
    """)
        .pl()
        .filter(pl.col("ncells") <= 25)
        .with_columns(
            (pl.col("du") / pl.col("country_contact_cells")).alias("country_contact_share")
        )
    )

    steward_countries = (
        steward.group_by("iso")
        .agg(
            n_dark=pl.col("species_name").n_unique(),
            n_quarter=(pl.col("country_contact_share") >= 0.25).sum(),
            n_single=(pl.col("country_contact_share") >= 0.9).sum(),
        )
        .sort("n_quarter", descending=True)
    )
    steward_species_n = steward["species_name"].n_unique()
    return (steward_countries, steward_species_n, steward)


@app.cell
def _(alt, mo, os, pl, steward, steward_countries, steward_species_n):
    _names = {}  # Country codes remain readable without a boundary catalogue.
    _top = steward_countries.head(16)
    _order = _top["iso"].to_list()
    _top_names = ", ".join(
        f"{_names.get(r['iso'], r['iso'])} ({r['n_quarter']})"
        for r in _top.head(6).iter_rows(named=True)
    )
    _single_species = steward.filter(pl.col("country_contact_share") >= 0.9)
    single_count = _single_species["species_name"].n_unique()
    _bars = (
        alt.Chart(_top)
        .mark_bar(color="#b0503f")
        .encode(
            y=alt.Y("iso:N", sort=_order, title=None),
            x=alt.X("n_quarter:Q", title="dark species with ≥ 25% country-contact share"),
            tooltip=[alt.Tooltip("n_dark:Q", title="all dark species touched")],
        )
        .properties(
            width=520,
            height=300,
            title="Where restricted threatened without recorded species evidence species live",
        )
        .configure_axis(labelFontSize=11, titleFontSize=12)
        .configure_title(anchor="start", fontSize=13)
    )
    steward_panel = mo.vstack(
        [
            mo.md(f"""
        **{steward_species_n:,}** restricted, threatened, without recorded species evidence species
        (share > 0 on country-contact cells) resolve here at country grain. Live top-6 by
        dark species with ≥ 25% country-contact share: {_top_names}. Country contacts
        can overlap, so their shares need not sum to one. {single_count:,}
        species meet the ≥90% threshold; this is a coarse contact diagnostic,
        not exclusivity or an authorized sequencing recommendation.
        """),
            _bars,
        ]
    )
    return (steward_panel,)


@app.cell
def _(BOUND_P, CELLS_P, SPECIES_P, con, mo, pl):
    # Stewardship × ecoregion contacts: how much of each dark
    # species' *country-contacted* range sits inside a RESOLVE 2017 ecoregion
    # (the conservation_framework codes the serving layer retained) versus
    # outside. Ecoregions are biogeographic units — not protected areas —
    # so this reads as "biogeographically anchored vs. un-anchored range",
    # not "protected vs. unprotected".
    fw_steward = (
        con.sql(f"""
        WITH ranges AS (
            SELECT gbif_accepted_id AS sid, UNNEST(h3_indexes) AS cell FROM '{CELLS_P}'
        ),
        fw AS (
            SELECT DISTINCT h3_index AS cell FROM '{BOUND_P}'
            WHERE len(conservation_framework) > 0
        ),
        country_contact AS (
            SELECT DISTINCT h3_index AS cell FROM '{BOUND_P}' WHERE len(admin0) > 0
        ),
        base AS (
            SELECT gbif_accepted_id AS sid, species_name, family,
                   redlist_category AS rl, has_dna_species_level AS has_dna,
                   goat_data_deficient AS gdd
            FROM '{SPECIES_P}'
        ),
        sizes AS (
            SELECT gbif_accepted_id AS sid, len(h3_indexes) AS ncells FROM '{CELLS_P}'
        ),
        fw_count AS (
            SELECT r.sid, COUNT(*) AS n_fw
            FROM ranges r JOIN fw USING (cell) JOIN country_contact USING (cell) GROUP BY 1
        ),
        country_contact_count AS (
            SELECT r.sid, COUNT(*) AS n_country_contact
            FROM ranges r JOIN country_contact USING (cell) GROUP BY 1
        )
        SELECT base.species_name, base.family,
               COALESCE(fc.n_fw, 0)::DOUBLE / lc.n_country_contact::DOUBLE AS fw_share,
               sz.ncells
        FROM base
        JOIN sizes sz ON sz.sid = base.sid
        LEFT JOIN fw_count fc ON fc.sid = base.sid
        JOIN country_contact_count lc ON lc.sid = base.sid
        WHERE NOT base.gdd AND NOT base.has_dna
          AND base.rl IN ('Critically Endangered', 'Endangered')
    """)
        .pl()
        .filter(pl.col("ncells") <= 25)
    )
    fw_steward = fw_steward.with_columns(
        pl.when(pl.col("fw_share") >= 0.8)
        .then(pl.lit("≥80% inside ecoregions"))
        .when(pl.col("fw_share") < 0.2)
        .then(pl.lit("<20% inside (mostly un-anchored)"))
        .otherwise(pl.lit("20–80% (mixed)"))
        .alias("anchorage")
    )
    _split = fw_steward.group_by("anchorage").agg(n=pl.len()).sort("n", descending=True)
    fw_panel = mo.vstack(
        [
            mo.md(f"""
        **Stewardship × ecoregion anchorage** for the {fw_steward.height:,} dark
        species with country-contacted ranges: {
                fw_steward.filter(pl.col("anchorage") == "≥80% inside ecoregions").height:,}
        have ≥ 80% of their country-contacted cells inside a RESOLVE 2017
        ecoregion boundary and {
                fw_steward.filter(
                    pl.col("anchorage") == "<20% inside (mostly un-anchored)"
                ).height:,}
        sit almost entirely outside it. Honest caveat, stated flat: a
        species being "inside ecoregions" is a statement about biogeographic
        cataloguing, not about protection status (Protected Planet data
        stays out of the serving layer). The denominator includes possible EEZ-only country contacts, while
        the numerator intersects those same contacts with retained ecoregions.
        Zero ecoregion contact stays visible; inspect the observed split rather
        than presuming terrestrial coverage. The actionable
        protection-vs-nothing split the §7 reviewer note asks for needs
        protected-area geometry (a licensed acquisition), not the
        ecoregion codes. The numbers identify wholly
        un-anchored dark species (panel below) whose priority geography is
        tied to no named bioregion.
        """),
            mo.ui.table(
                _split.to_dicts(),
                label="Dark-cohort anchorage split (share of country-contacted cells inside any ecoregion)",
            ),
            mo.accordion(
                {
                    "top families in the un-anchored share": mo.ui.table(
                        fw_steward.filter(pl.col("anchorage") == "<20% inside (mostly un-anchored)")
                        .group_by("family")
                        .agg(n=pl.len())
                        .sort("n", descending=True)
                        .head(8)
                        .to_dicts(),
                        label="Families of dark species mostly outside retained ecoregions",
                    ),
                }
            ),
        ]
    )
    fw_panel
    return (fw_panel,)


@app.cell
def _(mo):
    mo.md(r"""
    **Governance note on the tables below.** From here to the end of §4 the
    outputs are *locality-sensitive lists for CR/EN species* — the same
    precision that makes them a sequencing work order makes them usable as
    poaching target lists, across countries with different enforcement
    regimes. `DATA_POLICY.md` keeps IUCN-linked rows out of the repository
    (`NOTICE.md` records the same for EDGE); aggregates above also require derivative review; any per-species list, product snapshot or
    public serving view built from them must clear the written-permission
    and derivative-review gates in `DATA_POLICY.md` before it ships. The
    tables are therefore collapsed by default and are working-analysis
    artifacts — not publication deliverables.
    """)
    return


@app.cell
def _(mo):
    disclosure = mo.ui.switch(
        label="Show locality-sensitive per-species lists (internal analysis only)",
        value=False,
    )
    disclosure
    return (disclosure,)


@app.cell
def _(disclosure, mo, pl, steward):
    if disclosure.value:
        work_order = mo.ui.table(
            steward.filter(pl.col("country_contact_share") >= 0.9)
            .sort("du", descending=True)
            .head(12)
            .select(
                "species_name",
                "iso",
                pl.col("du").alias("country-contact cells in country"),
                pl.col("country_contact_share").round(2),
                pl.col("ncells").alias("all mapped cells"),
            )
            .to_dicts(),
            label="Sequencing work order — single-country dark species (largest first, sample)",
        )
    else:
        work_order = mo.md(
            "**Per-species work order withheld by default.** Toggle the "
            "switch above to reveal the sample; anything beyond local "
            "analysis (full tables, distributions to external audiences) "
            "needs the DATA_POLICY.md derivative review first."
        )
    work_order
    return (work_order,)


@app.cell
def _(BOUND_P, CELLS_P, SPECIES_P, con, disclosure, mo, pl):
    municipality = (
        con.sql(f"""
        WITH ranges AS (
            SELECT gbif_accepted_id AS sid, UNNEST(h3_indexes) AS cell FROM '{CELLS_P}'
        ),
        bounds AS (
            SELECT h3_index AS cell, UNNEST(municipality) AS mun
            FROM '{BOUND_P}' WHERE len(municipality) > 0
        ),
        base AS (
            SELECT gbif_accepted_id AS sid, species_name, redlist_category,
                   has_dna_species_level AS has_dna, goat_data_deficient AS gdd
            FROM '{SPECIES_P}'
        ),
        sizes AS (
            SELECT gbif_accepted_id AS sid, len(h3_indexes) AS ncells FROM '{CELLS_P}'
        )
        SELECT base.species_name, bounds.mun, sizes.ncells
        FROM base JOIN sizes ON base.sid = sizes.sid
        JOIN ranges ON base.sid = ranges.sid
        JOIN bounds ON ranges.cell = bounds.cell
        WHERE NOT base.gdd AND NOT base.has_dna
          AND base.redlist_category IN ('Critically Endangered', 'Endangered')
    """)
        .pl()
        .filter(pl.col("ncells") <= 25)
    )
    mun_rows = (
        municipality.group_by("species_name", "mun")
        .agg(pl.col("ncells").max())
        .sort("ncells")
        .head(10)
        .to_dicts()
    )
    if disclosure.value:
        _mun_ui = mo.ui.table(
            mun_rows,
            label="Municipality-level dark species (smallest ranges first, preview)",
        )
    else:
        _mun_ui = mo.md("*Withheld — locality-sensitive toggle is off*")
    municipality_panel = mo.vstack(
        [
            mo.md(f"""
        **ADM2 / municipality grain today.** {municipality.height:,.0f} cell-level
        dark-species / municipality contacts are observed in these configured inputs.
        One species–municipality pair can contribute contacts from multiple range cells.
        This panel reports contacts rather than distribution fractions. Missing
        memberships may reflect unavailable boundaries or no spatial contact;
        these files alone do not distinguish the two. A fraction requires an
        explicit denominator and boundary-coverage review. Species rows below are gated by the
        same internal-only switch as the work-order table above.
        """),
            _mun_ui,
        ]
    )
    return (mun_rows, municipality, municipality_panel)


@app.cell
def _(mo):
    mo.md(r"""
    ## 5. The components behind the score *(roadmap P2)*

    The roadmap's P2 item starts from exactly the question this section
    previews for UI presentation: two cells can carry the same score while
    being driven by opposite things — many threatened species that *already*
    have recorded qualifying evidence, a handful of species weighed heavily on
    both dimensions, or sheer richness adding many low-weight contributions.
    The closest structural precedent, Schultz et al. 2024 (reviewed bioRxiv
    preprint — treated by the roadmap as methodological precedent, not
    validated evidence for any prioritization rule), keeps its components
    separately interpretable instead of folding them into one number, and a
    conservation-genomics map should let
    users ask whether a cell is threat driven, representation-gap driven, or
    only apparently prioritised through richness.

    The presentation change is over data the serving layer already holds — the
    30-bin threat × DNA joint counts retained per coarse cell. Here the same
    structure is rebuilt from the pair lists so per-cell composition (dominant
    bin, share of score from CR/EN species, share from gap species) is exact.
    """)
    return


@app.cell
def _(LISTS_P, SPECIES_P, WL_DNA, WL_T, con, pl):
    component_bins = con.sql(f"""
        WITH lists AS (
            SELECT h3_index AS cell, UNNEST(species_ids) AS sid FROM '{LISTS_P}'
        ),
        base AS (
            SELECT gbif_accepted_id AS sid, redlist_category, goat_data_deficient,
                   family_has_dna, genus_has_dna, has_dna_species_level,
                   {WL_T}::DOUBLE AS tw,
                   {WL_DNA}::DOUBLE AS dw
            FROM '{SPECIES_P}'
        ),
        pairs AS (
            SELECT lists.cell, base.tw, base.dw
            FROM lists JOIN base USING (sid)
        )
        SELECT cell, tw, dw, COUNT(*) AS n
        FROM pairs
        GROUP BY cell, tw, dw
    """).pl()
    return (component_bins,)


@app.cell
def _(component_bins, pl):
    cell_story = (
        component_bins.with_columns((pl.col("tw") * pl.col("dw")).alias("w"))
        .group_by("cell")
        .agg(
            pl.sum("n").alias("n_species"),
            (pl.col("w") * pl.col("n")).sum().alias("score"),
            pl.when(pl.col("tw").is_in([3.0, 4.0]))
            .then(pl.col("w") * pl.col("n"))
            .otherwise(0.0)
            .sum()
            .alias("threat_hi"),
            pl.when(pl.col("dw") >= 2.0)
            .then(pl.col("w") * pl.col("n"))
            .otherwise(0.0)
            .sum()
            .alias("gap_w"),
            pl.when(pl.col("tw").is_in([3.0, 4.0]) & (pl.col("dw") >= 2.0))
            .then(pl.col("w") * pl.col("n"))
            .otherwise(0.0)
            .sum()
            .alias("both_hi"),
            (pl.col("w") * pl.col("n")).max().alias("top_bin_w"),
        )
        .with_columns(
            (pl.col("threat_hi") / pl.col("score").replace(0, None)).alias("share_threat_hi"),
            (pl.col("gap_w") / pl.col("score").replace(0, None)).alias("share_gap"),
            (pl.col("both_hi") / pl.col("score").replace(0, None)).alias("share_both"),
            (pl.col("top_bin_w") / pl.col("score").replace(0, None)).alias("share_top_bin"),
        )
        .with_columns(
            pl.when(pl.col("score") == 0)
            .then(pl.lit("zero score (shares undefined)"))
            .when((pl.col("share_top_bin") >= 0.5) & (pl.col("n_species") <= 20))
            .then(pl.lit("few weighed heavily on both"))
            .when(pl.col("share_top_bin") <= 0.15)
            .then(pl.lit("sheer richness"))
            .when(pl.col("share_threat_hi") >= 0.5)
            .then(pl.lit("threat heavy"))
            .when(pl.col("share_threat_hi") < 0.25)
            .then(pl.lit("threat-light, gap-weighted"))
            .otherwise(pl.lit("mixed"))
            .alias("driver")
        )
    )
    return (cell_story,)


@app.cell
def _(component_bins, pl):
    global_bins = (
        component_bins.group_by("tw", "dw")
        .agg(((pl.col("tw") * pl.col("dw") * pl.col("n")).sum()).alias("score"))
        .with_columns((pl.col("score") / pl.col("score").sum()).alias("global_share"))
        .sort(["dw", "tw"])
    )
    overall_score = global_bins["score"].sum()
    return (global_bins, overall_score)


@app.cell
def _(cell_story, mo, pl):
    keyed = cell_story.with_columns(pl.col("score").round(1).alias("key"))
    contrast_keys = (
        keyed.group_by("key")
        .agg(pl.col("driver").n_unique().alias("driver_variants"))
        .filter((pl.col("driver_variants") > 2) & (pl.col("key") > 0))
        .sort("key", descending=True)
        .head(5)["key"]
        .to_list()
    )
    contrast_rows = []
    for key in contrast_keys:
        for driver in keyed.filter(pl.col("key") == key)["driver"].unique().to_list()[:2]:
            row = (
                keyed.filter((pl.col("key") == key) & (pl.col("driver") == driver))
                .sort("share_top_bin")
                .head(1)
                .select(
                    "cell",
                    "score",
                    "n_species",
                    "driver",
                    "share_threat_hi",
                    "share_gap",
                    "share_top_bin",
                )
            )
            contrast_rows.extend(row.to_dicts())
    contrast_table = mo.ui.table(
        contrast_rows,
        label="Same score, different stories — cells that tie on the baseline score but build it from different bins",
    )
    return (contrast_table, contrast_keys)


@app.cell
def _(alt, global_bins, mo, overall_score, pl):
    threat_order = {0.1: "LC", 1.0: "NT", 2.0: "VU/DD", 3.0: "EN", 4.0: "CR"}
    gap_order = {2.0: "sp", 3.0: "gen", 4.0: "fam/gdd", 0.0: "sampled"}
    composition_long = global_bins.with_columns(
        pl.col("tw")
        .map_elements(lambda x: threat_order.get(x, str(x)), return_dtype=pl.String)
        .alias("threat"),
        pl.col("dw")
        .map_elements(lambda x: gap_order.get(x, str(x)), return_dtype=pl.String)
        .alias("dna bucket"),
    ).filter(pl.col("global_share") > 0)
    mo.stop(
        composition_long.height == 0,
        mo.md("No positive score mass; score composition is unavailable."),
    )
    top_bin = composition_long.sort("global_share", descending=True).head(1).to_dicts()[0]
    composition_rect = (
        alt.Chart(composition_long)
        .mark_rect()
        .encode(
            x=alt.X("threat:N", sort=["LC", "NT", "VU/DD", "EN", "CR"], title="threat weight"),
            y=alt.Y("dna bucket:N", sort=["sampled", "sp", "gen", "fam/gdd"], title="DNA bucket"),
            color=alt.Color(
                "global_share:Q", scale=alt.Scale(scheme="tealblues"), title="share of total score"
            ),
            tooltip=[alt.Tooltip("global_share:Q", format=".1%")],
        )
        .properties(width=380, height=200, title="Where does the total score mass sit?")
        .configure_axis(labelFontSize=11, titleFontSize=12)
    )
    composition_panel = mo.vstack(
        [
            composition_rect,
            mo.md(f"""
        Total score mass: **{overall_score:,.0f}** weight units. The single
        largest joint bin is **{top_bin["threat"]} × {top_bin["dna bucket"]}**
        at {top_bin["global_share"]:.0%} of all score mass — the composition
        surface the UI item asks to expose per cell.
        """),
        ]
    )
    return composition_panel


@app.cell
def _(alt, cell_points, cell_story, mo, pl):
    story_labels = cell_story
    driver_scatter = (
        alt.Chart(story_labels.select("score", "n_species", "share_top_bin", "driver"))
        .transform_calculate(sx="log10(datum.score + 1)")
        .mark_circle(size=14, opacity=0.28)
        .encode(
            x=alt.X("sx:Q", title="cell score (log10 + 1)"),
            y=alt.Y(
                "share_top_bin:Q",
                title="share of the score in the cell's largest weight bin",
                scale=alt.Scale(domain=[0, 1]),
            ),
            color=alt.Color("driver:N", title="driver profile"),
            tooltip=[
                alt.Tooltip("score:Q", format=".1f"),
                alt.Tooltip("n_species:Q", title="species"),
                alt.Tooltip("driver:N"),
            ],
        )
        .properties(width=560, height=320, title="Score size versus how concentrated it is")
        .configure_axis(labelFontSize=11, titleFontSize=12)
    )
    return (driver_scatter, story_labels)


@app.cell
def _(driver_scatter, mo, pl, story_labels):
    driver_counts = (
        story_labels.group_by("driver")
        .agg(pl.len().alias("cells"), pl.col("score").median().alias("median score"))
        .sort("median score", descending=True)
    )
    components_panel = mo.vstack(
        [
            driver_scatter,
            mo.md("""
        High scores are not one-dimensional: cells with small biggest-bin shares
        are the "sheer richness" tail the roadmap warns about, while
        "few weighed heavily on both" profiles are the opposite. The per-cell
        presentation item can adopt exactly this framing: expose the joint
        threat × DNA bins (already served), the headline counts of threatened
        and gap species, and a driver label.
        """),
            mo.ui.table(
                driver_counts.to_dicts(),
                label="Driver profiles across all mapped cells",
            ),
        ]
    )
    return (components_panel,)


@app.cell
def _(mo):
    mo.md(r"""
    The roadmap's P2 per-cell presentation wants the driver composition to be
    *mappable*, not only readable as a scatter. Prototype: each res-3 cell
    gets a two-axis colour class — rows encode the share of the cell's score
    attributable to CR/EN weight (threat axis, terciles), columns the share
    from gap-weighted species (gap axis, terciles). Hue encodes the threat
    row, shade depth the gap column, exactly the two encodings the final UI
    tile would need.
    """)
    return


@app.cell
def _(alt, cell_points, cell_story, mo, pl):
    _story = cell_story.join(
        cell_points.select("cell", "lat", "lon", "cell_index"), on="cell", how="inner"
    ).filter(pl.col("score") > 0)
    mo.stop(
        _story.height == 0, mo.md("No positive score cells; bivariate quantiles are unavailable.")
    )
    _tq = _story.select(
        pl.col("share_threat_hi").quantile(0.33).alias("t1"),
        pl.col("share_threat_hi").quantile(0.66).alias("t2"),
        pl.col("share_gap").quantile(0.33).alias("g1"),
        pl.col("share_gap").quantile(0.66).alias("g2"),
    ).row(0)
    _t1, _t2, _g1, _g2 = (float(_x) for _x in _tq)
    THREAT_BINS = ["threat-light", "threat-mid", "threat-heavy"]
    GAP_BINS = ["gap-light", "gap-mid", "gap-rich"]
    _biv = _story.with_columns(
        pl.when(pl.col("share_threat_hi") <= _t1)
        .then(pl.lit(THREAT_BINS[0]))
        .when(pl.col("share_threat_hi") <= _t2)
        .then(pl.lit(THREAT_BINS[1]))
        .otherwise(pl.lit(THREAT_BINS[2]))
        .alias("tbin"),
        pl.when(pl.col("share_gap") <= _g1)
        .then(pl.lit(GAP_BINS[0]))
        .when(pl.col("share_gap") <= _g2)
        .then(pl.lit(GAP_BINS[1]))
        .otherwise(pl.lit(GAP_BINS[2]))
        .alias("gbin"),
    ).with_columns((pl.col("tbin") + " × " + pl.col("gbin")).alias("biv"))
    _palette = (
        ["#e4eff7", "#9ec1ea", "#4f92d3"]
        + ["#f4ecd2", "#dccb85", "#b18a2e"]
        + ["#f6d5d5", "#e39b9b", "#b0503f"]
    )
    _dom = [f"{_t} × {_g}" for _t in THREAT_BINS for _g in GAP_BINS]
    bivariate_map = (
        alt.Chart(
            _biv.select(
                "lon",
                "lat",
                "biv",
                pl.col("share_threat_hi").round(3),
                pl.col("share_gap").round(3),
            )
        )
        .mark_square(size=13, opacity=1.0)
        .encode(
            x=alt.X("lon:Q", scale=alt.Scale(domain=[-180, 190]), axis=None),
            y=alt.Y("lat:Q", scale=alt.Scale(domain=[-60, 85]), axis=None),
            color=alt.Color(
                "biv:N",
                scale=alt.Scale(domain=_dom, range=_palette),
                legend=alt.Legend(
                    title="score mix: threat row × gap column (terciles)",
                    symbolType="square",
                ),
            ),
            tooltip=[
                alt.Tooltip("share_threat_hi:Q", format=".0%", title="threat share"),
                alt.Tooltip("share_gap:Q", format=".0%", title="gap share"),
            ],
        )
        .properties(
            width=600,
            height=310,
            title="Bivariate prototype: threat mix × representation-gap mix",
        )
        .configure_view(stroke=None)
    )
    bivariate_map
    return (bivariate_map,)


@app.cell
def _(mo):
    mo.md(r"""
    ## 5b. Geography-side cuts the cells already support

    Three questions that do not need new acquisition, only the cell table:

    - **Latitudinal gradient** of richness and of DNA coverage per cell —
      the "sequencing follows temperate effort" test in one groupby, tying
      into the roadmap's positioning thread (Miraldo 2016 → Theodoridis
      2020 → Schultz 2024);
    - **Grain sensitivity (mini-MAUP)** — parent-agggregating the same
      pairs to res-2 and re-ranking, plus an honest pointer at the res-7
      store where the canonical grain lives;
    - **Named-region panels** — Coral Triangle (marine), Madagascar vs
      tropical Andes (stewardship contrast), and one archipelago — the
      legibility cards biologists actually read first.
    """)
    return


@app.cell
def _(alt, cell_points, pair_level, mo, pl):
    _cellcov = pair_level.group_by("cell").agg(
        pl.len().alias("n_sp"),
        pl.when((~pl.col("goat_dd")) & pl.col("has_dna").is_not_null())
        .then(pl.col("has_dna").cast(pl.Float64))
        .mean()
        .alias("dna_share"),
    )
    _g = (
        cell_points.select("cell", "lat")
        .join(_cellcov, on="cell", how="inner")
        .with_columns(((pl.col("lat") // 5).cast(pl.Int64) * 5).alias("lat_band"))
        .group_by("lat_band")
        .agg(
            cells=pl.len(),
            richness=pl.col("n_sp").sum(),
            mean_dna_share=(pl.col("dna_share").mean() * 100),
        )
        .sort("lat_band")
    )
    _rich = (
        alt.Chart(_g)
        .mark_bar(color="#2b6a9c")
        .encode(
            x=alt.X("lat_band:O", title="5° latitude band (cell centre)"),
            y=alt.Y("richness:Q", title="species-cell pairs"),
            tooltip=[
                alt.Tooltip("cells:Q", title="cells"),
                alt.Tooltip("richness:Q", title="species (pairs)"),
            ],
        )
        .properties(width=560, height=170, title="Richness by latitude band")
    )
    _dna = (
        alt.Chart(_g)
        .mark_line(color="#b0503f", point=True)
        .encode(
            x=alt.X("lat_band:O"),
            y=alt.Y("mean_dna_share:Q", title="% species-level DNA in cell, mean"),
            tooltip=[alt.Tooltip("mean_dna_share:Q", format=".1f")],
        )
        .properties(
            width=560, height=150, title="…and the recorded species evidence the same cells show"
        )
    )
    geography_panel = mo.vstack([_rich, _dna])
    geography_panel
    return (geography_panel,)


@app.cell
def _(cell_scores, h3, jaccard, mo, pl, rank_set, spearman):
    _parent = {
        _c: h3.str_to_int(h3.cell_to_parent(h3.int_to_str(_c), 2))
        for _c in cell_scores["cell"].to_list()
    }
    _g2 = cell_scores.with_columns(pl.col("cell").replace_strict(_parent).alias("parent"))
    _agg = (
        _g2.group_by("parent")
        .agg(
            pl.col("baseline").sum().alias("baseline"),
            pl.col("rarity").sum().alias("rarity"),
            pl.len().alias("kids"),
        )
        .rename({"parent": "cell"})
    )

    def _share_top(frame, col: str, share: float) -> float:
        _total = frame[col].sum()
        return (
            float(
                frame.filter(pl.col("cell").is_in(list(rank_set(frame, col, share))))[col].sum()
                / _total
            )
            if _total
            else float("nan")
        )

    res3_j = jaccard(rank_set(cell_scores, "baseline", 0.01), rank_set(cell_scores, "rarity", 0.01))
    res3_rho = spearman(cell_scores, "baseline", "rarity")
    res3_conc = _share_top(cell_scores, "rarity", 0.01)
    res2_j = jaccard(rank_set(_agg, "baseline", 0.01), rank_set(_agg, "rarity", 0.01))
    res2_rho = spearman(_agg, "baseline", "rarity")
    res2_conc = _share_top(_agg, "rarity", 0.01)
    maup_rows = [
        {
            "grain": "res-3 (exports)",
            "cells": cell_scores.height,
            "top-1% J(baseline, rarity)": res3_j,
            "Spearman ρ": res3_rho,
            "top-1% rarity concentration": res3_conc,
        },
        {
            "grain": "res-2 (parent aggregate)",
            "cells": _agg.height,
            "top-1% J(baseline, rarity)": res2_j,
            "Spearman ρ": res2_rho,
            "top-1% rarity concentration": res2_conc,
        },
    ]
    maup_panel = mo.vstack(
        [
            mo.ui.table(
                pl.DataFrame(maup_rows)
                .with_columns(
                    pl.col("top-1% J(baseline, rarity)").round(3),
                    pl.col("Spearman ρ").round(3),
                    pl.col("top-1% rarity concentration").round(3),
                )
                .to_dicts(),
                label="Grain sensitivity: same pairs, re-aggregated one hex-level up",
            ),
            mo.md(f"""
        Reading note: this IS the coarse end of the MAUP curve — res-3 vs
        its res-2 parents. The fine end (res-5, res-7) needs the external
        res-7 store: {h3.get_num_cells(7) / 1_000_000:.0f} M res-7 cells /
        a different set of memberships,
        reached through `just global-res7-aggregate` — the canonical-grain
        build the roadmap already owns. Until then this panel previews the
        *direction* of grain effects: whether baseline↔rarity overlap grows
        or shrinks as grain coarsens, and how concentrated the rarity budget
        is at each level.
        """),
        ]
    )
    maup_panel
    return (maup_panel,)


@app.cell
def _(cell_points, cell_story, mo, pl):
    REGIONS = [
        ("Coral Triangle", (-10.0, 15.0), (92.0, 140.0)),
        ("Madagascar", (-27.0, -11.0), (42.0, 51.0)),
        ("Andes (northern)", (-10.0, 5.0), (-82.0, -64.0)),
        ("Hawaiian archipelago", (18.0, 24.0), (-162.0, -152.0)),
    ]
    _story = cell_story.select(
        "cell", "driver", "share_threat_hi", "share_gap", "n_species", "score"
    )
    _pts = cell_points.join(_story, on="cell", how="left")
    _rows = []
    for _name, _lat, _lon in REGIONS:
        _r = _pts.filter(
            (pl.col("lat") >= _lat[0])
            & (pl.col("lat") <= _lat[1])
            & (pl.col("lon") >= _lon[0])
            & (pl.col("lon") <= _lon[1])
        )
        _dom = (
            _r.filter(pl.col("driver").is_not_null())
            .group_by("driver")
            .agg(pl.len())
            .sort("len", descending=True)
        )
        _baseline = _r["baseline"].filter(_r["baseline"].is_finite())
        _gap = _r["share_gap"].filter(_r["share_gap"].is_finite())
        _rows.append(
            {
                "region": _name,
                "cells": _r.height,
                "species (pairs)": int(_r["n_species"].sum() or 0),
                "baseline observations": len(_baseline),
                "mean baseline": _baseline.mean(),
                "defined gap observations": len(_gap),
                "mean gap share": _gap.mean(),
                "dominant driver": _dom.head(1)["driver"].item() if _dom.height else "—",
            }
        )
    case_panel = mo.vstack(
        [
            mo.md("""
        Named-region panels use didactic rectangles rather than official
        polygons. Compare these exploratory cuts without assuming the
        ordering or composition of their results. Pull the numbers straight from the cells
        above so they update with each export.
        Means use the reported finite-observation counts; a mean with no
        defined observations is unavailable (null), not measured zero.
        Gap shares are defined only for positive-score cells under these
        nonnegative weight rules. Empty rectangles indicate no selected cells,
        not biological absence.
        """),
            mo.ui.table(_rows, label="Region snapshots at res-3"),
        ]
    )
    case_panel
    return (case_panel,)


@app.cell
def _(cp_min_j, cp_min_rho, EXPORT_STAMP, mo):
    mo.md(f"""
    ## 6. Reading the results of this run
    Input identity: {EXPORT_STAMP}.

    Range-rarity distributes each species' weight over its declared mapped
    cells. Compare its ranks with the baseline, and inspect the independent
    range/list conservation check before trusting that comparison.
    Specification retention reports sensitivity to recorded assumptions;
    it does not validate either surface or establish a conservation benefit.
    The crosswalk-enabled sweep has minimum top-1% overlap {cp_min_j:.2f}
    and minimum defined rank correlation {cp_min_rho:.2f} for these inputs.

    Country stewardship uses country-scope contacts, including possible EEZ
    contacts and overlapping countries,
    not area-weighted responsibility. Zero ecoregion coverage stays in the
    country-contacted cohort; ecoregions do not measure legal protection. Every
    derived table and aggregate stays local pending DATA_POLICY.md review.

    Composition bins describe score arithmetic. The gap share is one for
    positive scores under the current sampled-weight-zero ladder, so that
    axis is not independent evidence of genomic neglect. Parent aggregation
    adds child-cell budgets and can repeat species; it is not a deduplicated
    res-2 species score or a validation of fine-resolution behavior.
    """)
    return


if __name__ == "__main__":
    app.run()
