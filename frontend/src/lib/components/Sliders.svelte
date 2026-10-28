<script lang="ts">
  import MapOptions from './MapOptions.svelte';
  import ScoringControls from './ScoringControls.svelte';
  import BoundaryFilter from './BoundaryFilter.svelte';

  let { showMapOptions = true, sidebar = false } = $props<{
    showMapOptions?: boolean;
    sidebar?: boolean;
  }>();
</script>

<div class={['sliders-wrapper', { 'is-sidebar': sidebar }]}>
  {#if showMapOptions}<MapOptions />{/if}
  <BoundaryFilter condensed={sidebar} showSummary={!sidebar} />
  <div class="scoring-slot"><ScoringControls map /></div>
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
  .scoring-slot {
    margin-top: 1rem;
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
