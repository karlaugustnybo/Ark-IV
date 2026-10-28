<script lang="ts">
  import { withCoverageProfile } from '$lib/map/coverageProfile';
  import { growthStageHelp, relativeDay, type ResearchEntry } from '$lib/research';
  import GrowthIcon from './GrowthIcon.svelte';

  /** Page frame for one study, after the digital garden's essay layout. */
  export let entry: ResearchEntry;

  // Like the garden, a study tended within three days of planting shows one date.
  $: tendedLater = Date.parse(entry.tended) - Date.parse(entry.planted) > 3 * 86_400_000;
</script>

<svelte:head>
  <title>{entry.title} · Ark-IV</title>
  <meta name="description" content={entry.description} />
</svelte:head>

<main class="main-content research-article">
  <header class="article-header">
    <div class="above-title">
      <a class="back-link" href={withCoverageProfile('/research')}>
        <svg viewBox="0 0 20 20" width="16" height="16" fill="currentColor" aria-hidden="true"
          ><path
            fill-rule="evenodd"
            d="M17 10a.75.75 0 0 1-.75.75H5.612l4.158 3.96a.75.75 0 1 1-1.04 1.08l-5.5-5.25a.75.75 0 0 1 0-1.08l5.5-5.25a.75.75 0 1 1 1.04 1.08L5.612 9.25H16.25A.75.75 0 0 1 17 10Z"
          /></svg
        ><span>Research</span>
      </a>
      <GrowthIcon stage={entry.growthStage} />
      <span class="growth-stage" title={growthStageHelp[entry.growthStage]}>
        {entry.growthStage}
      </span>
    </div>
    <div class="title-container">
      <h1>{entry.title}</h1>
      <p>{entry.description}</p>
    </div>
    <div class="metadata">
      <ul class="topics" aria-label="Topics">
        {#each entry.topics as topic (topic)}<li>{topic}</li>{/each}
      </ul>
      <div class="dates">
        <span>Planted <time datetime={entry.planted}>{relativeDay(entry.planted)}</time></span>
        {#if tendedLater}
          <span>Last tended <time datetime={entry.tended}>{relativeDay(entry.tended)}</time></span>
        {/if}
      </div>
    </div>
  </header>

  <article class="article-body">
    <slot />
  </article>
</main>

<style>
  .research-article {
    --research-rule: color-mix(in srgb, var(--color-accent-brown) 16%, transparent);
    color: var(--color-black);
    font-family: var(--font-body);
  }
  .article-header,
  .article-body {
    max-width: 800px;
    margin-inline: auto;
    padding-inline: var(--space-xs);
  }
  .article-header {
    margin-top: var(--space-l);
  }
  .above-title {
    display: flex;
    align-items: center;
    gap: var(--space-xs);
    min-height: 24px;
    font: bold var(--font-size-xs) / 1 var(--font-sans);
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }
  .back-link {
    display: inline-flex;
    align-items: center;
    color: var(--color-primary-green);
    text-decoration: none;
  }
  .back-link svg {
    width: 0;
    color: var(--color-gray-300);
    transition: all 0.8s cubic-bezier(0.2, 1, 0.8, 1);
  }
  .back-link span {
    display: inline-block;
    transition: transform 0.3s cubic-bezier(0.2, 1, 0.8, 1);
  }
  .back-link:hover svg,
  .back-link:focus-visible svg {
    width: 16px;
    margin-right: 2px;
    color: var(--color-primary-green);
  }
  .back-link:hover span,
  .back-link:focus-visible span {
    transform: translate3d(3px, 0, 0);
  }
  .growth-stage {
    color: var(--color-gray-800);
    cursor: help;
  }
  .title-container {
    padding: var(--space-s) 0 var(--space-l);
    border-bottom: 1px solid var(--color-tinted-cream);
  }
  .title-container h1 {
    margin: 0;
    font-family: var(--font-serif);
    font-size: var(--font-size-2xl);
    font-weight: 600;
    line-height: var(--leading-tighter);
  }
  .title-container p {
    margin: var(--space-s) 0 0;
    color: var(--color-gray-800);
    font-size: var(--font-size-md);
    font-weight: 300;
    line-height: var(--leading-base);
  }
  .metadata {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: var(--space-s);
    font: var(--font-size-xs) / 1.4 var(--font-sans);
  }
  .topics {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-3xs) var(--space-xs);
    max-width: 70%;
    margin: var(--space-xs) 0 0;
    padding: 0;
    list-style: none;
  }
  .dates {
    display: flex;
    flex-direction: column;
    margin-top: var(--space-xs);
    color: var(--color-gray-600);
    text-align: right;
    letter-spacing: 0.01em;
  }
  .article-body {
    margin-top: var(--space-xs);
    padding-block: var(--space-l) var(--space-2xl);
    line-height: var(--leading-loose);
  }
  .article-body :global(h2) {
    margin: var(--space-l) 0 var(--space-xs);
    font-family: var(--font-serif);
    font-size: var(--font-size-lg);
    font-weight: 400;
    line-height: var(--leading-snug);
  }
  .article-body :global(h2:first-child) {
    margin-top: 0;
  }
  .article-body :global(p) {
    margin: 0 0 var(--space-s);
  }
  @media (max-width: 768px) {
    .metadata {
      flex-direction: column;
      gap: var(--space-xs);
    }
    .topics {
      max-width: 100%;
    }
    .dates {
      margin-top: 0;
      text-align: left;
    }
  }
  @media (max-width: 425px) {
    .title-container h1 {
      font-size: var(--font-size-xl);
    }
    .title-container p {
      font-size: var(--font-size-base);
      font-weight: 400;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .back-link svg,
    .back-link span {
      transition: none;
    }
  }
</style>
