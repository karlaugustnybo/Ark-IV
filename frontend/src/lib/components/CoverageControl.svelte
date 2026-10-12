<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api/client';
  import { coverageProfile } from '$lib/map/coverageProfile';
  let passageAvailable = $state(false);
  let loading = $state(true);
  let error = $state('');
  onMount(() => {
    const controller = new AbortController();
    api.coverageProfiles(controller.signal)
      .then((result) => {
        passageAvailable = result.profiles.some(
          (p) => p.id === 'with-passage' && p.available
        );
      })
      .catch((reason) => {
        if (!controller.signal.aborted) error = reason instanceof Error ? reason.message : 'Coverage settings unavailable';
      })
      .finally(() => (loading = false));
    return () => controller.abort();
  });
  function change(event: Event) {
    const url = new URL(window.location.href);
    if ((event.currentTarget as HTMLInputElement).checked)
      url.searchParams.set('coverage_profile', 'with-passage');
    else url.searchParams.delete('coverage_profile');
    // Reload the complete dataset together, clearing old map/request caches.
    window.location.assign(url);
  }
</script>
<div class="coverage-control">
  <label class:unavailable={!passageAvailable}
    ><input
      type="checkbox"
      checked={coverageProfile === 'with-passage'}
      disabled={loading || (!passageAvailable && coverageProfile !== 'with-passage')}
      onchange={change}
    /> Include passage in map scores</label
  >
  {#if error || (!loading && !passageAvailable)}<p>
      {error || 'Passage coverage unavailable.'}
    </p>{/if}
</div>
<style>
  .coverage-control {
    margin: 0 0 1rem;
    text-align: left;
  }
  label {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-weight: 600;
    font-family: var(--font-sans);
    font-size: var(--font-size-sm);
  }
  p {
    margin: 0.3rem 0;
    font-size: 0.8rem;
    color: var(--color-gray-600);
    font-family: var(--font-sans);
    line-height: 1.4;
  }
  label.unavailable {
    color: var(--color-gray-500);
    cursor: not-allowed;
  }
</style>
