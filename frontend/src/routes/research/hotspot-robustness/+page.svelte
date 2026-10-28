<script lang="ts">
  import ResearchArticle from '$lib/components/ResearchArticle.svelte';
  import { researchEntry } from '$lib/research';

  const entry = researchEntry('hotspot-robustness');

  const robustnessBars = [
    { label: '≥ 95%', value: 1477 },
    { label: '50–95%', value: 390 },
    { label: '< 50%', value: 153 }
  ];
  const agreement = [
    { measure: 'Default buckets', spearman: 0.944, top5: 0.886, top1: 0.844 },
    { measure: 'Flat buckets', spearman: 0.935, top5: 0.828, top1: 0.849 },
    { measure: 'Exact-species buckets', spearman: 0.931, top5: 0.765, top1: 0.829 },
    { measure: 'Gap, upper bound', spearman: 0.953, top5: 0.828, top1: 0.779 },
    { measure: 'Gap, fair share', spearman: 0.949, top5: 0.806, top1: 0.671 },
    { measure: 'Gap, scored only', spearman: 0.937, top5: 0.798, top1: 0.584 }
  ];
  const countryShift = [
    { country: 'Australia', from: 209, to: 266 },
    { country: 'Colombia', from: 29, to: 8 },
    { country: 'Brazil', from: 23, to: 10 },
    { country: 'Mexico', from: 22, to: 4 },
    { country: 'Ecuador', from: 16, to: 3 },
    { country: 'United States', from: 12, to: 1 },
    { country: 'Peru', from: 10, to: 0 },
    { country: 'Senegal', from: 0, to: 12 }
  ];
</script>

<ResearchArticle {entry}>
  <p>
    A priority map is only useful if its brightest cells do not depend on arbitrary choices.
    Ark-IV’s default score multiplies a Red List weight by a DNA-gap weight and sums it over the
    species mapped to each cell. The weights are heuristics, the DNA buckets are a coarse proxy for
    evolutionary distance, and the taxonomy match between IUCN and genome databases is imperfect.
    Which hotspots survive reasonable changes to these choices, and what does replacing the buckets
    with branch length on a dated tree of life do to the picture?
  </p>

  <h2>How the specifications were built</h2>
  <p>
    The analysis uses the 25 October 2026 build: 115,408 mapped species, scored on resolution-3 H3
    cells. Phylogenetic gaps come from the same run’s dated global tree, measured as rooted marginal
    branch length against the 16,818 tips represented under the website DNA rule.
  </p>
  <p>
    Five factors are crossed: Data Deficient weight (2, 0, 1, 4), DNA measure (the default buckets,
    exact-species, flat, and three phylogenetic gap levels), range rarity (off, on), taxonomy
    transfer (all registered, safe transfer only, matched only) and assessment window (all, 2015
    onwards). That gives 4 × 6 × 2 × 3 × 2 = 288 specifications, each weighted equally.
  </p>
  <p>
    For each cell, <code>robustness(c)</code> is the share of specifications under which it stays in the
    top 5%. Range rarity turns the score into a different question (irreplaceability rather than priority
    density), so robustness is reported over the 144 rarity-off specifications. Rank agreement uses Spearman
    correlation over all cells and Jaccard overlap of the top 1% and 5%, with deterministic tie-breaking.
  </p>
  <aside class="research-note" aria-labelledby="gap-levels">
    <h3 id="gap-levels">Three phylogenetic gap levels</h3>
    <dl>
      <div>
        <dt>Scored only</dt>
        <dd>
          The pipeline’s stored gap. Species with an unsafe genomic identity, no tree identity, or
          an incomplete baseline contribute nothing.
        </dd>
      </div>
      <div>
        <dt>Upper bound</dt>
        <dd>
          The marginal gap <i>PD(R ∪ &#123;s&#125;) − PD(R)</i> for every species placed on the tree,
          assuming withheld species are unsequenced unless represented.
        </dd>
      </div>
      <div>
        <dt>Fair share</dt>
        <dd>
          Each uncovered branch split equally among the unsequenced species below it, so the shares
          sum to the uncovered branch union.
        </dd>
      </div>
    </dl>
  </aside>

  <h2>Most hotspots are robust</h2>
  <p>
    Of the 2,020 cells in the default top 5%, 1,477 stay in it under at least 95% of the rarity-off
    specifications. Only 153 survive fewer than half. A further 139 cells outside the default top 5%
    enter it under at least half, and 2,769 cells are in it at least once.
  </p>
  <figure>
    <div class="bar-chart" role="img" aria-label="Robustness of the 2,020 default top-5% cells">
      {#each robustnessBars as bar (bar.label)}
        <div class="bar-row">
          <span>{bar.label}</span>
          <span class="bar-track"><i style:width={`${(bar.value / 2020) * 100}%`}></i></span>
          <b>{bar.value.toLocaleString('en')}</b>
        </div>
      {/each}
    </div>
    <figcaption>
      Default top-5% resolution-3 cells by the share of 144 rarity-off specifications under which
      they remain in the top 5%.
    </figcaption>
  </figure>
  <p>
    The robust core is concentrated in Indonesia, Papua New Guinea, the Philippines, Vietnam,
    Thailand, Myanmar and Fiji. The fragile hotspots cluster elsewhere: France’s overseas
    territories, Madagascar, Mozambique and Colombia (1 of 29 top cells is core). The Marshall
    Islands have no core cell but 17 entrants.
  </p>
  <p>
    Rank order across all cells barely moves (mean Spearman 0.93–0.95 for every DNA measure). The
    tail moves more, and how much depends on the DNA measure.
  </p>
  <div class="table-wrapper">
    <table>
      <caption>Mean agreement with the default score over rarity-off specifications.</caption>
      <thead><tr><th>DNA measure</th><th>Spearman</th><th>Top 5%</th><th>Top 1%</th></tr></thead>
      <tbody>
        {#each agreement as row (row.measure)}
          <tr>
            <td>{row.measure}</td>
            <td>{row.spearman.toFixed(3)}</td>
            <td>{row.top5.toFixed(3)}</td>
            <td>{row.top1.toFixed(3)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  <p>
    Range rarity is by far the largest lever. Switched on, mean top-5% overlap with the default
    falls to 0.22. It should be offered as a separate surface, not treated as a sensitivity of the
    default.
  </p>

  <h2>Missing evidence behaves like zero evidence when it is dropped</h2>
  <p>
    Of the 115,408 mapped species, 68,239 have a stored gap. The rest are withheld: 30,395 for an
    unsafe genomic identity, 12,416 without a tree identity, 2,464 with an incomplete baseline, and
    1,894 outside the generation. Scoring only stored gaps silently treats these 41% as fully
    sequenced.
  </p>
  <p>
    That is visible in the tail. Against the default at baseline settings, top-1% overlap is 0.61
    for stored gaps only and 0.82 for the upper bound. Withheld species are not a random sample
    either: the median upper-bound gap of an unsafe-identity species is 45.3 Myr, against 37.5 Myr
    for scored species. The species table therefore ranks withheld species at their upper bound by
    default, marked with ≤.
  </p>

  <h2>Shared branches are counted once per sister</h2>
  <p>
    A marginal gap answers “what does sequencing only this species add?”. It is the right quantity
    for ranking species one at a time, but not for adding up. Three unsequenced sisters each claim
    their whole shared stem. Summed over all placed species, marginal upper bounds total 7.55
    million Myr. The uncovered branch union they describe is 1.69 million Myr, so a cell sum
    overstates shared history 4.5 times.
  </p>
  <p>
    Fair shares sum exactly to the union. Using them moves hotspots: species-rich neotropical
    clades, whose species share long unsequenced stems, lose top-5% cells; regions with isolated
    lineages gain them.
  </p>
  <div class="table-wrapper">
    <table>
      <caption>
        Top-5% cell contacts per country, default score versus fair-share upper bound. Contacts
        overlap and include offshore cells.
      </caption>
      <thead><tr><th>Country</th><th>Default</th><th>Fair share</th></tr></thead>
      <tbody>
        {#each countryShift as row (row.country)}
          <tr><td>{row.country}</td><td>{row.from}</td><td>{row.to}</td></tr>
        {/each}
      </tbody>
    </table>
  </div>
  <p>
    At baseline settings, top-1% overlap (Jaccard) is 0.76 between the upper bound and fair share,
    but only 0.59 between stored gaps and fair share: how missing evidence is handled matters about
    as much as how shared branches are counted.
  </p>

  <h2>Both cell surfaces mostly measure richness</h2>
  <p>
    Summing EDGE2 per cell agrees with the default score (Spearman 0.961, top-5% overlap 0.80) about
    as well as a plain count of threatened species does (0.930, 0.78). Any score summed over species
    inherits richness. Comparing cells by mean rather than total priority, or by joint branch gain,
    separates priority from richness.
  </p>

  <h2>What this changes for Ark-IV</h2>
  <p>
    The weights are not where the uncertainty lives. Data Deficient weight, assessment age and
    taxonomy filtering shift a few marginal cells; the robust core survives them. The consequential
    choices are structural: whether missing evidence is dropped or bounded, whether shared history
    is counted once, and whether rarity is part of the question.
  </p>
  <p>
    That leads to two quantities for two questions. For a single species, the marginal gap is the
    gain from sequencing it, and the species table ranks by expected history lost, extinction
    probability × marginal gap. For a place, the fair share is the portion of remaining unsequenced
    history its species are responsible for, which can be summed across a cell. Neither is a
    sampling plan: sequencing one of three sisters recovers the whole shared stem, not a third of
    it. Choosing several cells needs the joint branch union, which belongs in a portfolio mode.
  </p>

  <h2>Limits</h2>
  <dl class="limits">
    <div>
      <dt>Scope</dt>
      <dd>
        One snapshot and the listed specifications. Geometry rules, crosswalk match quality and
        per-class scoring are not yet in the grid. Robust here means robust to these choices, not
        validated against an independent conservation objective.
      </dd>
    </div>
    <div>
      <dt>Bounds</dt>
      <dd>
        Upper bounds overstate gaps for withheld species that are in fact sequenced under an
        identity Ark-IV could not match safely. Species without a tree identity have no bound and
        stay unscored.
      </dd>
    </div>
    <div>
      <dt>Tree</dt>
      <dd>
        Gaps use a single dated tree. Topology and dating uncertainty are not propagated, and EDGE2
        risk summaries do not represent full tree uncertainty.
      </dd>
    </div>
    <div>
      <dt>Map</dt>
      <dd>
        Map colours still use the bucket score. A fair-share map layer needs per-cell sums by threat
        category and a serving rebuild; it is on the roadmap.
      </dd>
    </div>
    <div>
      <dt>Reproduce</dt>
      <dd>
        The marimo notebook <code>notebooks/hotspot_robustness.py</code> recomputes every number here
        from local exports. Gap bounds are rebuilt from the tree and must match the stored gaps exactly.
      </dd>
    </div>
  </dl>
</ResearchArticle>

<style>
  h3 {
    margin: 0 0 var(--space-xs);
    color: var(--color-accent-brown);
    font: 400 var(--font-size-md) / var(--leading-snug) var(--font-serif);
  }
  .research-note {
    margin: var(--space-m) 0;
    padding: var(--space-s);
    border: 1px solid var(--color-tinted-cream);
    border-radius: var(--border-radius-base);
    background: var(--color-light-cream);
  }
  dl {
    display: grid;
    gap: var(--space-xs);
    margin: 0;
  }
  dl div {
    display: grid;
    grid-template-columns: 7rem 1fr;
    gap: var(--space-s);
  }
  dt {
    color: var(--color-gray-700);
    font: 600 var(--font-size-xs) / 1.9 var(--font-sans);
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }
  dd {
    margin: 0;
    line-height: var(--leading-base);
  }
  .limits div {
    padding-bottom: var(--space-xs);
    border-bottom: 1px solid var(--color-tinted-cream);
  }
  figure {
    margin: var(--space-m) 0;
  }
  .bar-chart {
    display: grid;
    gap: 0.4rem;
    padding: var(--space-s) 0;
    border-block: 1px solid var(--color-tinted-cream);
    font: var(--font-size-xs) / 1.2 var(--font-sans);
  }
  .bar-row {
    display: grid;
    grid-template-columns: 4rem 1fr 3.5rem;
    gap: 0.6rem;
    align-items: center;
  }
  .bar-track i {
    display: block;
    height: 0.6rem;
    border-radius: 2px;
    background: var(--color-tertiary-green);
  }
  .bar-row b {
    font-variant-numeric: tabular-nums;
    font-weight: 600;
  }
  .table-wrapper {
    margin: var(--space-m) 0;
    overflow-x: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font: var(--font-size-sm) / 1.4 var(--font-sans);
  }
  th,
  td {
    padding: 0.45rem 0.6rem;
    border-bottom: 1px solid var(--color-tinted-cream);
    text-align: right;
  }
  th:first-child,
  td:first-child {
    text-align: left;
  }
  th {
    color: var(--color-gray-700);
    font-weight: 600;
  }
  td {
    font-variant-numeric: tabular-nums;
  }
  figcaption,
  caption {
    margin-top: 0.5rem;
    color: var(--color-gray-700);
    font: var(--font-size-xs) / 1.4 var(--font-sans);
    text-align: left;
    caption-side: bottom;
  }
  @media (max-width: 550px) {
    dl div {
      grid-template-columns: 1fr;
      gap: 0;
    }
  }
</style>
