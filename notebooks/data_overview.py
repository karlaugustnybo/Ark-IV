# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "marimo>=0.19",
#     "altair>=6,<7",
#     "duckdb>=1.5,<2",
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

    return alt, configured_inputs, duckdb, h3, mo, os, pl


@app.cell
def _(EXPORT_STAMP, mo):
    mo.md(f"""
    # What is actually in the Ark-IV global exports?

    A plain look at the read-only serving exports: how many rows and cells,
    which fields are populated, how the identifier joins cover the species
    table, and the basic distributions (Red List, DNA evidence, cell
    richness, mapped range size, ecosystem systems, families). No scoring,
    no modelling, just a sense of the data before any analysis.

    Inputs are local files selected by environment; the class columns come from the retained
    IUCN↔GoaT crosswalk under `data/exports/iucn_goat_global/`. Every claim
    below is computed live against the consumed input identity
    **{EXPORT_STAMP}** (SHA-256 of the consumed files; not a provider release date) — nothing is copied from a
    previous run.
    """)
    return


@app.cell
def _(configured_inputs):
    inputs = configured_inputs()
    EXPORTS = inputs.exports
    SPECIES_P = f"{EXPORTS}/species.parquet"
    SYSTEMS_P = f"{EXPORTS}/species_systems.parquet"
    CELLS_P = f"{EXPORTS}/species_cells_res3.parquet"
    LISTS_P = f"{EXPORTS}/cell_species_lists.parquet"
    BOUND_P = f"{EXPORTS}/cell_boundaries.parquet"
    CROSSWALK_P = inputs.crosswalk
    return BOUND_P, CELLS_P, CROSSWALK_P, LISTS_P, SPECIES_P, SYSTEMS_P, inputs


@app.cell
def _(inputs):
    EXPORT_STAMP = inputs.identity
    return (EXPORT_STAMP,)


@app.cell
def _(BOUND_P, CELLS_P, LISTS_P, SPECIES_P, SYSTEMS_P, duckdb, mo, pl):
    _sizes = []
    for label, path in [
        ("species (rows)", SPECIES_P),
        ("species-systems (pairs)", SYSTEMS_P),
        ("species with mapped ranges", CELLS_P),
        ("res-3 cells with species lists", LISTS_P),
        ("res-3 cells with boundary memberships", BOUND_P),
    ]:
        _n = duckdb.sql(f"SELECT COUNT(*) FROM '{path}'").fetchone()[0]
        _sizes.append({"dataset": label, "rows": _n})
    _sizes = pl.DataFrame(_sizes)
    mo.md(f"""
    ## 1. Volumes

    {mo.ui.table(_sizes.to_dicts(), label="Row counts across the serving exports")}
    """)
    return


@app.cell
def _(SPECIES_P, alt, duckdb, mo, pl):
    _cov = duckdb.sql(f"""
        SELECT * FROM (SELECT
            COUNT(*) FILTER (species_name IS NOT NULL) AS name,
            COUNT(*) FILTER (family IS NOT NULL) AS family,
            COUNT(*) FILTER (redlist_category IS NOT NULL) AS redlist,
            COUNT(*) FILTER (iucn_assessment_id IS NOT NULL) AS assessment_id,
            COUNT(*) FILTER (gbif_taxon_id IS NOT NULL) AS gbif_link,
            COUNT(*) FILTER (goat_taxon_id IS NOT NULL) AS goat_link,
            COUNT(*) FILTER (edge_group_name IS NOT NULL) AS edge_group,
            COUNT(*) FILTER (has_dna_species_level = TRUE) AS species_dna,
            COUNT(*) FILTER (genus_has_dna = TRUE) AS genus_dna,
            COUNT(*) FILTER (family_has_dna = TRUE) AS family_dna,
            COUNT(*) FILTER (goat_data_deficient = TRUE) AS goat_missing
        FROM '{SPECIES_P}')
        UNPIVOT (n FOR label IN (name, family, redlist, assessment_id, gbif_link, goat_link, edge_group, species_dna, genus_dna, family_dna, goat_missing))
    """).pl()
    _total = duckdb.sql(f"SELECT COUNT(*) FROM '{SPECIES_P}'").fetchone()[0]
    _cov = _cov.with_columns((pl.col("n") / _total * 100).alias("pct"))
    _bar = (
        alt.Chart(_cov)
        .mark_bar()
        .encode(
            y=alt.Y("label:N", sort=list(_cov["label"]), title=None),
            x=alt.X("pct:Q", title=f"% of the {_total:,} species rows"),
            color=alt.value("#2b6a9c"),
            tooltip=[
                alt.Tooltip("n:Q", format="d", title="species"),
                alt.Tooltip("pct:Q", format=".1f", title="% of rows"),
            ],
        )
    )
    _lab = (
        alt.Chart(_cov)
        .mark_text(dx=6, align="left", fontSize=11)
        .encode(
            y=alt.Y("label:N", sort=list(_cov["label"]), title=None),
            x=alt.X("pct:Q"),
            text=alt.Text("pct:Q", format=".1f"),
        )
    )
    mo.vstack(
        [
            mo.md(r"""
        ## 2. How complete is each species record?
        """),
            (_bar + _lab)
            .properties(
                width=600, height=330, title="Share of species rows carrying each field or state"
            )
            .configure_axis(labelFontSize=11, titleFontSize=12),
            mo.md(r"""
        Reading order: inspect the computed identity-field shares (see the computed bars for the available cohort),
        the two external links (`gbif`, `goat`) are incomplete by design —
        unmatched keys get omitted rather than guessed — and the three DNA
        flags are evidence states, where the association rules differ at
        each level (compare each recorded evidence level independently).
        """),
        ]
    )
    return


@app.cell
def _(BOUND_P, CROSSWALK_P, SPECIES_P, duckdb, mo, pl):
    _total = duckdb.sql(f"SELECT COUNT(*) FROM '{SPECIES_P}'").fetchone()[0]
    _ebp = duckdb.sql(
        f"SELECT COUNT(*) FROM '{SPECIES_P}' WHERE has_ebp_criteria_evidence = TRUE"
    ).fetchone()[0]
    _ebb = duckdb.sql(
        f"SELECT COUNT(*) FROM '{SPECIES_P}' WHERE edge_group_name IS NOT NULL"
    ).fetchone()[0]
    _cw = duckdb.sql(f"""
        SELECT COALESCE(taxonomic_concept_relation, '(no crosswalk row / null relation)')
                 AS concept,
               COUNT(*) AS n
        FROM '{CROSSWALK_P}'
        GROUP BY 1 ORDER BY n DESC
    """).pl()
    _cw_rows = duckdb.sql(f"SELECT COUNT(*) FROM '{CROSSWALK_P}'").fetchone()[0]
    _dated = duckdb.sql(f"SELECT COUNT(iucn_assessment_date) FROM '{CROSSWALK_P}'").fetchone()[0]
    _yr = duckdb.sql(
        f"SELECT MIN(iucn_year_published), MAX(iucn_year_published) FROM '{CROSSWALK_P}'"
    ).fetchone()
    _fw = duckdb.sql(
        f"SELECT COUNT(*) FILTER (len(conservation_framework) > 0) FROM '{BOUND_P}'"
    ).fetchone()[0]
    _cells = duckdb.sql(f"SELECT COUNT(*) FROM '{BOUND_P}'").fetchone()[0]
    _mun = duckdb.sql(
        f"SELECT COUNT(*) FILTER (len(municipality) > 0) FROM '{BOUND_P}'"
    ).fetchone()[0]
    _cw_tab = mo.ui.table(
        _cw.to_dicts(), label="Taxonomic-concept buckets in the IUCN↔GoaT crosswalk"
    )
    mo.vstack(
        [
            mo.md(r"""
        ## 2b. Flags the notebooks do not touch yet (and what they imply)
        """),
            mo.md(f"""
        **`has_ebp_criteria_evidence` — {_ebp:,} species ({_ebp / _total:.1%}).** Written by
        `ark_pipeline/builders/species_metadata.py` from the GoaT
        `ebp_standard_criteria` field (criteria 6.7 / 6.C) gated on the
        crosswalk's `safe_for_automatic_species_trait_transfer` — i.e. it is a
        GoaT-sourced criteria-evidence flag, not a verified quality standard
        or a statement that the organism is already a planned target. It is
        what makes the EBP × dark-cohort crossing in
        `range_dna_investigation.py` §5 possible.
        **`edge_group_name` — {_ebb:,} rows** (EDGE-of-Existence cohort labels,
        gated to ZSL licence limits), the input for notebook 3's EDGE pilot.
        **`conservation_framework[]`** — RESOLVE Ecoregions 2017 ecoregion
        codes (CC BY 4.0, Dinerstein et al. 2017; attribution in
        `NOTICE.md`), {_fw:,} of {_cells:,} res-3 cells carry at least one
        ecoregion code. *Naming caveat:* these are biogeographic ecoregion
        boundaries, **not** protected areas (Protected Planet data remains
        excluded); "framework-bearing cell" means "inside one of the retained
        terrestrial partitions".
        **Municipality grain:** {_mun:,} of {_cells:,} res-3 cells in this
        export carry ADM2 contact membership ({_mun / _cells:.1%}). Stewardship
        analyses must use the observed memberships and review boundary coverage;
        an empty membership does not distinguish missing boundaries from no contact.
        """),
            mo.md(f"""
        **Crosswalk rows and registered species use different denominators.**
        The crosswalk contains {_cw_rows:,} rows; {_dated:,} have dates,
        with retained publication years {_yr[0]}–{_yr[1]}. These are crosswalk
        row counts, not the number of registered species matched. The joined
        class-lineage coverage below uses the species-register denominator.
        Null concept relations do not prove that a crosswalk row is absent.
        """),
            _cw_tab,
        ]
    )
    return


@app.cell
def _(SPECIES_P, alt, duckdb):
    RL_ORDER = [
        "Least Concern",
        "Near Threatened",
        "Vulnerable",
        "Endangered",
        "Critically Endangered",
        "Data Deficient",
        "Extinct",
        "Extinct in the Wild",
    ]
    RL_COLORS = {
        "Least Concern": "#59b9dd",
        "Near Threatened": "#ffcc00",
        "Vulnerable": "#f28021",
        "Endangered": "#d13837",
        "Critically Endangered": "#ac2e44",
        "Data Deficient": "#d7d6d6",
        "Extinct": "#331616",
        "Extinct in the Wild": "#43193b",
    }
    _rl = duckdb.sql(f"""
        SELECT redlist_category AS category, COUNT(*) AS n
        FROM '{SPECIES_P}'
        GROUP BY 1 ORDER BY n DESC
    """).pl()
    _bar = (
        alt.Chart(_rl)
        .mark_bar()
        .encode(
            y=alt.Y("category:N", sort=list(_rl["category"]), title=None),
            x=alt.X("n:Q", title="species"),
            color=alt.Color(
                "category:N",
                legend=None,
                scale=alt.Scale(
                    domain=[c for c in RL_ORDER if c in set(_rl["category"])],
                    range=[RL_COLORS[c] for c in RL_ORDER if c in set(_rl["category"])],
                ),
            ),
            tooltip=[alt.Tooltip("n:Q", title="species")],
        )
    )
    _lab = (
        alt.Chart(_rl)
        .mark_text(dx=6, align="left", fontSize=11)
        .encode(
            y=alt.Y("category:N", sort=list(_rl["category"]), title=None),
            x=alt.X("n:Q"),
            text=alt.Text("n:Q", format=","),
            color=alt.value("#333"),
        )
    )
    (_bar + _lab).properties(
        width=600, height=260, title="IUCN Red List categories across all registered species"
    ).configure_axis(labelFontSize=11, titleFontSize=12).configure_title(
        anchor="start", fontSize=13
    )
    return


@app.cell
def _(SPECIES_P, alt, duckdb):
    _levels = duckdb.sql(f"""
        SELECT 'family covered' AS label, COUNT(*) FILTER (family_has_dna = TRUE) AS n
        FROM '{SPECIES_P}'
        UNION ALL SELECT 'genus covered', COUNT(*) FILTER (genus_has_dna = TRUE)
        FROM '{SPECIES_P}'
        UNION ALL SELECT 'own species evidence', COUNT(*) FILTER (has_dna_species_level = TRUE)
        FROM '{SPECIES_P}'
        UNION ALL SELECT 'GoaT evidence unknown', COUNT(*) FILTER (goat_data_deficient = TRUE)
        FROM '{SPECIES_P}'
    """).pl()
    _order = ["family covered", "genus covered", "own species evidence", "GoaT evidence unknown"]
    _bar = (
        alt.Chart(_levels)
        .mark_bar(color="#3b8475")
        .encode(
            x=alt.X("label:N", sort=_order, title=None, axis=alt.Axis(labelAngle=-20)),
            y=alt.Y("n:Q", title="species"),
            tooltip=[alt.Tooltip("n:Q", title="species")],
        )
    )
    _lab = (
        alt.Chart(_levels)
        .mark_text(dy=-8, fontSize=12)
        .encode(
            x=alt.X("label:N", sort=_order, title=None),
            y=alt.Y("n:Q"),
            text=alt.Text("n:Q", format=","),
            color=alt.value("#222"),
        )
    )
    _levels_total = duckdb.sql(f"SELECT COUNT(*) FROM '{SPECIES_P}'").fetchone()[0]
    (_bar + _lab).properties(
        width=600, height=280, title=f"The genomic evidence ladder (species, n = {_levels_total:,})"
    ).configure_axis(labelFontSize=11, titleFontSize=12).configure_title(
        anchor="start", fontSize=13
    )
    return


@app.cell
def _(CROSSWALK_P, SPECIES_P, duckdb, mo):
    _j = duckdb.sql(f"""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (c.ncbi_class IS NOT NULL) AS goat_class,
               COUNT(*) FILTER (COALESCE(c.ncbi_class, c.iucn_class) IS NOT NULL) AS any_class
        FROM '{SPECIES_P}' s
        LEFT JOIN '{CROSSWALK_P}' c ON c.iucn_sis_id = TRY_CAST(s.iucn_sis_id AS BIGINT)
    """).fetchone()
    mo.md(f"""
    **Class lineage**: of {_j[0]:,} species rows, **{_j[1]:,}** carry a GoaT
    lineage class from the retained IUCN↔GoaT crosswalk ({_j[1] / _j[0]:.0%});
    with the crosswalk's own IUCN class as fallback the share is
    {_j[2] / _j[0]:.0%}. The {_j[0] - _j[2]:,} remaining rows have no resolved class in the
    joined crosswalk — keep that in mind for clade-driven analyses.
    """)
    return


@app.cell
def _(LISTS_P, alt, duckdb, h3, mo, pl):
    _rich = duckdb.sql(f"""
        SELECT len(species_ids) AS n
        FROM '{LISTS_P}'
    """).pl()
    _stats = duckdb.sql(f"""
        SELECT min(n) AS lo,
               quantile_cont(n, 0.25) AS p25,
               quantile_cont(n, 0.5) AS med,
               quantile_cont(n, 0.75) AS p75,
               quantile_cont(n, 0.95) AS p95,
               max(n) AS hi,
               count(*) AS cells
        FROM (SELECT len(species_ids) AS n FROM '{LISTS_P}')
    """).fetchone()
    _b = (
        _rich.with_columns(
            pl.when(pl.col("n") <= 50)
            .then(pl.lit("1–50"))
            .when(pl.col("n") <= 200)
            .then(pl.lit("51–200"))
            .when(pl.col("n") <= 500)
            .then(pl.lit("201–500"))
            .when(pl.col("n") <= 1000)
            .then(pl.lit("501–1,000"))
            .when(pl.col("n") <= 2000)
            .then(pl.lit("1,001–2,000"))
            .otherwise(pl.lit("> 2,000"))
            .alias("bucket"),
        )
        .group_by("bucket")
        .agg(n=pl.len())
        .select(
            pl.col("bucket"),
            pl.col("n"),
            pl.col("bucket")
            .replace_strict(
                {
                    "1–50": 0,
                    "51–200": 1,
                    "201–500": 2,
                    "501–1,000": 3,
                    "1,001–2,000": 4,
                    "> 2,000": 5,
                }
            )
            .alias("i"),
        )
        .sort("i")
    )
    _bar = (
        alt.Chart(_b)
        .mark_bar(color="#456f8c")
        .encode(
            x=alt.X("bucket:N", sort=list(_b["bucket"]), title="species per res-3 cell"),
            y=alt.Y("n:Q", title="cells"),
            tooltip=[alt.Tooltip("n:Q", title="cells")],
        )
    )
    _lab = (
        alt.Chart(_b)
        .mark_text(dy=-6, fontSize=11)
        .encode(
            x=alt.X("bucket:N", sort=list(_b["bucket"]), title=None),
            y=alt.Y("n:Q"),
            text=alt.Text("n:Q", format=","),
            color=alt.value("#333"),
        )
    )
    _cellh3 = duckdb.sql(f"""
        SELECT COUNT(*) AS cells FROM '{LISTS_P}'
    """).fetchone()[0]
    H3_GRID = h3.get_num_cells(3)
    H3_AREA = float(h3.average_hexagon_area(3, unit="km^2"))
    H3_EDGE = float(h3.average_hexagon_edge_length(3, unit="km"))
    _pct = _cellh3 / H3_GRID
    mo.vstack(
        [
            mo.md(r"""
        ## 3. How rich is a cell?
        """),
            (_bar + _lab)
            .properties(
                width=600,
                height=280,
                title=f"Species counts across the {_cellh3:,} occupied res-3 cells",
            )
            .configure_axis(labelFontSize=11, titleFontSize=12)
            .configure_title(anchor="start", fontSize=13),
            mo.md(
                f"Distribution of species per res-3 cell (n = {_cellh3:,}): "
                f"min {_stats[0]:,}, p25 {_stats[1]:,.0f}, median {_stats[2]:,.0f}, "
                f"p75 {_stats[3]:,.0f}, p95 {_stats[4]:,.0f}, max {_stats[5]:,}. "
                f"For scale: the complete res-3 layer over the sphere has {H3_GRID:,} "
                f"cells (average hexagon {H3_AREA:,.0f} km², {2 * H3_EDGE:,.0f} km "
                f"vertex-to-vertex), so {_pct:.1%} of them carry at least one species "
                f"in the export. Careful with that share: the res-3 grid is *not* a "
                "land grid — the any-touch rule places coastal and aquatic range "
                "polygons on hexes over open water too, so occupied cells include "
                "ocean hexes touched by mapped species. This number says the export "
                "covers this fraction of the sphere's coarse grid; occupancy on land is a different "
                "quantity (notebook 3's stewardship cut keeps country-scope contacts, "
                "including possible EEZ contacts; it is not a terrestrial mask)."
            ),
        ]
    )
    return


@app.cell
def _(BOUND_P, CROSSWALK_P, LISTS_P, SPECIES_P, SYSTEMS_P, duckdb, h3, mo, os):
    _names = {}  # Country codes remain readable without a boundary catalogue.
    _top = duckdb.sql(f"""
        WITH ranked AS (
            SELECT h3_index, len(species_ids) AS n
            FROM '{LISTS_P}'
            ORDER BY n DESC LIMIT 100
        ),
        sids AS (
            SELECT t.h3_index, ls.sid
            FROM ranked t
            JOIN (SELECT h3_index, UNNEST(species_ids) AS sid FROM '{LISTS_P}') ls
              USING (h3_index)
        ),
        sys_counts AS (
            SELECT s.h3_index,
                   count(DISTINCT s.sid) FILTER (sp.system = 'Freshwater') AS freshwater,
                   count(DISTINCT s.sid) FILTER (sp.system = 'Marine') AS marine,
                   count(DISTINCT s.sid) FILTER (sp.system = 'Terrestrial') AS terrestrial
            FROM sids s
            LEFT JOIN '{SYSTEMS_P}' sp ON sp.gbif_accepted_id = s.sid
            GROUP BY 1
        )
        SELECT t.h3_index, t.n, b.admin0,
               sy.freshwater, sy.marine, sy.terrestrial
        FROM ranked t
        LEFT JOIN sys_counts sy USING (h3_index)
        LEFT JOIN '{BOUND_P}' b USING (h3_index)
        ORDER BY t.n DESC
    """).fetchall()

    _class_strs = {}
    if os.path.exists(CROSSWALK_P.replace("''", "'")):
        _ids = ", ".join(f"({int(r[0])})" for r in _top)
        _class_strs = dict(
            duckdb.sql(f"""
            WITH sids AS (
                SELECT d.h3_index, ls.sid
                FROM (VALUES {_ids}) d(h3_index)
                JOIN (SELECT h3_index, UNNEST(species_ids) AS sid FROM '{LISTS_P}') ls
                  USING (h3_index)
            ),
            classes AS (
                SELECT s.h3_index,
                       COALESCE(c.ncbi_class,
                                upper(substr(c.iucn_class, 1, 1))
                                || lower(substr(c.iucn_class, 2))) AS class,
                       count(*) AS k
                FROM sids s
                JOIN '{SPECIES_P}' ms ON ms.gbif_accepted_id = s.sid
                LEFT JOIN '{CROSSWALK_P}' c ON c.iucn_sis_id = TRY_CAST(ms.iucn_sis_id AS BIGINT)
                WHERE COALESCE(c.ncbi_class, c.iucn_class) IS NOT NULL
                GROUP BY 1, 2
                HAVING count(*) >= 50
            )
            SELECT h3_index,
                   string_agg(class || ': ' || k, ' · ' ORDER BY k DESC)
            FROM classes
            GROUP BY 1
        """).fetchall()
        )
    _rows = [
        {
            "res-3 cell": h3.int_to_str(int(r[0])),
            "species": int(r[1]),
            "countries": ", ".join(_names.get(c, c) for c in r[2]),
            "freshwater": int(r[3]),
            "marine": int(r[4]),
            "terrestrial": int(r[5]),
            "classes": _class_strs.get(int(r[0])),
        }
        for r in _top
    ]
    _countries = sorted({c for r in _top for c in r[2]})
    _note = (
        "Class counts of ≥50 species per cell come from the GoaT↔IUCN crosswalk"
        " (`ncbi_class`, `iucn_class` fallback)."
        if os.path.exists(CROSSWALK_P.replace("''", "'"))
        else "GoaT↔IUCN crosswalk missing — classes column is blank."
    )
    mo.vstack(
        [
            mo.md(f"""
    The {len(_top)} richest res-3 cells reach up to {int(_top[0][1]):,}
    species each — {", ".join(_names.get(c, c) for c in _countries)} — these are the
    spots where the map will saturate and where a coarse-cell "priority" means
    a *lot* of co-occurring species. The freshwater / marine / terrestrial
    columns (species may belong to several systems) report the recorded system mixture: in the richest cell {_top[0][4] / _top[0][1]:.0%} of the
    species carry the Marine system in this snapshot. This does not identify a biological cause of richness. {_note}
    """),
            mo.ui.table(_rows, label="Top res-3 cells by species count"),
        ]
    )
    return


@app.cell
def _(CELLS_P, alt, duckdb, pl):
    _sp = duckdb.sql(f"""
        SELECT len(h3_indexes) AS n FROM '{CELLS_P}'
    """).pl()
    _b = (
        _sp.with_columns(
            pl.when(pl.col("n") < 10)
            .then(pl.lit("1–9"))
            .when(pl.col("n") < 25)
            .then(pl.lit("10–24"))
            .when(pl.col("n") < 50)
            .then(pl.lit("25–49"))
            .when(pl.col("n") < 100)
            .then(pl.lit("50–99"))
            .when(pl.col("n") < 250)
            .then(pl.lit("100–249"))
            .when(pl.col("n") < 500)
            .then(pl.lit("250–499"))
            .when(pl.col("n") < 1000)
            .then(pl.lit("500–999"))
            .otherwise(pl.lit("1,000+"))
            .alias("bucket"),
        )
        .group_by("bucket")
        .agg(n=pl.len())
        .select(
            pl.col("bucket"),
            pl.col("n"),
            pl.col("bucket")
            .replace_strict(
                {
                    "1–9": 0,
                    "10–24": 1,
                    "25–49": 2,
                    "50–99": 3,
                    "100–249": 4,
                    "250–499": 5,
                    "500–999": 6,
                    "1,000+": 7,
                }
            )
            .alias("i"),
        )
        .sort("i")
    )
    _bar = (
        alt.Chart(_b)
        .mark_bar(color="#7d5c9b")
        .encode(
            x=alt.X("bucket:N", sort=list(_b["bucket"]), title="res-3 cells per species"),
            y=alt.Y("n:Q", title="species"),
            tooltip=[alt.Tooltip("n:Q", title="species")],
        )
    )
    _lab = (
        alt.Chart(_b)
        .mark_text(dy=-6, fontSize=11)
        .encode(
            x=alt.X("bucket:N", sort=list(_b["bucket"]), title=None),
            y=alt.Y("n:Q"),
            text=alt.Text("n:Q", format=","),
            color=alt.value("#333"),
        )
    )
    (_bar + _lab).properties(
        width=600,
        height=280,
        title="Mapped range size across the selected species",
    ).configure_axis(labelFontSize=11, titleFontSize=12).configure_title(
        anchor="start", fontSize=13
    )
    return


@app.cell
def _(CELLS_P, duckdb, mo):
    _s = duckdb.sql(f"""
        SELECT min(n) AS lo,
               quantile_cont(n, 0.5) AS med,
               quantile_cont(n, 0.9) AS p90,
               quantile_cont(n, 0.99) AS p99,
               max(n) AS hi
        FROM (SELECT len(h3_indexes) AS n FROM '{CELLS_P}')
    """).fetchall()
    _s = _s[0]
    mo.md(
        f"Range size (res-3 cells per species): min {_s[0]:,}, median {_s[1]:,.0f}, "
        f"p90 {_s[2]:,.0f}, p99 {_s[3]:,.0f}, max {_s[4]:,}. "
        "Compare these quantiles within the selected mapped cohort; they do not "
        "establish the distribution of ranges outside these inputs."
    )
    return


@app.cell
def _(SYSTEMS_P, alt, duckdb):
    _sys = duckdb.sql(f"""
        SELECT system, COUNT(DISTINCT gbif_accepted_id) AS n
        FROM '{SYSTEMS_P}'
        GROUP BY 1 ORDER BY n DESC
    """).pl()
    SY_COLORS = {
        "Terrestrial": "#6b8e23",
        "Freshwater": "#3f7c8c",
        "Marine": "#2b5b8c",
    }
    _bar = (
        alt.Chart(_sys)
        .mark_bar()
        .encode(
            x=alt.X("system:N", sort=list(_sys["system"]), title=None),
            y=alt.Y("n:Q", title="species"),
            color=alt.Color(
                "system:N",
                legend=None,
                scale=alt.Scale(
                    domain=list(SY_COLORS),
                    range=[SY_COLORS[s] for s in SY_COLORS],
                ),
            ),
            tooltip=[alt.Tooltip("n:Q", title="species")],
        )
    )
    _lab = (
        alt.Chart(_sys)
        .mark_text(dy=-6, fontSize=12)
        .encode(
            x=alt.X("system:N", sort=list(_sys["system"]), title=None),
            y=alt.Y("n:Q"),
            text=alt.Text("n:Q", format=","),
            color=alt.value("#333"),
        )
    )
    (_bar + _lab).properties(
        width=560,
        height=280,
        title="Species mass by IUCN ecosystem system (species may map to several)",
    ).configure_axis(labelFontSize=11, titleFontSize=12).configure_title(
        anchor="start", fontSize=13
    )
    return


@app.cell
def _(SPECIES_P, alt, duckdb):
    _fam = duckdb.sql(f"""
        SELECT family, COUNT(*) AS n
        FROM '{SPECIES_P}'
        WHERE family IS NOT NULL
        GROUP BY 1 ORDER BY n DESC LIMIT 20
    """).pl()
    _bar = (
        alt.Chart(_fam)
        .mark_bar(color="#b0503f")
        .encode(
            x=alt.X("n:Q", title="registered species"),
            y=alt.Y("family:N", sort=list(_fam["family"]), title=None),
            tooltip=[alt.Tooltip("n:Q", title="species")],
        )
    )
    (_bar).properties(
        width=560, height=520, title="Top 20 families among registered species"
    ).configure_axis(labelFontSize=11, titleFontSize=12).configure_title(
        anchor="start", fontSize=13
    )
    return


@app.cell
def _(CELLS_P, EXPORT_STAMP, SPECIES_P, duckdb, mo):
    _u = duckdb.sql(f"""
        WITH mapped AS (
            SELECT DISTINCT gbif_accepted_id FROM '{CELLS_P}'
        ),
        species AS (
            SELECT gbif_accepted_id FROM '{SPECIES_P}'
        )
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (m.gbif_accepted_id IS NULL) AS unmapped
        FROM species
        LEFT JOIN mapped m USING (gbif_accepted_id)
    """).fetchone()
    mo.md(f"""
    ## What to watch out for when using this table
    ({EXPORT_STAMP} export snapshot)

    - `iucn_key` (aliased from `gbif_accepted_id`) **is the IUCN SIS taxon
      key**, not a GBIF key — join the GBIF world through `gbif_taxon_id`
      instead.
    - GoaT Data Deficient rows are *absence of evidence*, not confirmed
      without recorded species evidence species – they should never count as "DNA-negative" in a
      score without clarity.
    - Some species carry no mapped range at res 3 ({_u[1] / _u[0]:.1%} of
      {_u[0]:,} rows) — real assessments without
      a geographic product, not missing rows.
    - EDGE group labels indicate availability, not evolutionary-distinctiveness
      values or a validated conservation priority.
    """)
    return


if __name__ == "__main__":
    app.run()
