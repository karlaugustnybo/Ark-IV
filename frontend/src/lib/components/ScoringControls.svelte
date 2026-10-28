<script lang="ts">
  import { Scale } from 'lucide-svelte';
  import WeightControls from './WeightControls.svelte';
  import { dnaMeasure, includeWithheld, type DnaMeasure } from '$lib/stores/weights';

  // The map always colours cells from bucket counts, so it keeps both weight
  // menus and explains that branch scoring only reaches the species table.
  let { map = false } = $props<{ map?: boolean }>();

  const measures: Array<[DnaMeasure, string]> = [
    ['branch', 'Unsequenced history'],
    ['buckets', 'DNA buckets']
  ];
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

  const buckets = $derived($dnaMeasure === 'buckets');
  const withheldHelp =
    'Withheld species are placed on the dated tree, but their gap cannot be stated exactly: ' +
    'their genome records could not be safely matched, or a sequenced relative is missing ' +
    'from the tree. Included, they are ranked at an upper bound that assumes they have no DNA.';
</script>

<fieldset class={['scoring-controls', { 'is-map': map }]}>
  <legend><Scale size={14} strokeWidth={1.7} />Priority score</legend>
  <div class="scoring-pills">
    <div class="pill-group" role="group" aria-label="DNA gap measure">
      {#each measures as [value, label] (value)}<button
          type="button"
          class:active={$dnaMeasure === value}
          aria-pressed={$dnaMeasure === value}
          onclick={() => dnaMeasure.set(value)}>{label}</button
        >{/each}
    </div>
    {#if !buckets && !map}
      <div class="pill-group" role="group" aria-label="Withheld species">
        <button
          type="button"
          class:active={$includeWithheld}
          aria-pressed={$includeWithheld}
          title={withheldHelp}
          onclick={() => includeWithheld.update((value) => !value)}>Include withheld species</button
        >
      </div>
    {/if}
  </div>
  <p class="formula">
    {#if buckets}IUCN weight × DNA bucket weight{:else}Expected history lost: EDGE2 extinction
      probability × unsequenced Myr.
      {#if $includeWithheld}Withheld species count at their upper bound (≤); species without a tree
        placement stay unscored.{:else}Species without a safe tree placement stay unscored.{/if}{/if}
  </p>
  {#if map && !buckets}
    <p class="formula map-note">
      Map colours still use the bucket weights below; unsequenced history ranks the species table.
    </p>
  {/if}
  {#if buckets || map}
    <div class="weight-menus">
      <WeightControls title="IUCN Category Weights" controls={iucn} />
      <WeightControls title={map ? 'DNA & Coverage Weights' : 'DNA Weights'} controls={dna} />
    </div>
  {/if}
</fieldset>

<style>
  .scoring-controls {
    min-width: 0;
    margin: 0;
    padding: 0;
    border: 0;
    font-family: var(--font-sans);
    text-align: left;
  }
  legend {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0;
    color: var(--color-gray-700);
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.025em;
  }
  legend :global(svg) {
    color: var(--color-tertiary-green);
  }
  .scoring-pills {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem 1rem;
    margin-top: 0.5rem;
  }
  .pill-group {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }
  .pill-group button {
    min-height: 1.8rem;
    padding: 0.38rem 0.68rem;
    border: 1px solid transparent;
    border-radius: 999px;
    background: var(--color-gray-200);
    color: var(--color-gray-700);
    font-size: 0.67rem;
    font-weight: 650;
    line-height: 1;
    opacity: 0.68;
    transition:
      opacity 120ms ease,
      border-color 120ms ease,
      transform 120ms ease;
  }
  .pill-group button:hover {
    opacity: 1;
    transform: translateY(-1px);
  }
  .pill-group button.active {
    border-color: currentColor;
    background: #e4eee7;
    color: #315d47;
    opacity: 1;
  }
  .pill-group button:focus-visible {
    outline: 2px solid var(--color-primary-green);
    outline-offset: 2px;
  }
  .formula {
    margin: 0.45rem 0 0;
    color: var(--color-gray-500);
    font-family: var(--font-body);
    font-size: 0.7rem;
  }
  .weight-menus {
    display: grid;
    gap: 0.5rem;
    margin-top: 0.6rem;
  }
  .weight-menus :global(.sliders-dropdown) {
    margin: 0;
  }
  @media (prefers-reduced-motion: reduce) {
    .pill-group button {
      transition: none;
    }
  }
</style>
