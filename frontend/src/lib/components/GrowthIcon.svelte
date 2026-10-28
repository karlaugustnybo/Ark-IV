<script lang="ts">
  import type { GrowthStage } from '$lib/research';

  export let stage: GrowthStage;
  export let size = 16;
</script>

<!-- Icons copied from the digital garden's growth stages. -->
<svg
  class="growth-icon"
  class:budding={stage === 'budding'}
  width={size}
  height={size}
  viewBox="0 0 25 25"
  fill="currentColor"
  aria-hidden="true"
>
  {#if stage === 'seedling'}
    <path
      d="M12.21 16.47A12.63 12.63 0 00.81 7.31a.3.3 0 00-.31.33 11.67 11.67 0 0012 10h.06a11.67 11.67 0 0012-10 .3.3 0 00-.31-.33 12.63 12.63 0 00-11.4 9.16.3.3 0 01-.64 0z"
    />
  {:else if stage === 'budding'}
    <path
      d="M12.79 18.34a.41.41 0 01-.59 0c-4-4.22-4-12.11 0-16.32a.4.4 0 01.59 0c4.06 4.21 4.06 12.1 0 16.32z"
    />
    <path
      d="M12.21 21.89a12.63 12.63 0 00-11.4-9.16.3.3 0 00-.31.33 11.67 11.67 0 0012 10h.06a11.67 11.67 0 0012-10 .3.3 0 00-.31-.33 12.63 12.63 0 00-11.4 9.16.3.3 0 01-.64 0z"
    />
  {:else}
    <path
      d="M12.76 15.45a.36.36 0 01-.52 0c-3.59-3.74-3.6-10.73 0-14.45a.36.36 0 01.52 0c3.59 3.72 3.6 10.71 0 14.45z"
    />
    <path
      d="M12.25 18.61a11.21 11.21 0 00-10.12-8.13.26.26 0 00-.27.29 10.36 10.36 0 0010.61 8.91h.06a10.36 10.36 0 0010.61-8.91.26.26 0 00-.27-.29 11.21 11.21 0 00-10.12 8.13.26.26 0 01-.5 0z"
    />
    <path
      d="M24.17 24.1a.33.33 0 00.32-.42 6.21 6.21 0 00-6-4.22 6.3 6.3 0 00-5.7 3.44.32.32 0 01-.58 0 6.3 6.3 0 00-5.7-3.44 6.21 6.21 0 00-6 4.22.33.33 0 00.32.42H12.5z"
    />
  {/if}
</svg>

<style>
  .growth-icon {
    flex-shrink: 0;
  }
  .budding {
    color: var(--color-primary-green);
  }
</style>
