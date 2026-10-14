# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "marimo>=0.19",
#     "altair>=6,<7",
#     "duckdb>=1.5,<2",
#     "pyarrow>=21",
#     "polars>=1.30",
#     "statsmodels>=0.14",
#     "numpy>=2",
#     # pandas is pinned below 3 for this notebook's own environment: the
#     # statsmodels 0.14 discrete-model path it drives targets the pandas 2.x
#     # API. The project lock's pandas 3.x is for pipeline code, not this env.
#     "pandas>=2.3,<3",
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
    import numpy as np
    import polars as pl
    import statsmodels.api as sm
    from _notebook_inputs import configured_inputs

    return alt, configured_inputs, duckdb, h3, mo, np, os, pl, sm


@app.cell
def _(EXPORT_STAMP, RES3_AREA, RES3_EDGE, RES3_SPAN, mo):
    mo.md(f"""
    # Range size, threat, and the genomic representation gap

    Could the *mapped range* of a species predict whether it has species-level
    DNA evidence — and how does that interact with Red List category, taxonomic
    class, and ecosystem scope? Every quantity here is derived from the
    serving-side inputs of the current global preview; production scoring is unchanged and
    nothing is written. All claims below are computed live against the export
    input identity **{EXPORT_STAMP}** (SHA-256 of the consumed files; not a provider release date).

    **Inputs**

    | Source | Used for |
    | --- | --- |
    | `data/global/exports/species.parquet` | species register with Red List and DNA flags |
    | `data/global/exports/species_cells_res3.parquet` | mapped per-species ranges (res-3 cell lists) |
    | `data/global/exports/species_systems.parquet` | IUCN ecosystem systems (terrestrial / freshwater / marine) |
    | `data/exports/iucn_goat_global/iucn_goat_crosswalk.parquet` | GoaT lineage classes (IUCN class fallback) for a coarse clade breakdown |

    Every input is a configured local file; the class columns live in the retained
    IUCN↔GoaT crosswalk under `data/exports/iucn_goat_global/`.

    The range proxy is the count of any-touch resolution-3 H3 cells per species —
    the same `N_s` the roadmap's range-rarity item proposes to reuse. An
    average res-3 hexagon is ~{RES3_AREA:,.0f} km² and ~{RES3_SPAN:,.0f} km
    vertex-to-vertex (average edge {RES3_EDGE:.0f} km; exact H3 statistics
    provider, not sample estimates), so the proxy is footprint-like: it
    overestimates narrow and coastal ranges and is identical for every species
    with one mapped cell. It is a claim about mapped extent, never occupancy.
    """)
    return


@app.cell
def _(configured_inputs, duckdb, h3, os):
    inputs = configured_inputs(
        ("species.parquet", "species_systems.parquet", "species_cells_res3.parquet")
    )
    EXPORTS = inputs.exports
    CELLS_P = f"{EXPORTS}/species_cells_res3.parquet"
    SPECIES_P = f"{EXPORTS}/species.parquet"
    SYSTEMS_P = f"{EXPORTS}/species_systems.parquet"
    CROSSWALK_P = inputs.crosswalk
    # Canonical, resolution-level H3 statistics instead of estimates from
    # sampled head cells (the sample was latitude-biased).
    RES3_AREA = float(h3.average_hexagon_area(3, unit="km^2"))
    RES3_EDGE = float(h3.average_hexagon_edge_length(3, unit="km"))
    RES3_SPAN = 2 * RES3_EDGE
    RES7_AREA = float(h3.average_hexagon_area(7, unit="km^2"))
    RES7_SPAN = 2 * float(h3.average_hexagon_edge_length(7, unit="km"))

    EXPORT_STAMP = inputs.identity
    con = duckdb.connect()
    return (
        CELLS_P,
        CROSSWALK_P,
        EXPORT_STAMP,
        RES3_AREA,
        RES3_EDGE,
        RES3_SPAN,
        RES7_AREA,
        RES7_SPAN,
        SPECIES_P,
        SYSTEMS_P,
        con,
    )


@app.cell
def _(CELLS_P, CROSSWALK_P, SPECIES_P, SYSTEMS_P, con):
    species_df = con.sql(f"""
        WITH systems AS (
            SELECT gbif_accepted_id AS key,
                   string_agg(DISTINCT system, '/' ORDER BY system) AS systems
            FROM '{SYSTEMS_P}'
            GROUP BY 1
        ),
        cells AS (
            SELECT gbif_accepted_id AS key, len(h3_indexes) AS ncells
            FROM '{CELLS_P}'
        ),
        classes AS (
            SELECT iucn_sis_id AS sis,
                   YEAR(TRY_CAST(iucn_assessment_date AS TIMESTAMP)) AS assess_year,
                   taxonomic_concept_relation AS concept_relation,
                   safe_for_automatic_species_trait_transfer AS safe_transfer,
                   -- One class-keyed authority: the crosswalk mixes GoaT/NCBI
                   -- lineage names (Actinopteri, Lepidosauria) with
                   -- title-cased IUCN fallbacks (Actinopterygii, Reptilia)
                   -- for the same coarse clades. Without this
                   -- synonym map every class-keyed grouping silently splits
                   -- ray-finned fish and reptiles into two buckets each.
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
                   END AS goat_class
            FROM '{CROSSWALK_P}'
            WHERE sis IS NOT NULL
        ),
        base AS (
            SELECT gbif_accepted_id AS key,
                   TRY_CAST(iucn_sis_id AS BIGINT) AS sis,
                   species_name,
                   family,
                   redlist_category,
                   has_dna_species_level AS has_dna,
                   genus_has_dna,
                   family_has_dna,
                   goat_data_deficient AS goat_dd,
                   has_ebp_criteria_evidence AS ebp,
                   edge_group_name
            FROM '{SPECIES_P}'
        )
        SELECT base.*, classes.goat_class, classes.assess_year,
               classes.concept_relation, classes.safe_transfer,
               systems.systems, cells.ncells
        FROM base
        LEFT JOIN cells USING (key)
        LEFT JOIN systems USING (key)
        LEFT JOIN classes USING (sis)
    """).pl()
    return (species_df,)


@app.cell
def _(mo, pl, species_df):
    _total = species_df.height
    mapped = species_df.filter(pl.col("ncells").is_not_null())
    mo.stop(mapped.height == 0, mo.md("No species with mapped ranges in this input."))
    _dd = mapped.filter(pl.col("goat_dd")).height
    dna_known = mapped.filter(~pl.col("goat_dd") & pl.col("has_dna").is_not_null())
    mo.stop(
        dna_known.height == 0,
        mo.md("No mapped species with known DNA evidence; representation rate is unavailable."),
    )
    _dna = dna_known.filter(pl.col("has_dna")).height
    _cls = mapped.filter(pl.col("goat_class").is_not_null()).height
    mo.md(f"""
    **Coverage.** {_total:,} registered species, {mapped.height:,} with a mapped
    res-3 distribution ({mapped.height / _total:.0%}). Among the mapped ones:
    {_dd:,} are GoaT Data Deficient ({_dd / mapped.height:.0%}) — their DNA state
    is unknown, not negative — and {_dna:,} of {dna_known.height:,} GoaT-known
    species have species-level DNA ({_dna / dna_known.height:.1%}).
    {_cls / mapped.height:.0%} resolve to a lineage class in the GoaT↔IUCN
    crosswalk.
    """)
    return


@app.cell
def _(RES3_AREA, np, pl, species_df):
    # Canonical coarse-bucket labels after the crosswalk synonym map: the
    # GoaT/NCBI names ('Actinopteri', 'Lepidosauria') and the IUCN-style
    # fallback names ('Actinopterygii', 'Reptilia') are one clade each here,
    # unified under the IUCN bucket spelling.
    TOP_CLASSES = [
        "Actinopterygii",
        "Reptilia",
        "Magnoliopsida",
        "Amphibia",
        "Mammalia",
        "Insecta",
        "Gastropoda",
        "Aves",
    ]
    BUCKETS = ["1–9", "10–24", "25–49", "50–99", "100–249", "250–499", "500–999", "1,000+"]
    _cl = pl.col("goat_class").fill_null("unmatched")
    derived_df = (
        species_df.filter(pl.col("ncells").is_not_null())
        .with_columns(
            (pl.col("ncells").cast(pl.Float64) * RES3_AREA).alias("area_km2"),
            pl.col("ncells").cast(pl.Float64).log10().alias("log10_cells"),
        )
        .with_columns(
            (pl.col("log10_cells") + np.log10(RES3_AREA)).alias("log10_area"),
            pl.when(pl.col("ncells") <= 9)
            .then(0)
            .when(pl.col("ncells") <= 24)
            .then(1)
            .when(pl.col("ncells") <= 49)
            .then(2)
            .when(pl.col("ncells") <= 99)
            .then(3)
            .when(pl.col("ncells") <= 249)
            .then(4)
            .when(pl.col("ncells") <= 499)
            .then(5)
            .when(pl.col("ncells") <= 999)
            .then(6)
            .otherwise(7)
            .alias("_bn"),
        )
        .with_columns(
            pl.col("_bn").replace_strict(dict(enumerate(BUCKETS))).alias("bucket"),
            pl.when(_cl.is_in(TOP_CLASSES))
            .then(_cl)
            .when(_cl == "unmatched")
            .then(pl.lit("unmatched"))
            .otherwise(pl.lit("other"))
            .alias("class_top"),
            pl.when(pl.col("redlist_category").str.starts_with("Lower Risk"))
            .then(pl.lit("LC"))
            .when(pl.col("redlist_category") == "Least Concern")
            .then(pl.lit("LC"))
            .when(pl.col("redlist_category") == "Near Threatened")
            .then(pl.lit("NT"))
            .when(pl.col("redlist_category") == "Vulnerable")
            .then(pl.lit("VU"))
            .when(pl.col("redlist_category") == "Endangered")
            .then(pl.lit("EN"))
            .when(pl.col("redlist_category") == "Critically Endangered")
            .then(pl.lit("CR"))
            .when(pl.col("redlist_category") == "Data Deficient")
            .then(pl.lit("DD"))
            .otherwise(pl.lit("Not assessed"))
            .alias("rl5"),
            pl.when(pl.col("systems").is_null())
            .then(None)
            .when(
                pl.col("systems").str.contains("Terrestrial")
                & pl.col("systems").str.contains("Marine")
            )
            .then(pl.lit("mixed terrestrial+marine"))
            .when(pl.col("systems").str.contains("Marine"))
            .then(pl.lit("marine (+freshwater)"))
            .when(pl.col("systems") == "Freshwater")
            .then(pl.lit("freshwater only"))
            .otherwise(pl.lit("terrestrial (+freshwater)"))
            .alias("scope"),
        )
    )
    return BUCKETS, TOP_CLASSES, derived_df


@app.cell
def _(derived_df, mo, pl, species_df):
    _lr_all = species_df.filter(pl.col("redlist_category").str.starts_with("Lower Risk")).height
    _na = species_df.filter(pl.col("redlist_category") == "Not Assessed").height
    _lr_mapped = derived_df.filter(pl.col("redlist_category").str.starts_with("Lower Risk")).height
    mo.md(f"""
    *Bookkeeping, stated once:* for reading convenience this notebook folds
    legacy `Lower Risk/*` rows ({_lr_mapped:,} of them among mapped species;
    {_lr_all:,} registered overall) into LC for the category summaries, and
    maps the {_na:,} `Not Assessed` IUCN-side non-ranks to their own bucket.
    The serving ladder treats both differently — they match no threat
    predicate and score zero. That divergence is fine for EDA; analyses that
    must mirror the serving rule use the pair-level ladder of notebook 3.
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 1. The range staircase

    Every bucket is a count of the species' mapped resolution-3 cells, from
    1–9 cells (~12,000–110,000 km²) up to 1,000+ cells (>12 million km²). Test whether the
    share with known species-level evidence changes across the staircase;
    the chart uses mapped species with known evidence and resolved classes.
    """)
    return


@app.cell
def _(BUCKETS, alt, derived_df, pl):
    _stairs = (
        derived_df.filter(
            (pl.col("class_top") != "unmatched")
            & (~pl.col("goat_dd"))
            & pl.col("has_dna").is_not_null()
        )
        .group_by("bucket")
        .agg(
            pl.len().alias("n"),
            (pl.col("has_dna").cast(pl.Float64).mean() * 100).alias("pct_dna"),
        )
    )
    _x = alt.X("bucket:N", sort=BUCKETS, axis=alt.Axis(title="mapped res-3 cells per species"))
    _bars = (
        alt.Chart(_stairs)
        .mark_bar(color="#2b6a9c")
        .encode(
            x=_x,
            y=alt.Y("pct_dna:Q", title="% species with species-level DNA"),
            tooltip=[
                alt.Tooltip("n:Q", title="species"),
                alt.Tooltip("pct_dna:Q", format=".1f", title="% with DNA"),
            ],
        )
    )
    _labels = (
        alt.Chart(_stairs)
        .mark_text(dy=-6, fontSize=11, color="#333")
        .encode(
            x=_x,
            y=alt.Y("pct_dna:Q"),
            text=alt.Text("pct_dna:Q", format=".1f"),
        )
    )
    (_bars + _labels).properties(
        width=620, height=300, title="Recorded species evidence by mapped range size"
    ).configure_axis(labelFontSize=11, titleFontSize=12).configure_title(
        anchor="start", fontSize=13
    )
    return


@app.cell
def _(TOP_CLASSES, alt, derived_df, mo, pl):
    _hm = (
        derived_df.filter(
            pl.col("class_top").is_in(TOP_CLASSES)
            & (~pl.col("goat_dd"))
            & pl.col("has_dna").is_not_null()
        )
        .group_by("class_top", "bucket")
        .agg(
            pl.len().alias("n"),
            (pl.col("has_dna").cast(pl.Float64).mean() * 100).alias("pct_dna"),
        )
    )
    _hx = alt.X(
        "bucket:N",
        sort=["1–9", "10–24", "25–49", "50–99", "100–249", "250–499", "500–999", "1,000+"],
        title="mapped res-3 cells per species",
        axis=alt.Axis(labels=False, title=None),
    )
    _rect = (
        alt.Chart(_hm)
        .mark_rect()
        .encode(
            x=_hx,
            y=alt.Y("class_top:N", sort=TOP_CLASSES, title=None),
            color=alt.Color("pct_dna:Q", scale=alt.Scale(scheme="tealblues")),
            tooltip=[
                alt.Tooltip("n:Q", title="species"),
                alt.Tooltip("pct_dna:Q", format=".1f", title="% with DNA"),
            ],
        )
    )
    _text = (
        alt.Chart(_hm)
        .mark_text(fontSize=10)
        .encode(
            x=_hx,
            y=alt.Y("class_top:N", sort=TOP_CLASSES),
            text=alt.Text("pct_dna:Q", format=".1f"),
            color=alt.condition("datum.pct_dna > 20", alt.value("white"), alt.value("#222")),
        )
    )
    mo.accordion(
        {
            "Class × range heatmap: is the range effect just a taxonomy effect?": (
                _rect + _text
            ).properties(
                width=560,
                height=300,
                title="% species with species-level DNA (teal = high)",
            ),
        }
    )
    return


@app.cell
def _(mo):
    mo.md(r"""
    Read the heatmap within each class as well as across classes. The
    denominators include mapped species with known GoaT status and non-null
    species evidence. A range gradient is an association in this selected
    cohort; the model below adjusts for recorded class, category and scope.
    """)
    return


@app.cell
def _(RES3_AREA, mo):
    mo.md(f"""
    ## 2. Threat and range size

    Critically Endangered species should sit on much smaller footprints than
    Least Concern ones if range-based threat assessment is internally
    consistent. Use the box plots to test that ordering — it is a *descriptive
    consistency check, not independent validation*: IUCN Criterion B assigns
    threat status partly from mapped range size in the first place (EOO/AOO),
    so the two quantities share ancestry by construction. Nothing here tests
    that the Red List is right; only that the export's stored category and
    mapped extent cohere.

    IUCN Criterion B EOO thresholds (100 / 5,000 / 20,000 km²) mostly fall
    below the bottom of this scale: one res-3 hexagon is already
    ~{RES3_AREA:,.0f} km², so only the 20,000 km² line is resolvable at this
    resolution.
    """)
    return


@app.cell
def _(alt, derived_df, np, pl):
    RC_ORDER = ["LC", "NT", "VU", "EN", "CR", "DD"]
    RC_COLORS = {
        "LC": "#59b9dd",
        "NT": "#ffcc00",
        "VU": "#f28021",
        "EN": "#d13837",
        "CR": "#ac2e44",
        "DD": "#d7d6d6",
    }
    _box = (
        derived_df.filter(
            (pl.col("class_top") != "unmatched")
            & (~pl.col("goat_dd"))
            & pl.col("has_dna").is_not_null()
        )
        .select("log10_area", "rl5")
        .filter(pl.col("rl5").is_in(RC_ORDER))
    )
    _thr = pl.DataFrame({"y": [float(np.log10(20000.0))], "label": ["20,000 km² (criterion B)"]})
    _dom = alt.Scale(domain=[3.9, 9.0])
    _rule = (
        alt.Chart(_thr)
        .mark_rule(stroke="#555", strokeDash=[5, 4])
        .encode(y=alt.Y("y:Q", scale=_dom))
    )
    _rtext = (
        alt.Chart(_thr)
        .mark_text(align="left", dx=6, dy=-110, fontSize=10, color="#555")
        .encode(y=alt.Y("y:Q", scale=_dom), text=alt.Text("label:N"))
    )
    _bp = (
        alt.Chart(_box)
        .mark_boxplot(ticks=False, extent=1.5, outliers=True)
        .encode(
            x=alt.X("rl5:N", sort=RC_ORDER, title="Red List category"),
            y=alt.Y("log10_area:Q", scale=_dom, title="log₁₀ range footprint (km²)"),
            color=alt.Color(
                "rl5:N",
                sort=RC_ORDER,
                legend=None,
                scale=alt.Scale(domain=RC_ORDER, range=[RC_COLORS[c] for c in RC_ORDER]),
            ),
        )
    )
    (_bp + _rule + _rtext).properties(
        width=620, height=320, title="Distribution footprint by Red List category"
    ).configure_axis(labelFontSize=11, titleFontSize=12).configure_title(
        anchor="start", fontSize=13
    )
    return


@app.cell
def _(derived_df, mo, pl):
    _med = (
        derived_df.group_by("rl5")
        .agg(
            med=pl.col("log10_area").median(),
            n=pl.len(),
        )
        .sort("med", descending=True)
    )
    _lines = "\n".join(
        f"- **{row['rl5']}** — median footprint ~{10 ** row['med']:,.0f} km² "
        f"(10^{row['med']:.2f}; {row['n']:,} species)"
        for row in _med.iter_rows(named=True)
    )
    mo.md(f"**Category medians**\n\n{_lines}")
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 3. Who owns the map, who owns the genome

    Cumulative footprint curves per class show whole taxonomic cohorts shifted
    along the range-size axis: compare the recorded class cohorts without presuming their ordering.
    Missing class assignments stay outside these class-specific curves.
    """)
    return


@app.cell
def _(TOP_CLASSES, alt, derived_df, mo, pl):
    _binned = (
        derived_df.filter(
            pl.col("class_top").is_in(TOP_CLASSES)
            & (~pl.col("goat_dd"))
            & pl.col("has_dna").is_not_null()
        )
        .select("class_top", "log10_area")
        .with_columns(((((pl.col("log10_area") - 4.0) / 0.1).floor() * 0.1) + 4.0).alias("bin"))
        .group_by("class_top", "bin")
        .agg(pl.len().alias("n"))
        .sort("bin")
    )
    _totals = _binned.group_by("class_top").agg(pl.col("n").sum().alias("total"))
    _cdf = (
        _binned.join(_totals, on="class_top")
        .sort(["class_top", "bin"])
        .with_columns(pl.col("n").cum_sum().over("class_top").alias("cum"))
        .with_columns((pl.col("cum") / pl.col("total")).alias("share"))
    )
    mo.accordion(
        {
            "Cumulative footprint curves per class": alt.Chart(_cdf)
            .mark_line(interpolate="step-after")
            .encode(
                x=alt.X(
                    "bin:Q",
                    axis=alt.Axis(tickMinStep=1, format=".0f"),
                    title="log₁₀ range footprint (km²)",
                ),
                y=alt.Y("share:Q", axis=alt.Axis(format="%"), title="cumulative share of class"),
                color=alt.Color("class_top:N", sort=TOP_CLASSES, title=None),
                tooltip=[
                    alt.Tooltip("class_top:N", title="class"),
                    alt.Tooltip("share:Q", format=".0%", title="cumulative"),
                    alt.Tooltip("bin:Q", format=".2f", title="log₁₀ footprint"),
                ],
            )
            .properties(
                width=620,
                height=320,
                title="Cumulative mapped footprint by lineage class",
            )
            .configure_axis(labelFontSize=11, titleFontSize=12)
            .configure_title(anchor="start", fontSize=13),
        }
    )
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 4. Formalizing the range ↔ DNA effect

    A logistic model on mapped species with known GoaT status (GoaT Data
    Deficient rows excluded — absent metadata is not missing DNA):

    `has_species_evidence ~ log₁₀(footprint) + taxonomic class + Red List + scope`

    Coefficients are log-odds deltas against the reference levels: a
    Least Concern, terrestrial, flowering-plant species at its range size.

    One statistical caveat handled here: species within a family share
    sequencing effort, range size, and threat status, so a plain logit treats
    species as independent experiments and overstates certainty. The
    confidence intervals are therefore **cluster-robust by family** (standard
    errors clustered on the species table's `family` column); the point
    estimates remain fitted associations in this selected cohort; clustering
    adjusts their standard errors. A full mixed/GLMM accounting of within-family random effects
    is the next step and is listed in the conclusion's limits.
    """)
    return


@app.cell
def _(TOP_CLASSES, alt, derived_df, mo, np, pl, sm):
    import pandas as pd

    _keep = derived_df.filter(
        pl.col("class_top").is_in(TOP_CLASSES)
        & pl.col("rl5").is_in(["LC", "VU", "EN", "CR"])
        & pl.col("scope").is_not_null()
        & pl.col("family").is_not_null()
        & ~pl.col("goat_dd")
        & pl.col("has_dna").is_not_null()
    ).select(
        pl.col("has_dna").cast(pl.Int8).alias("has_dna"),
        pl.col("log10_area").alias("log10_footprint"),
        "class_top",
        "rl5",
        "scope",
        "family",
    )
    mo.stop(_keep.height == 0, mo.md("No complete mapped observations for the logistic model."))
    _pd = _keep.to_pandas()
    _y = _pd["has_dna"].astype(float)
    mo.stop(
        not {"Magnoliopsida"} <= set(_pd["class_top"])
        or "LC" not in set(_pd["rl5"])
        or "terrestrial (+freshwater)" not in set(_pd["scope"]),
        mo.md("The stated logistic reference levels are absent from this cohort."),
    )
    _x = pd.get_dummies(
        _pd[["log10_footprint", "class_top", "rl5", "scope"]],
        columns=["class_top", "rl5", "scope"],
        prefix={"class_top": "cls", "rl5": "rl", "scope": "scp"},
        dtype=float,
    ).drop(columns=["cls_Magnoliopsida", "rl_LC", "scp_terrestrial (+freshwater)"], errors="ignore")
    mo.stop(
        _y.nunique() < 2 or _pd["family"].nunique() < 2,
        mo.md("Logit requires both outcomes and multiple family clusters."),
    )
    mo.stop(
        np.linalg.matrix_rank(sm.add_constant(_x).to_numpy()) < _x.shape[1] + 1,
        mo.md("Logit design is rank-deficient for this cohort."),
    )
    _fit = sm.Logit(_y, sm.add_constant(_x)).fit(
        disp=0,
        cov_type="cluster",
        cov_kwds={"groups": _pd["family"]},
    )
    mo.stop(
        not _fit.mle_retvals["converged"] or not np.isfinite(_fit.params).all(),
        mo.md(
            "Logit did not converge to finite coefficients; no model interpretation is available."
        ),
    )
    _ci = _fit.conf_int()
    TERM_LABELS = {
        "log10_footprint": "range footprint (per 10×)",
        "scp_marine (+freshwater)": "scope: marine (+freshwater)",
        "scp_freshwater only": "scope: freshwater only",
        "scp_mixed terrestrial+marine": "scope: mixed",
        "cls_Actinopterygii": "Actinopterygii",
        "cls_Reptilia": "Reptilia",
        "cls_Amphibia": "Amphibia",
        "cls_Mammalia": "Mammalia",
        "cls_Insecta": "Insecta",
        "cls_Gastropoda": "Gastropoda",
        "cls_Aves": "Aves",
        "rl_VU": "Vulnerable",
        "rl_EN": "Endangered",
        "rl_CR": "Critically Endangered",
    }
    forest = (
        pl.DataFrame(
            {
                "term": _fit.params.index,
                "coef": _fit.params.values,
                "lo": _ci.iloc[:, 0].values,
                "hi": _ci.iloc[:, 1].values,
            }
        )
        .filter(pl.col("term") != "const")
        .sort("coef")
    )
    forest = forest.with_columns(pl.col("term").replace_strict(TERM_LABELS))
    _zero = (
        alt.Chart(pl.DataFrame({"z": [0.0]}))
        .mark_rule(stroke="#888", strokeDash=[4, 4])
        .encode(x=alt.X("z:Q"))
    )
    _ci_layer = (
        alt.Chart(forest)
        .mark_rule(strokeWidth=2.4, color="#2b6a9c", opacity=0.5)
        .encode(
            y=alt.Y("term:N", sort=list(forest["term"]), title=None),
            x=alt.X("lo:Q"),
            x2="hi:Q",
        )
    )
    _dots = (
        alt.Chart(forest)
        .mark_circle(size=74, color="#2b6a9c")
        .encode(
            y=alt.Y("term:N", sort=list(forest["term"]), title=None),
            x=alt.X("coef:Q", title="coefficient (log-odds of having species-level DNA)"),
            tooltip=[
                alt.Tooltip("term:N", title="term"),
                alt.Tooltip("coef:Q", format=".2f", title="log-odds"),
                alt.Tooltip("lo:Q", format=".2f", title="CI low"),
                alt.Tooltip("hi:Q", format=".2f", title="CI high"),
            ],
        )
    )
    model_n = int(_fit.nobs)
    model_r2 = float(_fit.prsquared)
    model_odds_decade = float(np.exp(_fit.params["log10_footprint"]))
    model_families = int(_pd["family"].nunique())
    forest_note = mo.md(
        f"n = **{model_n:,}** species across **{model_families:,}** families "
        f"(standard errors clustered on family) · McFadden pseudo-R² = "
        f"**{model_r2:.3f}** · backbone odds ratio per 10× footprint = "
        f"**{model_odds_decade:.1f}**. Reference levels: Least Concern · "
        "Magnoliopsida · terrestrial (+freshwater)."
    )
    mo.vstack(
        [
            (_ci_layer + _zero + _dots)
            .properties(
                width=620,
                height=380,
                title="What moves the odds of having a species-level evidence? (family-clustered CIs)",
            )
            .configure_axis(labelFontSize=11, titleFontSize=12)
            .configure_title(anchor="start", fontSize=13),
            forest_note,
        ]
    )
    return model_families, model_n, model_odds_decade, model_r2


@app.cell
def _(mo):
    mo.md(r"""
    ## 4b. Both selection processes, separately

    §4 conditions on mapped, GoaT-known species — the survivorship frame. Two
    distinct selection processes are collapsed into that conditioning:

    1. `P(mapped | threat, class, scope)` — having a managed cartographic
       product at all (coverage is computed above from the selected register); and
    2. `P(species-level DNA | mapped, threat, class, scope)` — the §4 model.

    Fit both conditional associations with family-clustered uncertainty.
    Model A uses the registered universe; Model B uses mapped, GoaT-known
    species. Their odds ratios are reported separately, never multiplied
    into a joint selection effect.
    """)
    return


@app.cell
def _(TOP_CLASSES, mo, np, pl, sm, species_df):
    # Same bucket derivations as derived_df, applied to every registered row
    # (mapped or not), so the mapping process itself can be modelled.
    _frame = species_df.filter(pl.col("family").is_not_null()).with_columns(
        (pl.col("ncells").is_not_null()).alias("mapped"),
        pl.when(pl.col("redlist_category").str.starts_with("Lower Risk"))
        .then(pl.lit("LC"))
        .when(pl.col("redlist_category") == "Least Concern")
        .then(pl.lit("LC"))
        .when(pl.col("redlist_category") == "Near Threatened")
        .then(pl.lit("NT"))
        .when(pl.col("redlist_category") == "Vulnerable")
        .then(pl.lit("VU"))
        .when(pl.col("redlist_category") == "Endangered")
        .then(pl.lit("EN"))
        .when(pl.col("redlist_category") == "Critically Endangered")
        .then(pl.lit("CR"))
        .when(pl.col("redlist_category") == "Data Deficient")
        .then(pl.lit("DD"))
        .otherwise(pl.lit("Not assessed"))
        .alias("rl5"),
        pl.when(pl.col("systems").is_null())
        .then(None)
        .when(
            pl.col("systems").str.contains("Terrestrial") & pl.col("systems").str.contains("Marine")
        )
        .then(pl.lit("mixed"))
        .when(pl.col("systems").str.contains("Marine"))
        .then(pl.lit("marine"))
        .when(pl.col("systems") == "Freshwater")
        .then(pl.lit("freshwater"))
        .otherwise(pl.lit("terrestrial"))
        .alias("scp"),
        pl.when(pl.col("goat_class").is_in(TOP_CLASSES))
        .then(pl.col("goat_class"))
        .otherwise(pl.lit("other"))
        .alias("cls"),
        pl.when(pl.col("ncells").is_not_null())
        .then(pl.col("ncells").cast(pl.Float64).log10())
        .alias("log10_area"),
    )

    def _fit(frame, y: str):
        # Validate each process independently so an unavailable fit does not
        # suppress the other process's supported association.
        if frame.height == 0:
            return None, "No complete observations for this conditional logistic model."
        import pandas

        _pd = frame.to_pandas()
        _y = _pd[y].astype(float)
        if _y.nunique() < 2 or _pd["family"].nunique() < 2:
            return None, "Conditional logit needs both outcomes and multiple family clusters."
        if (
            "Magnoliopsida" not in set(_pd["cls"])
            or "LC" not in set(_pd["rl5"])
            or "terrestrial" not in set(_pd["scp"])
        ):
            return None, "The stated conditional-logit reference levels are absent."
        _x = pandas.get_dummies(
            _pd[["cls", "rl5", "scp"]],
            columns=["cls", "rl5", "scp"],
            prefix={"cls": "cls", "rl5": "rl", "scp": "scp"},
            dtype=float,
        ).drop(columns=["cls_Magnoliopsida", "rl_LC", "scp_terrestrial"], errors="ignore")
        if np.linalg.matrix_rank(sm.add_constant(_x).to_numpy()) < _x.shape[1] + 1:
            return None, "Conditional-logit design is rank-deficient."
        result = sm.Logit(_y, sm.add_constant(_x)).fit(
            disp=0,
            cov_type="cluster",
            cov_kwds={"groups": _pd["family"]},
        )
        if not result.mle_retvals["converged"] or not np.isfinite(result.params).all():
            return (
                None,
                "Conditional logit did not converge to finite results; no model interpretation is available.",
            )
        with np.errstate(over="ignore"):
            if not np.isfinite(np.exp(result.params)).all():
                return (
                    None,
                    "Conditional-logit odds ratios are nonfinite; no model interpretation is available.",
                )
        return result, ""

    A_frame = _frame.filter(
        pl.col("rl5").is_in(["LC", "NT", "VU", "EN", "CR", "DD", "Not assessed"])
        & pl.col("scp").is_not_null()
        & pl.col("cls").is_not_null()
    ).select("mapped", "cls", "rl5", "scp", "family")
    fit_a, diagnostic_a = _fit(A_frame, "mapped")
    B_frame = _frame.filter(
        pl.col("mapped")
        & pl.col("rl5").is_in(["LC", "VU", "EN", "CR"])
        & ~pl.col("goat_dd")
        & pl.col("scp").is_not_null()
        & pl.col("cls").is_in(TOP_CLASSES)
        & pl.col("has_dna").is_not_null()
    ).select("has_dna", "cls", "rl5", "scp", "family")
    fit_b, diagnostic_b = _fit(B_frame, "has_dna")

    def _or(fit, term: str) -> float | None:
        if fit is None or term not in fit.params.index:
            return None
        return float(np.exp(fit.params[term]))

    def _reported(value: float | None) -> str:
        return f"{value:.2f}×" if value is not None else "unavailable"

    map_n = int(fit_a.nobs) if fit_a is not None else None
    dna_n = int(fit_b.nobs) if fit_b is not None else None
    map_cr = _or(fit_a, "rl_CR")
    map_en = _or(fit_a, "rl_EN")
    map_vu = _or(fit_a, "rl_VU")
    map_dd = _or(fit_a, "rl_DD")
    dna_cr = _or(fit_b, "rl_CR")
    net_cr = None
    net_en = None
    _panel_a = (
        mo.md(f"**Model A — mapping association unavailable.** {diagnostic_a}")
        if fit_a is None
        else mo.md(f"""
        **Model A — P(mapped | class, category, scope).** n = {map_n:,}
        registered species (family known). Mapping odds ratios against a
        Least-Concern Magnoliopsida terrestrial species: CR **{_reported(map_cr)}**,
        EN **{_reported(map_en)}**, VU **{_reported(map_vu)}**,
        Red List Data Deficient **{_reported(map_dd)}**. Missing category terms
        have unavailable odds ratios. These associations depend on the register,
        available systems and taxonomy, and model specification.
        """)
    )
    _panel_b = (
        mo.md(f"**Model B — representation association unavailable.** {diagnostic_b}")
        if fit_b is None
        else mo.md(f"""
        **Model B — P(species-level DNA | mapped, class, category, scope)** —
        n = {dna_n:,} mapped, GoaT-known species. CR odds ratio
        **{_reported(dna_cr)}** (unavailable if the CR term is absent).
        """)
    )
    part_panel = mo.vstack(
        [
            _panel_a,
            _panel_b,
            mo.md("""
        These are separate conditional associations. Multiplying conditional
        odds ratios does not give the odds ratio for being both mapped and
        represented, or a population selection bias. Such a claim needs
        predicted joint probabilities and a defined target population.
        No causal correction is made here.
        """),
        ]
    )
    part_panel
    return dna_cr, map_cr, net_cr, net_en


@app.cell
def _(mo):
    mo.md(r"""
    ## 5. Small ranges must not stay dark

    The roadmap's range-rarity item needs a per-species mapped-range size `N_s`;
    this is its cheapest actionable cut. Omitted GoaT rows (Data Deficient) were
    excluded; the negative state means no qualifying recorded evidence,
    not proof that no genomic resource exists.
    """)
    return


@app.cell
def _(TOP_CLASSES, alt, derived_df, mo, pl):
    dark_df = derived_df.filter(
        pl.col("has_dna").is_not_null()
        & ~pl.col("has_dna")
        & ~pl.col("goat_dd")
        & pl.col("rl5").is_in(["CR", "EN"])
        & (pl.col("ncells") <= 25)
        & pl.col("class_top").is_in(TOP_CLASSES)
    )
    _byclass = dark_df.group_by("class_top").agg(n=pl.len()).sort("n", descending=True)
    _by = alt.Y("class_top:N", sort=list(_byclass["class_top"]), title=None)
    _bars = (
        alt.Chart(_byclass)
        .mark_bar(color="#b0503f")
        .encode(
            y=_by,
            x=alt.X(
                "n:Q", title="threatened, restricted, without recorded species evidence species"
            ),
            tooltip=[alt.Tooltip("n:Q", title="species")],
        )
    )
    _labels = (
        alt.Chart(_byclass)
        .mark_text(dx=6, align="left", fontSize=10, color="#333")
        .encode(
            y=_by,
            x=alt.X("n:Q"),
            text=alt.Text("n:Q", format="d"),
        )
    )
    dark_table_rows = (
        dark_df.sort(["rl5", "ncells"])
        .head(12)
        .select(
            "species_name",
            "class_top",
            "rl5",
            "ncells",
            pl.col("area_km2").round(0).cast(pl.Int64).alias("area km²"),
            "systems",
        )
    )
    mo.vstack(
        [
            mo.md(f"""
        **{dark_df.height:,}** Endangered or Critically Endangered species sit on ≤ 25 res-3
        cells (~≤ 310,000 km²) with no species-level genomic evidence in GoaT/NCBI
        — restricted to the top-8 lineage classes defined above; the conclusion's
        section-end count also includes other classes. The per-species list is an exploratory candidate cohort for additional
        review: narrow, threatened species
        with no qualifying species-level evidence in the selected export. Absence
        of recorded evidence is not proof of biological absence.
        """),
            mo.accordion(
                {
                    "count by class": (_bars + _labels)
                    .properties(
                        width=560,
                        height=260,
                        title="Restricted yet without recorded species evidence, by class (top-8 lineage classes)",
                    )
                    .configure_axis(labelFontSize=11, titleFontSize=12),
                }
            ),
            mo.ui.table(dark_table_rows.to_dicts(), label="Twelve most restricted (CR first)"),
        ]
    )
    return (dark_df,)


@app.cell
def _(mo):
    mo.md(r"""
    ## 5b. EBP overlap, and the columns that make the "untestable" list testable

    Two provenance columns that sat unused become decision data here: the
    GoaT EBP criteria flag (above) and the crosswalk's assessment year and
    taxonomic-concept relation (below). What changed since the serving
    exports were treated as carrying neither:
    """)
    return


@app.cell
def _(dark_df, mo, pl, species_df):
    dark_ebp = dark_df.filter(pl.col("ebp"))
    dark_ebp_total = dark_ebp.height
    dark_ebp_bycls = {
        r["class_top"]: r["n"]
        for r in dark_ebp.group_by("class_top")
        .agg(n=pl.len())
        .sort("n", descending=True)
        .iter_rows(named=True)
    }
    dark_no_ebp = dark_df.height - dark_ebp_total
    if dark_ebp_total:
        _ebp_line = (
            f"**{dark_ebp_total:,}** already carry GoaT\n"
            f"`ebp_standard_criteria` 6.7/6.C evidence — the flag the pipeline\n"
            f"serves as `has_ebp_criteria_evidence`, gated on the crosswalk's\n"
            f"`safe_for_automatic_species_trait_transfer` (written by\n"
            f"`ark_pipeline/builders/species_metadata.py`). Top classes among\n"
            f"them:\n"
            f"{', '.join(f'{k} {v:,}' for k, v in list(dark_ebp_bycls.items())[:3])}.\n"
            "These supplied flags conflict with the current producer's evidence rule: a\n"
            "safe-transferred positive criteria flag also implies qualifying species evidence.\n"
            "Check input consistency before interpreting this crossing; it does not establish\n"
            f"a planned target cohort. The remaining **{dark_no_ebp:,}** lack that flag."
        )
    else:
        _ebp_line = (
            "**None of these selected rows carries the EBP-criteria flag.** "
            "That is an observation in this cohort, not proof of universal "
            "disjointness or evidence that every unflagged species is a new "
            "sequencing target. The flag records GoaT criteria evidence, "
            "gated on safe species transfer."
        )
    ebp_note = mo.md(f"""
    **EBP criteria-evidence crossing.** Of the {dark_df.height:,} restricted
    threatened species without recorded species-level evidence above: {_ebp_line} The
    flag is computed by the pipeline from safe-transferred GoaT criteria text;
    it is neither independent quality certification nor target-list membership.
    """)
    _rel = species_df.select(
        "concept_relation",
    )
    _exact = pl.col("concept_relation").eq_missing("CURRENT_NAME_EXACT")
    _n_exact = _rel.filter(_exact).height
    _n_rel = _rel.filter(pl.col("concept_relation").is_not_null()).height
    _syn = _rel.filter(pl.col("concept_relation") == "NCBI_SYNONYM").height
    _split = _rel.filter(pl.col("concept_relation") == "IUCN_SPLIT_OR_NCBI_LUMP").height
    _near = _rel.filter(pl.col("concept_relation") == "GBIF_CORROBORATED_NEAR_NAME").height
    _none = _rel.height - _n_rel
    testable_note = mo.md(f"""
    - **Available assessment dates**
      ({species_df.filter(pl.col("assess_year").is_not_null()).height:,} of
      {species_df.height:,} registered rows; missing dates remain unknown). "Recent-assessments-only" is a
      pure join — and cohort dating is testable here (next cell).
    - **Name relation classes** are served too: {_n_exact:,}
      `CURRENT_NAME_EXACT`, {_syn:,} `NCBI_SYNONYM`, {_split:,}
      `IUCN_SPLIT_OR_NCBI_LUMP`, {_near:,} near-name corroborations — plus
      **{_none:,}** rows with no relation, a crosswalk-vs-species-table
      coverage question rather than a weight choice. Notebook 3 §3b turns
      these into scored specifications (exact-only rescore, unclassified
      bucket kept visible).
    """)
    ebp_panel = mo.vstack([ebp_note, testable_note])
    ebp_panel
    return


@app.cell
def _(alt, derived_df, mo, np, pl, species_df):
    _duo = (
        derived_df.filter(~pl.col("goat_dd") & pl.col("has_dna").is_not_null())
        .with_columns(
            pl.col("assess_year").fill_null(0).alias("_y"),
            (pl.col("has_dna").cast(pl.Float64)).alias("_d"),
        )
        .with_columns(
            pl.when(pl.col("_y") == 0)
            .then(pl.lit(None))
            .when(pl.col("_y") <= 2000)
            .then(pl.lit("≤2000"))
            .when(pl.col("_y") <= 2005)
            .then(pl.lit("2001–2005"))
            .when(pl.col("_y") <= 2010)
            .then(pl.lit("2006–2010"))
            .when(pl.col("_y") <= 2015)
            .then(pl.lit("2011–2015"))
            .when(pl.col("_y") <= 2020)
            .then(pl.lit("2016–2020"))
            .otherwise(pl.lit("2021+"))
            .alias("band")
        )
        .filter(pl.col("band").is_not_null())
    )
    _bands = ["≤2000", "2001–2005", "2006–2010", "2011–2015", "2016–2020", "2021+"]
    _coh = (
        _duo.group_by("band")
        .agg(
            n=pl.len(),
            pct=(pl.col("_d").mean() * 100),
        )
        .with_columns(
            pl.col("band").replace_strict({b: i for i, b in enumerate(_bands)}).alias("_o")
        )
        .sort("_o")
    )
    _xs = _coh["_o"].to_list()
    _ys = _coh["pct"].to_list()
    mo.stop(len(_xs) < 2, mo.md("At least two dated assessment cohorts are needed for a slope."))
    slope_pp, _inter = np.polyfit(_xs, _ys, 1)
    _bars = (
        alt.Chart(_coh)
        .mark_bar(color="#2b6a9c")
        .encode(
            x=alt.X("band:N", sort=_bands, title="assessment-date year (cohort)"),
            y=alt.Y("pct:Q", title="% with species-level DNA at export time"),
            tooltip=[
                alt.Tooltip("n:Q", title="species"),
                alt.Tooltip("pct:Q", format=".1f", title="% with DNA"),
            ],
        )
        .properties(
            width=560,
            height=260,
            title="Recorded species evidence by assessment-date band",
        )
    )
    _labels = (
        alt.Chart(_coh)
        .mark_text(dy=-6, fontSize=10, color="#333")
        .encode(
            x=alt.X("band:N", sort=_bands),
            y=alt.Y("pct:Q"),
            text=alt.Text("pct:Q", format=".1f"),
        )
    )
    first_pct = float(_ys[0])
    last_pct = float(_ys[-1])
    cohort_trend = mo.vstack(
        [
            (_bars + _labels)
            .configure_axis(labelFontSize=11, titleFontSize=12)
            .configure_title(anchor="start", fontSize=13),
            mo.md(f"""
        Each cohort = species whose retained assessment is *dated* in that
        displayed band (crosswalk `iucn_assessment_date`; recorded evidence as of
        this export). Observation semantics: the input is expected to retain one row per SIS species, and
        {species_df.filter(pl.col("assess_year").is_not_null()).height:,} of the
        {species_df.height:,} registered rows have cohort dating (the
        remainder have no usable date in the joined crosswalk). Reading: the share of each
        assessment cohort holding a species-level evidence at export
        time, with a linear slope of **{slope_pp:+.1f} percentage points per
        cohort period** ({first_pct:.1f}% → {last_pct:.1f}%). Positive means
        newer assessment cohorts have higher present-day representation;
        assessment dates do not timestamp projects, sampling or genome availability.
        This static comparison cannot establish sequencing progress.
        """),
        ]
    )
    cohort_trend
    return first_pct, last_pct, slope_pp


@app.cell
def _(derived_df, mo, pl):
    _known = derived_df.filter(~pl.col("goat_dd") & pl.col("has_dna").is_not_null())
    _tot = _known.height
    _cov = (
        _known.select(
            pl.when(pl.col("genus_has_dna").is_null() | pl.col("family_has_dna").is_null())
            .then(pl.lit("unknown higher-level evidence"))
            .when(pl.col("has_dna"))
            .then(pl.lit("species-level evidence (samp)"))
            .when(pl.col("genus_has_dna"))
            .then(pl.lit("species absent, genus represented (partial)"))
            .when(pl.col("family_has_dna"))
            .then(pl.lit("genus absent, family represented"))
            .otherwise(pl.lit("no recorded evidence nearby (family absent)"))
            .alias("status")
        )
        .group_by("status")
        .agg(n=pl.len())
        .sort("n", descending=True)
    )
    _row = {r["status"]: r["n"] for r in _cov.iter_rows(named=True)}
    _dark_cross = (
        _known.filter(
            pl.col("has_dna").is_not_null()
            & ~pl.col("has_dna")
            & pl.col("rl5").is_in(["CR", "EN"])
            & (pl.col("ncells") <= 25)
        )
        .select(
            pl.when(pl.col("genus_has_dna").is_null() | pl.col("family_has_dna").is_null())
            .then(pl.lit("unknown higher-level evidence"))
            .when(pl.col("genus_has_dna"))
            .then(pl.lit("genus represented"))
            .when(pl.col("family_has_dna"))
            .then(pl.lit("family represented"))
            .otherwise(pl.lit("family absent"))
            .alias("status")
        )
        .group_by("status")
        .agg(n=pl.len())
    )
    _dc = {r["status"]: r["n"] for r in _dark_cross.iter_rows(named=True)}
    ladder_note = mo.md(f"""
    **Categorical-ladder descriptives for the mapped, GoaT-known universe**
    ({_tot:,} species). These descriptive evidence buckets are separate from
    the unchanged production weights and predicate precedence.

    Among restricted threatened species without recorded species evidence,
    **{_dc.get("genus represented", 0):,}** have a represented genus,
    {_dc.get("family represented", 0):,} have only a represented family,
    {_dc.get("family absent", 0):,} have known false genus and family flags,
    and **{_dc.get("unknown higher-level evidence", 0):,}** have unknown
    higher-level evidence. Unknown flags do not establish lineage absence.

    Across the full known-species cohort,
    {_row.get("species-level evidence (samp)", 0):,} have recorded species evidence,
    {_row.get("species absent, genus represented (partial)", 0):,} have a represented genus,
    {_row.get("genus absent, family represented", 0):,} have only a represented family,
    {_row.get("no recorded evidence nearby (family absent)", 0):,} have known false
    nearby-lineage flags, and {_row.get("unknown higher-level evidence", 0):,}
    have unknown higher-level evidence. Representation means recorded qualifying
    evidence under the website rule, which can include samples or work in progress;
    it does not establish a sequenced relative or completed reference genome.

    The exploratory genus-partial variant and its rank movement run in notebook 3
    §3b. The ladder remains descriptive until validated; neither broad evidence
    flags nor the separate narrow EBP criteria flag certify assembly quality.
    """)
    ladder_note
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 6. Ecosystem scope and the diadromy proxy

    The IUCN assessments table the pipeline served has no migration attribute, so
    migration cannot be studied directly yet. System overlap can be explored descriptively, but it does not identify
    diadromy or migration. Compare representation across the disjoint scope
    buckets, with known evidence as the denominator.
    """)
    return


@app.cell
def _(alt, derived_df, mo, pl):
    SCOPE_COLORS = {
        "terrestrial (+freshwater)": "#6b8e23",
        "freshwater only": "#3f7c8c",
        "marine (+freshwater)": "#2b5b8c",
        "mixed terrestrial+marine": "#90456b",
    }
    _sc = (
        derived_df.filter(
            pl.col("scope").is_not_null() & (~pl.col("goat_dd")) & pl.col("has_dna").is_not_null()
        )
        .group_by("scope")
        .agg(
            n=pl.len(),
            pct_dna=(pl.col("has_dna").cast(pl.Float64).mean() * 100),
        )
        .sort("n", descending=True)
    )
    _y = alt.Y("scope:N", sort=list(_sc["scope"]), title=None)
    _top = (
        alt.Chart(_sc)
        .mark_bar()
        .encode(
            y=_y,
            x=alt.X("n:Q", title="mapped species"),
            color=alt.Color(
                "scope:N",
                legend=None,
                scale=alt.Scale(
                    domain=list(SCOPE_COLORS),
                    range=[SCOPE_COLORS[s] for s in SCOPE_COLORS],
                ),
            ),
            tooltip=[alt.Tooltip("n:Q", title="mapped species")],
        )
        .properties(width=620, height=170, title="Where the mapped species mass sits")
    )
    _bottom = (
        alt.Chart(_sc)
        .mark_tick(thickness=2.5, size=20, opacity=0.9)
        .encode(
            y=_y,
            x=alt.X("pct_dna:Q", title="% species with species-level DNA"),
            color=alt.Color(
                "scope:N",
                legend=None,
                scale=alt.Scale(
                    domain=list(SCOPE_COLORS),
                    range=[SCOPE_COLORS[s] for s in SCOPE_COLORS],
                ),
            ),
            tooltip=[alt.Tooltip("pct_dna:Q", format=".1f", title="% with DNA")],
        )
        .properties(width=620, height=130, title="…and the recorded species evidence in each scope")
    )
    mo.vstack(
        [
            _top.configure_axis(labelFontSize=11, titleFontSize=12),
            _bottom.configure_axis(labelFontSize=11, titleFontSize=12),
        ]
    )
    return


@app.cell
def _(
    RES3_AREA,
    RES7_AREA,
    RES7_SPAN,
    derived_df,
    dna_cr,
    first_pct,
    last_pct,
    map_cr,
    mo,
    model_families,
    model_n,
    model_odds_decade,
    model_r2,
    net_cr,
    net_en,
    pl,
    slope_pp,
    species_df,
):
    _known_summary = derived_df.filter(~pl.col("goat_dd") & pl.col("has_dna").is_not_null())
    _small = _known_summary.filter(pl.col("bucket") == "1–9")
    _large = _known_summary.filter(pl.col("bucket") == "1,000+")
    _pct_small = (_small["has_dna"].cast(pl.Float64).mean() or 0) * 100
    _pct_large = (_large["has_dna"].cast(pl.Float64).mean() or 0) * 100
    _mapped_n = species_df.filter(pl.col("ncells").is_not_null()).height
    _registered = species_df.height
    _dark = derived_df.filter(
        pl.col("has_dna").is_not_null()
        & ~pl.col("has_dna")
        & ~pl.col("goat_dd")
        & pl.col("rl5").is_in(["CR", "EN"])
        & (pl.col("ncells") <= 25)
    )
    _dark_total = _dark.height
    _dark_bycls = {
        r["class_top"]: r["n"]
        for r in _dark.group_by("class_top").agg(n=pl.len()).iter_rows(named=True)
    }
    _plants = _dark_bycls.get("Magnoliopsida", 0)
    _amphibia = _dark_bycls.get("Amphibia", 0)
    _aves = _dark_bycls.get("Aves", 0)
    _dark_ebp_n = _dark.filter(pl.col("ebp")).height
    _none = species_df.filter(pl.col("concept_relation").is_null()).height
    _none_share = _none / species_df.height
    _mapped_dna = derived_df.filter(pl.col("has_dna"))
    _mapped_dna_n = _mapped_dna.height
    _mapped_dna_pairs = [
        (r["class_top"], r["n"])
        for r in _mapped_dna.group_by("class_top")
        .agg(n=pl.len())
        .sort("n", descending=True)
        .head(2)
        .iter_rows(named=True)
    ]
    _unmapped_aves = species_df.filter(
        pl.col("ncells").is_null() & (pl.col("goat_class") == "Aves")
    ).height
    _map_cr_text = f"{map_cr:.2f}" if map_cr is not None else "unavailable"
    _dna_cr_text = f"{dna_cr:.2f}" if dna_cr is not None else "unavailable"
    mo.md(f"""
    ## What this run can tell us

    The mapped cohort contains {_mapped_n:,} of {_registered:,} registered
    species. The complete-observation logit uses {model_n:,} observations
    across {model_families:,} families; its conditional odds ratio per 10×
    footprint is {model_odds_decade:.2f} (pseudo-R² {model_r2:.3f}).
    Consult the family-clustered confidence intervals before interpreting it.

    Mapping and representation are separate conditional associations:
    CR mapping odds ratio {_map_cr_text}; CR representation odds ratio among
    mapped, GoaT-known species {_dna_cr_text}. Unavailable fits or absent terms
    have no numerical interpretation. They do not establish causation,
    population bias, or an end-to-end odds ratio. The dark cohort has
    {_dark_total:,} rows meeting the stated mapped-range and evidence filters.

    Assessment-cohort coverage is a cross-section, not sequencing history.
    Its fitted slope is {slope_pp:+.1f} percentage points per displayed band;
    assessment dates are not dates when genomes became available.
    {_none:,} registered rows have no concept-relation classification.

    Limits: coarse any-touch extent is neither occupancy nor IUCN AOO;
    Criterion B shares range ancestry with threat classification; selected
    mapped species are not global taxonomic coverage. Unknown evidence is
    excluded from negative-evidence denominators. Family clustering does not
    fit random effects, and incomplete taxonomy or spatial products remain
    uncorrected. This is descriptive exploration, not a production model.
    """)
    return


if __name__ == "__main__":
    app.run()
