<script lang="ts">
  import SourceLink from '$lib/components/SourceLink.svelte';
  import { gbifSpeciesUrl, goatTaxonUrl, iucnSpeciesUrl } from '$lib/sourceLinks';
  import type { SpeciesSortKey, SpeciesTableRow } from '$lib/species-table';

  export let rows: SpeciesTableRow[] = [];
  export let sort: SpeciesSortKey = 'priority';
  export let order: 'asc' | 'desc' = 'desc';
  export let loading = false;
  export let emptyMessage = 'No species match these filters.';
  export let onSort: (key: SpeciesSortKey) => void;
  /** Show unsequenced-history and EDGE2 columns in place of Family; rows must carry
   *  the phylogenetic fields. */
  export let phylogeny = false;

  type Column = { key: SpeciesSortKey; label: string; numeric?: boolean };
  const speciesColumn: Column = { key: 'species_name', label: 'Species' };
  const familyColumn: Column = { key: 'family', label: 'Family' };
  const statusColumns: Column[] = [
    { key: 'redlist_category', label: 'IUCN status' },
    { key: 'dna_level', label: 'DNA gap' }
  ];
  const phylogenyColumns: Column[] = [
    { key: 'unsequenced_myr', label: 'Unsequenced history', numeric: true },
    { key: 'edge2', label: 'EDGE2', numeric: true }
  ];
  const priorityColumn: Column = { key: 'priority', label: 'Priority', numeric: true };
  $: columns = phylogeny
    ? [speciesColumn, ...statusColumns, ...phylogenyColumns, priorityColumn]
    : [speciesColumn, familyColumn, ...statusColumns, priorityColumn];

  // Why a species' stored gap is withheld or missing. Withheld species (unsafe
  // identity, incomplete baseline) still have an upper bound; unplaced ones do not.
  const unavailable: Record<string, string> = {
    unavailable_tree_identity:
      'Not placed on the dated tree, so no unsequenced history can be computed, not even an upper bound',
    unsafe_genomic_identity:
      'Withheld: genome records could not be safely matched to this species, so it may already be sequenced',
    baseline_incomplete:
      'Withheld: a sequenced relative could not be placed on the tree, so the true gap may be smaller',
    not_loaded: 'No phylogenetic data loaded'
  };
  const boundNote = ' Shown as an upper bound that assumes this species has no DNA.';

  function myr(value: number): string {
    return value === 0 ? '0' : value.toFixed(1);
  }

  function statusClass(category: string): string {
    return (
      (
        {
          'Critically Endangered': 'status-cr',
          Endangered: 'status-en',
          Vulnerable: 'status-vu',
          'Near Threatened': 'status-nt',
          'Least Concern': 'status-lc',
          'Data Deficient': 'status-dd'
        } as Record<string, string>
      )[category] ?? 'status-na'
    );
  }

  function ariaSort(
    key: SpeciesSortKey,
    activeSort: SpeciesSortKey,
    activeOrder: 'asc' | 'desc'
  ): 'ascending' | 'descending' | 'none' {
    return activeSort === key ? (activeOrder === 'asc' ? 'ascending' : 'descending') : 'none';
  }

  function chooseSort(event: Event) {
    const next = (event.currentTarget as HTMLSelectElement).value as SpeciesSortKey;
    if (next !== sort) onSort(next);
  }
</script>

<div class="species-table-frame" aria-busy={loading}>
  <div class="mobile-table-sort">
    <label
      >Sort by <select value={sort} on:change={chooseSort}
        >{#each columns as column}<option value={column.key}>{column.label}</option>{/each}</select
      ></label
    >
    <button
      class="btn-secondary"
      type="button"
      on:click={() => onSort(sort)}
      aria-label={`Sort ${order === 'asc' ? 'descending' : 'ascending'}`}
    >
      {order === 'asc' ? 'Ascending ↑' : 'Descending ↓'}
    </button>
  </div>
  <div class="table-scroll" role="region" aria-label="Species results">
    <table class="species-data-table" class:with-phylogeny={phylogeny}>
      <colgroup>
        <col class="species-column" />
        {#if !phylogeny}<col class="family-column" />{/if}
        <col class="status-column" />
        <col class="dna-column" />
        {#if phylogeny}<col class="gap-column" /><col class="edge2-column" />{/if}
        <col class="priority-column" />
      </colgroup>
      <thead
        ><tr
          >{#each columns as column}<th
              class:numeric-column={column.numeric}
              aria-sort={ariaSort(column.key, sort, order)}
              ><button class="table-sort-link" type="button" on:click={() => onSort(column.key)}
                ><span>{column.label}</span><span
                  class:sort-active={sort === column.key}
                  class="sort-indicator"
                  aria-hidden="true"
                  >{sort === column.key ? (order === 'asc' ? '↑' : '↓') : '↕'}</span
                ></button
              ></th
            >{/each}</tr
        ></thead
      >
      <tbody>
        {#if loading && !rows.length}
          <tr><td colspan={columns.length} class="empty-row">Loading species…</td></tr>
        {:else if rows.length}
          {#each rows as row}
            {@const gbifHref = gbifSpeciesUrl(row.gbif_taxon_id)}
            {@const goatHref = goatTaxonUrl(row.goat_taxon_id)}
            <tr>
              <td data-label="Species" class="species-name"
                >{#if gbifHref}<SourceLink
                    source="GBIF"
                    href={gbifHref}
                    context={row.species_name}
                    fused
                    className="species-source-link"><em>{row.species_name}</em></SourceLink
                  >{:else}<em>{row.species_name}</em>{/if}
                {#if row.metadata_status && row.metadata_status !== 'assessed'}
                  <small title="No matching assessment; source category is not used for scoring"
                    >Spatial source only{row.spatial_redlist_category
                      ? ` · ${row.spatial_redlist_category}`
                      : ''}</small
                  >
                {:else if row.iucn_assessment_latest === false}
                  <small
                    title="IUCN marks this assessment as historical; its recorded category is retained"
                    >Historical assessment</small
                  >
                {/if}</td
              >
              {#if !phylogeny}<td data-label="Family" class="family-cell">{row.family || '—'}</td
                >{/if}
              <td data-label="IUCN status"
                >{#if row.metadata_status && row.metadata_status !== 'assessed'}<span
                    class="status-badge status-na">Assessment unavailable</span
                  >{:else}<SourceLink
                    source="IUCN"
                    href={iucnSpeciesUrl(row.species_name, row.iucn_sis_id, row.iucn_assessment_id)}
                    context={row.species_name}
                    fused
                    className={`status-badge ${statusClass(row.redlist_category)}`}
                    >{row.redlist_category}</SourceLink
                  >{/if}</td
              >
              <td data-label="DNA gap"
                >{#if goatHref}<SourceLink
                    source="GoaT"
                    href={goatHref}
                    context={row.species_name}
                    fused
                    className={`dna-badge dna-${row.dnaStatus}`}>{row.dnaLabel}</SourceLink
                  >{:else}<span class={`dna-badge dna-${row.dnaStatus}`}>{row.dnaLabel}</span
                  >{/if}</td
              >
              {#if phylogeny}
                {@const reason = row.phylogenetic_status
                  ? unavailable[row.phylogenetic_status]
                  : undefined}
                <td data-label="Unsequenced history" class="score-cell"
                  >{#if row.gap_myr != null}<span
                      class="score-value"
                      title={reason
                        ? reason + (row.bounded ? boundNote : '')
                        : 'Million years of branch length with no sequenced genome'}
                      >{row.bounded ? '≤ ' : ''}{myr(row.gap_myr)} Myr</span
                    >{:else}<span class="score-value missing-value" title={reason}>—</span>{/if}</td
                >
                <td data-label="EDGE2" class="score-cell"
                  >{#if row.edge2_myr_median != null}<span
                      class="score-value"
                      title={row.edge2_myr_p025 != null && row.edge2_myr_p975 != null
                        ? `95% interval ${myr(row.edge2_myr_p025)}–${myr(row.edge2_myr_p975)} Myr`
                        : undefined}>{myr(row.edge2_myr_median)} Myr</span
                    >{:else}<span class="score-value missing-value" title={reason}>—</span>{/if}</td
                >
              {/if}
              <td data-label="Priority" class="score-cell"
                >{#if row.priority != null}<span
                    class="score-value priority-value"
                    title={row.priority_bounded && row.phylogenetic_status
                      ? unavailable[row.phylogenetic_status] + boundNote
                      : undefined}>{row.priority_bounded ? '≤ ' : ''}{row.priority.toFixed(2)}</span
                  >{:else}<span
                    class="score-value missing-value"
                    title={(row.phylogenetic_status && unavailable[row.phylogenetic_status]) ??
                      'No unsequenced history for this species'}>Unscored</span
                  >{/if}</td
              >
            </tr>
          {/each}
        {:else}
          <tr><td colspan={columns.length} class="empty-row">{emptyMessage}</td></tr>
        {/if}
      </tbody>
    </table>
  </div>
</div>
