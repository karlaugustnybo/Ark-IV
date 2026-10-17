<svelte:head>
  <title>Methodology · Ark-IV</title>
  <meta
    name="description"
    content="How Ark-IV constructs potential mapped occurrence and configurable genome-sampling priority from IUCN and GoaT data."
  />
</svelte:head>

<main class="main-content">
  <div class="tutorial-container methodology-page">
    <header class="tutorial-header">
      <p class="about-kicker">Methods, assumptions &amp; provenance</p>
      <h1>Methodology</h1>
      <p class="tutorial-intro">
        Ark-<i>IV</i> combines mapped species ranges, Red List assessments, and genome-project evidence
        to explore potential species richness and configurable genome-sampling priorities. It does not
        measure abundance, habitat suitability, local extinction probability, or the expected benefit
        of an intervention.
      </p>
    </header>

    <section class="tutorial-section">
      <div class="tutorial-subsection layout-single-column is-visible">
        <div class="about-content">
          <h2>Sources have distinct roles</h2>
          <p>
            No single dataset supplies a complete conservation priority. Source identifiers remain
            distinct, and data that are acquired are not necessarily used in the present analytical
            build.
          </p>
        </div>
        <ul class="source-ledger">
          <li>
            <span class="source-index">01</span>
            <div>
              <strong
                ><a href="https://www.iucnredlist.org/search" target="_blank" rel="noreferrer"
                  >IUCN Red List</a
                ></strong
              >
              <p>
                Stable SIS taxon and assessment IDs, scientific names, extinction-risk categories,
                habitat systems, range polygons, occurrence points, and species-to-basin
                relationships.
              </p>
            </div>
          </li>
          <li>
            <span class="source-index">02</span>
            <div>
              <strong
                ><a href="https://goat.genomehubs.org/" target="_blank" rel="noreferrer"
                  >Genomes on a Tree (GoaT) and NCBI Taxonomy</a
                ></strong
              >
              <p>
                Taxon identifiers, names, lineage, and sequencing-project evidence. Missing,
                inferred, in-progress, and completed evidence require different interpretations.
              </p>
            </div>
          </li>
          <li>
            <span class="source-index">03</span>
            <div>
              <strong
                ><a
                  href="https://www.gbif.org/dataset/d7dddbf4-2cf0-4f39-9b2a-bb099caae36c"
                  target="_blank"
                  rel="noreferrer">GBIF Backbone Taxonomy</a
                ></strong
              >
              <p>
                Supplies GBIF taxon IDs for species-name hotlinks to GBIF records when a unique
                accepted-species match is available. GBIF is not used by the current IUCN-to-GoaT
                crosswalk and does not affect identity, coverage, or priority scores.
              </p>
            </div>
          </li>
          <li>
            <span class="source-index">04</span>
            <div>
              <strong
                ><a
                  href="https://www.hydrosheds.org/products/hydrobasins"
                  target="_blank"
                  rel="noreferrer">HydroSHEDS HydroBASINS</a
                ></strong
              >
              <p>
                Versioned basin polygons joined to IUCN freshwater relationships by HYBAS_ID. They
                provide mapped distribution units, not point observations or habitat-suitability
                models.
              </p>
            </div>
          </li>
          <li>
            <span class="source-index">05</span>
            <div>
              <strong>Boundary frameworks</strong>
              <p>
                Natural Earth, Marine Regions, geoBoundaries, and RESOLVE supply filtering and
                location context. Boundary geometry is separate from species-range geometry.
              </p>
            </div>
          </li>
        </ul>
      </div>
      <div class="section-divider"><span>#</span></div>
    </section>

    <section class="tutorial-section">
      <div class="tutorial-subsection layout-single-column is-visible">
        <div class="about-content">
          <h2>How a species record moves through Ark-IV</h2>
          <p>
            The IUCN SIS taxon ID remains the stable application key. Exact source identifiers are
            attached without treating different taxonomic concepts as interchangeable.
          </p>
        </div>
        <ol class="data-flow">
          <li>
            <span>1</span>
            <div>
              <strong>Select eligible IUCN records</strong>
              <p>
                Keep extant or possibly extinct, native/reintroduced/assisted-colonisation records
                in the accepted resident, breeding, non-breeding, or uncertain seasons.
              </p>
            </div>
          </li>
          <li>
            <span>2</span>
            <div>
              <strong>Preserve taxon identity</strong>
              <p>
                Use the IUCN SIS taxon ID as the application key and attach a reviewed GoaT/NCBI
                match where supported. A separate GBIF taxon ID may supply an outbound hotlink.
              </p>
            </div>
          </li>
          <li>
            <span>3</span>
            <div>
              <strong>Build spatial membership</strong>
              <p>
                Add a species when an eligible polygon or basin touches a resolution-7 H3 cell, or
                when an eligible point falls inside it. Collapse duplicate species/cell pairs.
              </p>
            </div>
          </li>
          <li>
            <span>4</span>
            <div>
              <strong>Evaluate genomic representation</strong>
              <p>
                Reduce qualifying GoaT evidence to mutually exclusive species, genus, family,
                sampled, or data-deficient buckets, then aggregate the configured score.
              </p>
            </div>
          </li>
        </ol>
        <aside class="deficiency-note" aria-labelledby="spatial-meaning">
          <h3 id="spatial-meaning">What a map cell means</h3>
          <div class="definition-grid">
            <div>
              <span class="definition-source">Included</span><strong
                >Potential mapped occurrence</strong
              >
              <p>
                A species is counted once when at least one eligible source supplies cell
                membership. This is not proof that it occupies the entire hexagon.
              </p>
            </div>
            <div>
              <span class="definition-source">Absent</span><strong
                >No eligible coverage produced</strong
              >
              <p>
                Absence from the output is not an observed biological absence. Source coverage and
                reporting are uneven.
              </p>
            </div>
          </div>
        </aside>
      </div>
      <div class="section-divider"><span>#</span></div>
    </section>

    <section class="tutorial-section">
      <div class="tutorial-subsection layout-single-column is-visible">
        <div class="about-content">
          <h2>What counts as qualifying DNA evidence?</h2>
          <p>
            The current predicate is deliberately broad: it can indicate an acquired sample, work in
            progress, a reported standard, or a qualifying assembly. These are different
            achievements, so “represented” does not necessarily mean that a completed, usable
            reference genome exists.
          </p>
        </div>
        <div class="tutorial-table-wrapper about-table-wrapper">
          <table class="tutorial-table about-table">
            <thead><tr><th>Order</th><th>Evidence rule</th><th>DNA evidence</th></tr></thead><tbody>
              <tr
                ><td>1</td><td
                  >GoaT reports a non-empty <code>sample_acquired</code> or <code>in_progress</code> value</td
                ><td><strong>Present</strong></td></tr
              >
              <tr
                ><td>2</td><td
                  >A safely linked record reports a non-empty <code>ebp_standard_criteria</code> value</td
                ><td><strong>Present</strong></td></tr
              >
              <tr
                ><td>3</td><td
                  >A safely linked chromosome/complete-genome assembly has BUSCO completeness ≥ 90%</td
                ><td><strong>Present</strong></td></tr
              >
              <tr><td>4</td><td>No rule above is satisfied</td><td><strong>Missing</strong></td></tr
              >
            </tbody>
          </table>
        </div>
        <p class="about-method-note">
          Resampling markers and sequencing status do not currently alter this predicate.
        </p>
      </div>
      <div class="section-divider"><span>#</span></div>
    </section>

    <section class="tutorial-section">
      <div class="tutorial-subsection layout-single-column is-visible">
        <div class="about-content">
          <h2>How the DNA gap is selected</h2>
          <p>
            Each species receives one mutually exclusive DNA-gap category. The first matching
            condition wins, so the table and map apply exactly one DNA weight per species.
          </p>
        </div>
        <div class="tutorial-table-wrapper about-table-wrapper">
          <table class="tutorial-table about-table">
            <thead><tr><th>Order</th><th>Condition</th><th>DNA gap</th><th>Default</th></tr></thead
            ><tbody>
              <tr
                ><td>1</td><td>No populated GoaT taxon match</td><td
                  ><span class="method-pill method-gdd">GoaT Data Deficient</span></td
                ><td>4.0</td></tr
              >
              <tr
                ><td>2</td><td>No DNA evidence in the family</td><td
                  ><span class="method-pill method-family">Missing Family</span></td
                ><td>4.0</td></tr
              >
              <tr
                ><td>3</td><td>Family covered, but no evidence in the genus</td><td
                  ><span class="method-pill method-genus">Missing Genus</span></td
                ><td>3.0</td></tr
              >
              <tr
                ><td>4</td><td>Genus covered, but no evidence for the species</td><td
                  ><span class="method-pill method-species">Missing Species</span></td
                ><td>2.0</td></tr
              >
              <tr
                ><td>5</td><td>Species-level evidence is present</td><td
                  ><span class="method-pill method-sampled">Already Sampled</span></td
                ><td>0.0</td></tr
              >
            </tbody>
          </table>
        </div>
        <aside class="deficiency-note" aria-labelledby="two-deficiencies">
          <h3 id="two-deficiencies">Two kinds of “data deficient”</h3>
          <div class="definition-grid">
            <div>
              <span class="definition-source">IUCN</span><strong>Data Deficient</strong>
              <p>
                There is not enough conservation information to assess extinction risk. This affects
                the threat side of the score.
              </p>
            </div>
            <div>
              <span class="definition-source">GoaT</span><strong>Data Deficient</strong>
              <p>
                Ark-IV could not find a populated GoaT taxon match. This affects the DNA-gap side
                and is not a claim that DNA is confirmed absent.
              </p>
            </div>
          </div>
        </aside>
      </div>
      <div class="section-divider"><span>#</span></div>
    </section>

    <section class="tutorial-section">
      <div class="tutorial-subsection layout-single-column is-visible">
        <div class="about-content">
          <h2>How priority is calculated</h2>
          <p class="formula-line">
            <span>Species priority</span><b>=</b><span>IUCN weight</span><b>×</b><span
              >DNA-gap weight</span
            >
          </p>
          <p>
            A cell’s priority is the sum across its distinct included species. The normalized view
            divides that sum by richness, producing mean included-species priority—not
            area-normalized priority. Weights are adjustable heuristics, not extinction
            probabilities or an empirically validated allocation model.
          </p>
        </div>
        <div class="score-table-grid">
          <div class="tutorial-table-wrapper about-table-wrapper">
            <table class="tutorial-table about-table">
              <thead><tr><th>IUCN category</th><th>Default</th></tr></thead><tbody
                ><tr><td>Critically Endangered</td><td>4.0</td></tr><tr
                  ><td>Endangered</td><td>3.0</td></tr
                ><tr><td>Vulnerable</td><td>2.0</td></tr><tr
                  ><td>Near Threatened</td><td>1.0</td></tr
                ><tr><td>Data Deficient</td><td>2.0</td></tr><tr
                  ><td>Least Concern</td><td>0.1</td></tr
                ></tbody
              >
            </table>
          </div>
          <div class="tutorial-table-wrapper about-table-wrapper">
            <table class="tutorial-table about-table">
              <thead><tr><th>DNA gap</th><th>Default</th></tr></thead><tbody
                ><tr><td>GoaT Data Deficient</td><td>4.0</td></tr><tr
                  ><td>Missing Family</td><td>4.0</td></tr
                ><tr><td>Missing Genus</td><td>3.0</td></tr><tr
                  ><td>Missing Species</td><td>2.0</td></tr
                ><tr><td>Already Sampled</td><td>0.0</td></tr></tbody
              >
            </table>
          </div>
        </div>
      </div>
      <div class="section-divider"><span>#</span></div>
    </section>

    <section class="tutorial-section">
      <div class="tutorial-subsection layout-single-column is-visible">
        <div class="about-content">
          <h2>Licences and credits</h2>
          <p>
            Ark-IV application code is available under the <a
              href="https://github.com/karlaugustnybo/DIKUs-Ark/blob/main/LICENSE"
              target="_blank"
              rel="noreferrer">repository licence</a
            >. That code licence does not grant permission to redistribute a dataset. The public
            code release excludes IUCN rows, global distributions, and generated serving databases.
          </p>
        </div>
        <ul class="source-ledger">
          <li>
            <span class="source-index">IUCN</span>
            <div>
              <strong>IUCN 2026, Red List version 2026-1</strong>
              <p>
                Used under the <a
                  href="https://www.iucnredlist.org/terms/terms-of-use"
                  target="_blank"
                  rel="noreferrer">IUCN Red List Terms of Use</a
                >; raw, tabular, and spatial data are not bundled. Browser-delivered derivatives and
                APIs require a separate publication review.
              </p>
            </div>
          </li>
          <li>
            <span class="source-index">HYDRO</span>
            <div>
              <strong
                ><a
                  href="https://www.hydrosheds.org/products/hydrobasins"
                  target="_blank"
                  rel="noreferrer">HydroBASINS v1c</a
                ></strong
              >
              <p>
                Standard level 1–12 basin geometry from HydroSHEDS. Provider terms and attribution
                apply to basin-derived products.
              </p>
            </div>
          </li>
          <li>
            <span class="source-index">GBIF</span>
            <div>
              <strong
                ><a href="https://doi.org/10.15468/39omei" target="_blank" rel="noreferrer"
                  >GBIF Backbone Taxonomy (2023)</a
                ></strong
              >
              <p>
                Used to provide outbound species-record hotlinks. CC BY 4.0; accessed 26 August
                2026.
              </p>
            </div>
          </li>
          <li>
            <span class="source-index">MAP</span>
            <div>
              <strong>Boundary and basemap sources</strong>
              <p>
                <a
                  href="https://www.naturalearthdata.com/about/terms-of-use/"
                  target="_blank"
                  rel="noreferrer">Natural Earth</a
                >
                (public domain),
                <a href="https://www.geoboundaries.org/api.html" target="_blank" rel="noreferrer"
                  >geoBoundaries gbOpen</a
                >
                and
                <a href="https://ecoregions.appspot.com/" target="_blank" rel="noreferrer"
                  >RESOLVE Ecoregions 2017</a
                >
                (CC BY 4.0), and
                <a href="https://www.marineregions.org/sources.php" target="_blank" rel="noreferrer"
                  >Marine Regions World EEZ v12</a
                > (CC BY 4.0). The basemap retains © OpenStreetMap contributors © CARTO in-map attribution.
              </p>
            </div>
          </li>
        </ul>
      </div>
      <div class="section-divider"><span>#</span></div>
    </section>

    <section class="tutorial-section layout-single-column about-notes">
      <div class="about-content">
        <h2>Interpretation and validation</h2>
        <p>
          This page summarizes implemented behavior and open scientific assumptions; it is not a
          claim of independent biological validation.
        </p>
      </div>
      <dl class="method-notes">
        <div>
          <dt>Taxonomy</dt>
          <dd>
            IUCN taxon IDs stay primary. GoaT/NCBI identifiers support the genomic-data match; GBIF
            identifiers are used only for outbound species hotlinks. Unresolved identifiers remain
            absent.
          </dd>
        </div>
        <div>
          <dt>Taxon identity</dt>
          <dd>
            Ark-IV never deduplicates distinct IUCN taxa by a shared NCBI concept; this avoids
            silently collapsing splits and lumps.
          </dd>
        </div>
        <div>
          <dt>Search</dt>
          <dd>
            Species and family matching treats input as ordinary text, ignores case and accents,
            ranks exact and prefix matches first, and tolerates minor misspellings.
          </dd>
        </div>
        <div>
          <dt>System classification</dt>
          <dd>
            IUCN classifies species as terrestrial, freshwater, marine, or a combination. Counts can
            overlap across systems and must not be added to reconstruct the all-system total; labels
            do not geographically mask a range to habitat.
          </dd>
        </div>
        <div>
          <dt>Spatial evidence</dt>
          <dd>
            A displayed membership can come from an eligible range polygon, a point’s containing
            fine cell, or an IUCN-to-HydroBASINS relationship. Duplicate memberships are collapsed,
            so the map does not show observation counts or distinguish evidence type in the current
            serving generation.
          </dd>
        </div>
        <div>
          <dt>Boundary filters</dt>
          <dd>
            Filters operate on cell memberships. A species range and a boundary can touch different
            parts of the same cell, especially at coarse resolution, so results are species
            associated with selected cells—not confirmed jurisdiction occurrences.
          </dd>
        </div>
        <div>
          <dt>Map colour</dt>
          <dd>
            Colour domains can vary with resolution, filters, weights, and loaded cells. A colour is
            not an absolute cross-region rank; compare the numeric score and domain.
          </dd>
        </div>
        <div>
          <dt>Validation status</dt>
          <dd>
            Tests and receipts check implemented behavior, reconciliation, and provenance. They
            cannot prove biological accuracy, catch every wrong taxonomic match, or validate the
            default weights.
          </dd>
        </div>
        <div>
          <dt>Terms and citation</dt>
          <dd>
            Source links do not replace dataset-specific terms. IUCN data use is restricted to
            permitted non-commercial conservation, education, and research use; downstream releases
            must retain current source citations and licensing notices.
          </dd>
        </div>
      </dl>
    </section>
  </div>
</main>
