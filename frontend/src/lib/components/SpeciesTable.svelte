<script lang="ts">
  import { ignoresWindowShortcut } from '$lib/map/keyboardShortcuts';
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import { api, type SpeciesPage } from '$lib/api/client';
  import SpeciesDataTable from '$lib/components/SpeciesDataTable.svelte';
  import { dnaStatus, type SpeciesSortKey, type SpeciesTableRow } from '$lib/species-table';
  import { scoreMode, weights } from '$lib/stores/weights';
  import { boundaryFilters } from '$lib/stores/boundaries';
  import {
    tableDna,
    tableOrder,
    tablePage,
    tableRedlist,
    tableSearch,
    tableSort,
    tableSystems
  } from '$lib/stores/species';

  // Rows arrive in blocks of the API's largest page, since 100 rows cost about
  // the same as 10, and are sliced into pages here. Paging inside a loaded block
  // is instant, and blocks ahead in the paging direction are prefetched so a
  // held arrow key rarely waits. Every page size divides the block.
  const BLOCK_ROWS = 100;
  const PREFETCH_AHEAD = 3;
  const KEEP_BLOCKS = 6;

  type Block = SpeciesPage & { rows: NonNullable<SpeciesPage['rows']> };
  type View = { rows: Block['rows']; total: number; suggested: boolean };

  let blocks = new Map<number, Block>();
  const pending = new Set<number>();
  let generation = 0;
  /** Row count of the current query; null until its first block arrives. */
  let knownTotal: number | null = null;
  let controller = new AbortController();
  let view: View | null = null;
  let error = '';
  let debounce: ReturnType<typeof setTimeout>;
  let perPage = 10;
  let direction = 1;

  $: offset = ($tablePage - 1) * perPage;
  $: block = blocks.get(Math.floor(offset / BLOCK_ROWS));
  // The last complete page stays visible, dimmed, until its replacement arrives.
  $: if (block)
    view = {
      rows: block.rows.slice(offset % BLOCK_ROWS, (offset % BLOCK_ROWS) + perPage),
      total: block.total,
      suggested: block.suggested
    };
  $: loading = !block && !error;
  $: totalPages = view ? Math.max(1, Math.ceil(view.total / perPage)) : 1;

  // In the upper-bound mode, withheld species show their bound in the gap column.
  $: upper = $scoreMode === 'expected-loss-upper';
  $: tableRows = (view?.rows ?? []).map((row): SpeciesTableRow => ({
    ...row,
    gap_myr: upper ? row.gap_upper_myr : row.gap_myr,
    bounded:
      row.phylogenetic_status === 'baseline_incomplete' ||
      (upper && row.phylogenetic_status != null && row.gap_upper_myr != null),
    priority_bounded: upper && row.phylogenetic_status != null,
    dnaLabel: row.dna_level,
    dnaStatus: dnaStatus(row.dna_level)
  }));

  function params(blockIndex: number) {
    const current = get(weights);
    const filters = get(boundaryFilters);
    return new URLSearchParams({
      search: get(tableSearch),
      sort:
        get(scoreMode) === 'expected-loss-upper' && get(tableSort) === 'unsequenced_myr'
          ? 'unsequenced_myr_upper'
          : get(tableSort),
      order: get(tableOrder),
      page: String(blockIndex + 1),
      per_page: String(BLOCK_ROWS),
      redlist: get(tableRedlist).join(','),
      dna: get(tableDna).join(','),
      systems: get(tableSystems).join(','),
      admin0: (filters.admin0 ?? []).join(','),
      admin1: (filters.admin1 ?? []).join(','),
      municipality: (filters.municipality ?? []).join(','),
      eez: (filters.eez ?? []).join(','),
      conservation_framework: (filters.conservation_framework ?? []).join(','),
      score: get(scoreMode),
      ...Object.fromEntries(Object.entries(current).map(([key, value]) => [key, String(value)]))
    });
  }

  async function fetchBlock(index: number) {
    if (blocks.has(index) || pending.has(index)) return;
    const currentGeneration = generation;
    pending.add(index);
    try {
      const result = await api.species(params(index), controller.signal);
      if (currentGeneration !== generation) return;
      knownTotal = result.total;
      const pages = Math.max(1, Math.ceil(result.total / perPage));
      // The server clamps out-of-range pages, e.g. after filters shrink the result.
      if (get(tablePage) > pages) tablePage.set(pages);
      if (result.page === index + 1)
        blocks = new Map(blocks).set(index, { ...result, rows: result.rows ?? [] });
      error = '';
      ensure();
    } catch (reason) {
      if (
        currentGeneration === generation &&
        !(reason instanceof DOMException && reason.name === 'AbortError')
      ) {
        error = reason instanceof Error ? reason.message : 'Unable to load species';
      }
    } finally {
      if (currentGeneration === generation) pending.delete(index);
    }
  }

  // Loads the current page's block, prefetches neighbours and drops far blocks.
  function ensure() {
    const current = Math.floor(((get(tablePage) - 1) * perPage) / BLOCK_ROWS);
    const last = knownTotal == null ? current : Math.max(0, Math.ceil(knownTotal / BLOCK_ROWS) - 1);
    void fetchBlock(current);
    for (let step = 1; step <= PREFETCH_AHEAD; step += 1) {
      const ahead = current + direction * step;
      if (ahead >= 0 && ahead <= last) void fetchBlock(ahead);
    }
    if (current - direction >= 0 && current - direction <= last)
      void fetchBlock(current - direction);
    const far = [...blocks.keys()].filter((index) => Math.abs(index - current) > KEEP_BLOCKS);
    if (far.length) {
      blocks = new Map(blocks);
      far.forEach((index) => blocks.delete(index));
    }
  }

  // Filters, sorting and weights change the rows, so every cached block is stale.
  function schedule(resetPage = false) {
    if (resetPage) tablePage.set(1);
    clearTimeout(debounce);
    controller.abort();
    controller = new AbortController();
    generation += 1;
    blocks = new Map();
    pending.clear();
    knownTotal = null;
    error = '';
    debounce = setTimeout(ensure, 250);
  }

  function sort(key: SpeciesSortKey) {
    if (get(tableSort) === key) tableOrder.update((value) => (value === 'asc' ? 'desc' : 'asc'));
    else {
      tableSort.set(key);
      tableOrder.set(
        key === 'species_name' || key === 'family' || key === 'redlist_category' ? 'asc' : 'desc'
      );
    }
    schedule(true);
  }

  function page(delta: number) {
    if (!view) return;
    const current = get(tablePage);
    const nextPage = Math.min(totalPages, Math.max(1, current + delta));
    if (nextPage === current) return;
    direction = Math.sign(delta);
    tablePage.set(nextPage);
    ensure();
  }

  // Cached blocks hold rows, not pages, so a new page size needs no refetch.
  function changePageSize() {
    tablePage.set(1);
    ensure();
  }

  // Arrows have no native meaning on the table's own buttons, so paging keeps
  // working after a click leaves focus on Next/Previous or a sort header.
  function ownButton(target: EventTarget | null): boolean {
    return (
      target instanceof Element &&
      Boolean(target.closest('.pagination-buttons button, .species-data-table thead button'))
    );
  }

  function keydown(event: KeyboardEvent) {
    if (!view) return;
    const arrow =
      (event.key === 'ArrowLeft' || event.key === 'ArrowRight') &&
      !(event.altKey || event.ctrlKey || event.metaKey);
    if (
      !(arrow && ownButton(event.target) && !event.defaultPrevented) &&
      ignoresWindowShortcut(event)
    )
      return;
    if (event.key === 'ArrowLeft' && $tablePage > 1) {
      event.preventDefault();
      page(-1);
    } else if (event.key === 'ArrowRight' && $tablePage < totalPages) {
      event.preventDefault();
      page(1);
    } else {
      const shortcuts: Partial<Record<string, SpeciesSortKey>> = {
        '1': 'species_name',
        '2': 'redlist_category',
        '3': 'dna_level',
        '4': 'unsequenced_myr',
        '5': 'edge2',
        '6': 'priority'
      };
      const key = shortcuts[event.key];
      if (key) sort(key);
    }
  }

  onMount(() => {
    let ready = false;
    const subscriptions = [
      weights,
      scoreMode,
      tableSearch,
      tableRedlist,
      tableDna,
      tableSystems,
      boundaryFilters
    ].map((store) =>
      store.subscribe(() => {
        if (ready) schedule(true);
      })
    );
    ready = true;
    ensure();
    return () => {
      ready = false;
      subscriptions.forEach((unsubscribe) => unsubscribe());
      clearTimeout(debounce);
      generation += 1;
      controller.abort();
    };
  });
</script>

<svelte:window on:keydown={keydown} />

<div class="species-table-toolbar table-results-toolbar">
  <span>{view ? `${view.total.toLocaleString()} matching species` : 'Species results'}</span>
  <label class="page-size"
    >Rows <select bind:value={perPage} on:change={changePageSize}
      ><option value={10}>10</option><option value={25}>25</option><option value={50}>50</option
      ></select
    ></label
  >
</div>
{#if error}<p class="error-message">{error}</p>{/if}
{#if view?.suggested && $tableSearch.trim()}
  <p class="search-notice" role="status">
    No exact matches for “{$tableSearch.trim()}”. Showing similar names.
  </p>
{/if}
<SpeciesDataTable
  phylogeny
  rows={tableRows}
  sort={$tableSort}
  order={$tableOrder}
  {loading}
  onSort={sort}
  emptyMessage={$tableSearch.trim()
    ? `No species or families match “${$tableSearch.trim()}”. Try a shorter name or check the spelling.`
    : 'No species match these filters.'}
/>

{#if view}
  <div class="pagination">
    <span class="page-info"
      ><strong>{view.total.toLocaleString()}</strong> species · Page {$tablePage} of {totalPages}</span
    >
    <div class="pagination-buttons">
      <button class="btn-secondary" disabled={$tablePage <= 1} on:click={() => page(-1)}
        >Previous</button
      >
      <button class="btn-secondary" disabled={$tablePage >= totalPages} on:click={() => page(1)}
        >Next</button
      >
    </div>
  </div>
{/if}
