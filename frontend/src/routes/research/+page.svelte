<script lang="ts">
  import GrowthIcon from '$lib/components/GrowthIcon.svelte';
  import { withCoverageProfile } from '$lib/map/coverageProfile';
  import { growthStageHelp, relativeDay, researchEntries } from '$lib/research';
</script>

<svelte:head>
  <title>Research · Ark-IV</title>
  <meta
    name="description"
    content="Studies behind Ark-IV's scores: what was tested, on which build, and what it changed."
  />
</svelte:head>

<main class="main-content research-index">
  <header>
    <h1><span class="count">{researchEntries.length}</span>Research</h1>
    <p class="subtitle">Studies behind Ark-IV’s scores, and what each one changed.</p>
  </header>

  <ul class="grid">
    {#each researchEntries as entry (entry.slug)}
      <li>
        <a class="card" href={withCoverageProfile(`/research/${entry.slug}`)}>
          <h2>{entry.title}</h2>
          <p class="description">{entry.description}</p>
          <span class="metadata">
            <span>Study</span>
            <span class="stage" title={growthStageHelp[entry.growthStage]}>
              <GrowthIcon stage={entry.growthStage} size={15} />
            </span>
            <time datetime={entry.tended}>{relativeDay(entry.tended)}</time>
          </span>
        </a>
      </li>
    {/each}
  </ul>
</main>

<style>
  .research-index {
    max-width: 1420px;
    margin: var(--space-l) auto var(--space-2xl);
    padding: 0 var(--space-l);
    color: var(--color-black);
  }
  header {
    margin-bottom: var(--space-xl);
  }
  h1 {
    position: relative;
    margin: 0 0 var(--space-s);
    font-family: var(--font-serif);
    font-size: var(--font-size-3xl);
    font-weight: normal;
    line-height: var(--leading-tighter);
  }
  .count {
    position: absolute;
    top: 0.2rem;
    left: -2rem;
    color: var(--color-primary-green);
    font-size: var(--font-size-md);
  }
  .subtitle {
    margin: var(--space-xs) 0 var(--space-2xs);
    color: var(--color-gray-800);
    font-family: var(--font-serif);
    font-size: calc(var(--font-size-xl) / 1.2);
    font-weight: 100;
    line-height: var(--leading-base);
  }
  .grid {
    margin: var(--space-m) 0;
    padding: 0;
    list-style: none;
    column-count: 3;
    column-gap: 0.5rem;
  }
  .grid li {
    break-inside: avoid;
    margin-bottom: 0.5rem;
  }
  .card {
    display: flex;
    flex-direction: column;
    padding: var(--space-s);
    border: 1px solid var(--color-tinted-cream);
    border-radius: var(--border-radius-base);
    background: var(--color-light-cream);
    box-shadow: var(--box-shadow-sm);
    color: var(--color-gray-800);
    text-decoration: none;
    transition: all 0.3s ease-in-out;
  }
  .card h2 {
    margin: var(--space-2xs) 0;
    font-family: var(--font-body);
    font-size: var(--font-size-base);
    font-weight: 400;
    line-height: var(--leading-snug);
    transition: color 0.3s ease-in-out;
  }
  .card:hover,
  .card:focus-visible {
    box-shadow: var(--box-shadow-lg);
    transform: scale3d(1.02, 1.02, 1.02);
  }
  .card:hover h2,
  .card:focus-visible h2 {
    color: var(--color-primary-green);
  }
  .description,
  .metadata {
    color: var(--color-gray-500);
    font: 400 var(--font-size-xs) / 1.4 var(--font-sans);
  }
  .description {
    margin: 4px 0 0;
  }
  .metadata {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2xs);
    margin-top: 6px;
  }
  .stage {
    display: inline-flex;
  }
  @media (max-width: 1440px) {
    .count {
      position: static;
      display: block;
      font-size: calc(var(--font-size-md) / 1.5);
    }
  }
  @media (max-width: 1100px) {
    .grid {
      column-count: 2;
    }
  }
  @media (max-width: 768px) {
    .research-index {
      margin: var(--space-s) auto var(--space-xl);
      padding: 0 var(--space-m);
    }
    .subtitle {
      font-size: var(--font-size-lg);
    }
  }
  @media (max-width: 700px) {
    .grid {
      column-count: 1;
    }
  }
  @media (max-width: 550px) {
    h1 {
      font-size: var(--font-size-2xl);
    }
    .subtitle {
      font-size: var(--font-size-md);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .card,
    .card h2 {
      transition: none;
    }
    .card:hover,
    .card:focus-visible {
      transform: none;
    }
  }
</style>
