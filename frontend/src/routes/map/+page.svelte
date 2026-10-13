<script lang="ts">
  import { onDestroy, tick } from 'svelte';
  import { PanelLeftClose, SlidersHorizontal, X } from 'lucide-svelte';
  import MapOptions from '$lib/components/MapOptions.svelte';
  import Sliders from '$lib/components/Sliders.svelte';
  import Map from '$lib/components/Map.svelte';
  import CellDetails from '$lib/components/CellDetails.svelte';
  import { boundaryCollections, boundaryFilters, toggleBoundary } from '$lib/stores/boundaries';
  import { defaultWeights, resetWeights, weights, type WeightKey } from '$lib/stores/weights';

  let sidebarOpen = $state(false);
  let sidebarSpaceOpen = $state(false);
  let sidebarCloseTimer: ReturnType<typeof setTimeout> | undefined;
  let sidebar: HTMLElement;
  let openButton = $state<HTMLButtonElement>();
  let minimizeButton: HTMLButtonElement;
  const selectedPlaces = $derived(
    Object.entries($boundaryFilters).flatMap(([framework, codes]) =>
      codes.map((code) => ({
        framework,
        code,
        name:
          $boundaryCollections[framework]?.features.find(
            (feature) => feature.properties.code === code
          )?.properties.name ?? code
      }))
    )
  );
  const customWeightCount = $derived(
    Object.entries(defaultWeights).filter(([key, value]) => $weights[key as WeightKey] !== value)
      .length
  );

  async function openSidebar() {
    clearTimeout(sidebarCloseTimer);
    if (document.fullscreenElement) await document.exitFullscreen();
    sidebarSpaceOpen = true;
    sidebarOpen = true;
    await tick();
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    if (sidebarOpen) minimizeButton?.focus({ preventScroll: true });
  }

  async function minimizeSidebar() {
    clearTimeout(sidebarCloseTimer);
    sidebarOpen = false;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) sidebarSpaceOpen = false;
    else
      sidebarCloseTimer = setTimeout(() => {
        if (!sidebarOpen) sidebarSpaceOpen = false;
      }, 160);
    await tick();
    openButton?.focus({ preventScroll: true });
  }

  function keydown(event: KeyboardEvent) {
    if (event.key === 'Escape' && sidebarOpen && sidebar?.contains(document.activeElement)) {
      void minimizeSidebar();
    }
  }

  function sidebarTransitionEnd(event: TransitionEvent) {
    if (event.target !== event.currentTarget || event.propertyName !== 'opacity') return;
    if (!sidebarOpen) sidebarSpaceOpen = false;
    else if (document.activeElement === document.body) {
      minimizeButton?.focus({ preventScroll: true });
    }
  }

  onDestroy(() => clearTimeout(sidebarCloseTimer));
</script>

<svelte:head><title>Explore the map · Ark-IV</title></svelte:head>
<svelte:window onkeydown={keydown} />

<main class="main-content map-page">
  <header class="map-page-heading">
    <h1>Explore The Map</h1>
    <MapOptions />
  </header>

  <div class="map-workspace">
    <div class="map-summary" aria-label="Active map filters">
      <div class="place-summary">
        <span
          class={['scope-dot', { 'has-selection': selectedPlaces.length > 0 }]}
          aria-hidden="true"
        ></span>
        {#if selectedPlaces.length}
          {#each selectedPlaces.slice(0, 3) as place (`${place.framework}:${place.code}`)}
            <button
              class="place-chip"
              aria-label={`Remove ${place.name} filter`}
              onclick={() => toggleBoundary(place.framework, place.code)}
            >
              {place.name}<X size={12} aria-hidden="true" />
            </button>
          {/each}
          {#if selectedPlaces.length > 3}<span>+{selectedPlaces.length - 3} places</span>{/if}
        {:else}
          <span>Worldwide</span>
        {/if}
      </div>
      <div class="weight-summary">
        <span aria-live="polite"
          >{customWeightCount
            ? `${customWeightCount} custom weight${customWeightCount === 1 ? '' : 's'}`
            : 'Default weights'}</span
        >
        {#if customWeightCount}<button class="text-action" onclick={resetWeights}
            >Reset weights</button
          >{/if}
      </div>
    </div>

    <div class={['map-layout', { 'is-open': sidebarOpen, 'has-sidebar': sidebarSpaceOpen }]}>
      <div class="sidebar-slot" inert={!sidebarOpen} aria-hidden={!sidebarOpen}>
        <aside
          id="map-filters"
          class="map-sidebar"
          aria-label="Map filters and weights"
          bind:this={sidebar}
          ontransitionend={sidebarTransitionEnd}
        >
          <div class="sidebar-heading">
            <strong>Filters & weights</strong>
            <button
              class="minimize-button"
              onclick={minimizeSidebar}
              aria-label="Minimize filters"
              bind:this={minimizeButton}
            >
              <PanelLeftClose size={17} strokeWidth={1.7} aria-hidden="true" />
              <span>Minimize</span>
            </button>
          </div>
          <div class="sidebar-content">
            <Sliders showMapOptions={false} sidebar />
          </div>
        </aside>
      </div>
      <div class="map-surface">
        {#snippet toolbar()}
          {#if !sidebarOpen}
            <button
              class="dropdown-toggle filters-toggle"
              onclick={openSidebar}
              aria-expanded={sidebarOpen}
              aria-controls="map-filters"
              bind:this={openButton}
            >
              <SlidersHorizontal size={16} strokeWidth={1.7} aria-hidden="true" />
              Filters & weights
            </button>
          {/if}
        {/snippet}
        <Map {toolbar} />
      </div>
    </div>
  </div>
  <CellDetails />
</main>

<style>
  .map-page {
    --map-height: max(28rem, calc(100svh - 14rem));
  }
  .map-page-heading {
    padding: 1rem 1rem 1.25rem;
  }
  .map-page-heading h1 {
    margin-bottom: 2.25rem;
  }
  .map-workspace {
    width: calc(100% - 3rem);
    margin: 0 auto 2rem;
  }
  .map-summary {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.5rem 1rem;
    padding: 0 0 0.75rem;
    color: var(--color-gray-600);
    font: 0.75rem/1.4 var(--font-sans);
    text-align: left;
  }
  .place-summary,
  .weight-summary {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.45rem;
  }
  .scope-dot {
    width: 0.36rem;
    height: 0.36rem;
    border-radius: 50%;
    background: var(--color-gray-400);
  }
  .scope-dot.has-selection {
    background: var(--color-primary-green);
    box-shadow: 0 0 0 3px var(--color-primary-green-10);
  }
  .place-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.3rem 0.45rem;
    border: 1px solid var(--color-tinted-cream);
    border-radius: var(--border-radius-base);
    background: var(--color-light-cream);
    color: var(--color-gray-700);
    font-size: 0.75rem;
  }
  .place-chip:hover {
    border-color: var(--color-primary-green);
  }
  .text-action {
    padding: 0.2rem 0.4rem;
    color: var(--color-accent-brown);
    font-size: 0.75rem;
  }
  .map-layout {
    --sidebar-width: 350px;
    display: grid;
    grid-template-columns: 0px minmax(0, 1fr);
    gap: 0;
    align-items: start;
  }
  .map-layout.has-sidebar {
    grid-template-columns: var(--sidebar-width) minmax(0, 1fr);
    gap: 1rem;
  }
  .sidebar-slot {
    min-width: 0;
    overflow: hidden;
    visibility: hidden;
    transition: visibility 0s 160ms;
  }
  .is-open .sidebar-slot {
    visibility: visible;
    transition-delay: 0s;
  }
  .map-sidebar {
    width: var(--sidebar-width);
    min-width: 0;
    opacity: 0;
    transform: translateX(-6px);
    transition:
      opacity 160ms ease-out,
      transform 160ms ease-out;
    height: var(--map-height);
    display: flex;
    flex-direction: column;
    background: var(--color-light-cream);
    border: 1px solid var(--color-tinted-cream);
    border-radius: var(--map-radius);
    text-align: left;
    overflow: hidden;
  }
  .is-open .map-sidebar {
    opacity: 1;
    transform: none;
  }
  .sidebar-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 1rem;
    border-bottom: 1px solid var(--color-tinted-cream);
    font: 600 0.875rem var(--font-sans);
  }
  .minimize-button {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.35rem 0.4rem;
    color: var(--color-gray-600);
    font-size: 0.75rem;
  }
  .minimize-button:hover {
    color: var(--color-accent-brown);
    background: var(--color-primary-green-10);
  }
  .sidebar-content {
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 1rem;
  }
  .map-surface {
    min-width: 0;
  }
  .map-surface :global(.fs-wrap) {
    width: 100%;
    margin: 0;
  }
  .map-surface :global(.fs-wrap:not(:fullscreen) #map) {
    height: var(--map-height);
  }
  .filters-toggle {
    border-radius: var(--map-radius);
    padding: 0.75rem 1rem;
    height: 2.85rem;
    line-height: 1;
    color: var(--color-accent-brown);
    background: rgba(252, 251, 247, 0.96);
    border-color: rgba(93, 78, 60, 0.2);
    box-shadow: 0 9px 30px rgba(40, 32, 20, 0.16);
    transition:
      transform 160ms ease,
      color 160ms ease,
      border-color 160ms ease;
  }
  .filters-toggle:hover {
    background: rgba(252, 251, 247, 0.96);
    box-shadow: 0 9px 30px rgba(40, 32, 20, 0.16);
    color: var(--color-primary-green);
    border-color: rgba(93, 78, 60, 0.35);
    transform: translateY(-1px);
  }
  button:focus-visible {
    outline: 2px solid var(--color-primary-green);
    outline-offset: 2px;
  }
  @media (max-width: 900px) {
    .map-layout {
      --sidebar-width: 310px;
    }
  }
  @media (max-width: 700px) {
    .map-page {
      --map-height: max(24rem, calc(100svh - 13rem));
    }
    .map-page-heading {
      padding: 0.75rem 1rem 1rem;
    }
    .map-page-heading h1 {
      font-size: clamp(2rem, 8vw, 3rem);
      margin-bottom: 2rem;
    }
    .map-workspace {
      width: calc(100% - 1.5rem);
    }
    .map-layout {
      --sidebar-height: min(24rem, 50svh);
      grid-template-columns: minmax(0, 1fr);
      grid-template-rows: 0px minmax(0, auto);
    }
    .map-layout.has-sidebar {
      grid-template-columns: minmax(0, 1fr);
      grid-template-rows: var(--sidebar-height) minmax(0, auto);
    }
    .sidebar-slot {
      min-height: 0;
      height: 100%;
    }
    .map-sidebar {
      width: 100%;
      height: var(--sidebar-height);
      transform: translateY(-6px);
    }
    .is-open .map-sidebar {
      transform: none;
    }
    .filters-toggle {
      border-radius: var(--map-radius);
      padding-inline: 0.7rem;
      font-size: 0.8rem;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .map-layout,
    .sidebar-slot,
    .map-sidebar,
    .filters-toggle {
      transition: none;
    }
  }
</style>
