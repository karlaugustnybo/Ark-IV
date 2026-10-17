<script lang="ts">
  import { withCoverageProfile } from '$lib/map/coverageProfile';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import '../app.css';
  import { preloadMapBundle, preloadMapBundleInIdleTime } from '$lib/map/mapBundle';
  import { navigateToPreloadedMap } from '$lib/map/mapNavigation';
  let { children } = $props();
  const warmMap = () => {
    void preloadMapBundle().catch(() => {});
  };

  // Warm the global map after the current page becomes idle. Direct map intent
  // (hover, focus, or pointer-down) starts it immediately, while slow/save-data
  // connections are left alone until the user actually opens the map.
  onMount(preloadMapBundleInIdleTime);
</script>

<svelte:head>
  <title>Ark-IV</title>
  <meta name="description" content="Worldwide conservation DNA sequencing priorities" />
  <link rel="icon" href="/favicon.svg" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="anonymous" />
  <link
    href="https://fonts.googleapis.com/css2?family=Baskervville:ital@0;1&family=JetBrains+Mono:ital,wght@0,100..800;1,100..800&family=Noto+Serif+Display:ital,wght@0,100..900;1,100..900&family=Noto+Sans:ital,wght@0,100..900;1,100..900&display=swap"
    rel="stylesheet"
  />
</svelte:head>

<nav class="site-navbar">
  <div class="nav-left"><a class="nav-brand" href={withCoverageProfile('/')}>Ark-<i>IV</i></a></div>
  <div class="nav-right" role="navigation" aria-label="Main">
    <a href={withCoverageProfile('/table')} aria-current={page.url.pathname === '/table' ? 'page' : undefined}
      ><span>Table</span></a
    ><a
      href={withCoverageProfile('/map')}
      aria-current={page.url.pathname === '/map' ? 'page' : undefined}
      onclick={navigateToPreloadedMap}
      onpointerenter={warmMap}
      onfocus={warmMap}
      onpointerdown={warmMap}><span>Map</span></a
    ><a href={withCoverageProfile('/tutorial')} aria-current={page.url.pathname === '/tutorial' ? 'page' : undefined}
      ><span>Tutorial</span></a
    ><a href={withCoverageProfile('/methodology')} aria-current={page.url.pathname === '/methodology' ? 'page' : undefined}
      ><span>Methodology</span></a
    >
  </div>
</nav>
{@render children()}
<footer class="site-footer">
  <p>
    Ark-<i>IV</i> · Conservation prioritisation powered by IUCN and GoaT data ·
    <a href="https://github.com/karlaugustnybo/DIKUs-Ark" target="_blank" rel="noreferrer"
      >Source (CC0-1.0)</a
    >
  </p>
</footer>
