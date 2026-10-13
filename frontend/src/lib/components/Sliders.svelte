<script lang="ts">
  import MapOptions from './MapOptions.svelte';
  import WeightControls from './WeightControls.svelte';
  import BoundaryFilter from './BoundaryFilter.svelte';

  let {
    showSystems = true,
    showMapOptions = true,
    sidebar = false
  } = $props<{
    showSystems?: boolean;
    showMapOptions?: boolean;
    sidebar?: boolean;
  }>();

  const iucn = [
    { key: 'cr' as const, label: 'Critically Endangered' },
    { key: 'en' as const, label: 'Endangered' },
    { key: 'vu' as const, label: 'Vulnerable' },
    { key: 'nt' as const, label: 'Near Threatened' },
    { key: 'dd' as const, label: 'Data Deficient' },
    { key: 'lc' as const, label: 'Least Concern', step: 0.1 }
  ];
  const dna = [
    { key: 'gdd' as const, label: 'GoaT Data Deficient' },
    { key: 'sp' as const, label: 'Missing Species DNA' },
    { key: 'gen' as const, label: 'Missing Genus DNA' },
    { key: 'fam' as const, label: 'Missing Family DNA' },
    { key: 'samp' as const, label: 'Already Sampled', step: 0.1 }
  ];
</script>

<div class={['sliders-wrapper', { 'is-sidebar': sidebar }]}>
  {#if showSystems}
    {#if showMapOptions}<MapOptions />{/if}
    <BoundaryFilter condensed={sidebar} showSummary={!sidebar} />
  {/if}
  <WeightControls title="IUCN Category Weights" controls={iucn} />
  <WeightControls title={showSystems ? 'DNA & Coverage Weights' : 'DNA Weights'} controls={dna} />
</div>

<style>
  .is-sidebar {
    width: 100%;
    max-width: none;
    margin: 0;
    text-align: left;
  }
  .is-sidebar :global(.sliders-dropdown) {
    margin-top: 0.75rem;
  }
  .is-sidebar :global(.boundary-filter) {
    margin-top: 0;
  }
  .is-sidebar :global(.dropdown-toggle) {
    width: 100%;
    min-width: 0;
    justify-content: space-between;
    padding-inline: 0.85rem;
    text-align: left;
  }
  .is-sidebar :global(.boundary-toggle > span:nth-child(2)) {
    flex: 1;
    text-align: left;
  }
  .is-sidebar :global(.dropdown-toggle),
  .is-sidebar :global(.dropdown-panel),
  .is-sidebar :global(.weight-num),
  .is-sidebar :global(.boundary-search),
  .is-sidebar :global(.boundary-search input) {
    border-radius: var(--map-radius);
  }
  .is-sidebar :global(.dropdown-grid) {
    grid-template-columns: 1fr;
    padding: 1rem;
    gap: 1rem;
  }
  .is-sidebar :global(.framework-rail) {
    grid-template-columns: repeat(2, 1fr);
  }
  .is-sidebar :global(.framework-tab:last-child:nth-child(odd)) {
    grid-column: 1 / -1;
  }
  .is-sidebar :global(.boundary-heading) {
    padding-inline: 0.85rem;
  }
  .is-sidebar :global(.boundary-footnote) {
    flex-direction: column;
    gap: 0.2rem;
  }
</style>
