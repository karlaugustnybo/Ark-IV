<script lang="ts">
  import { ChevronUp, ChevronDown } from 'lucide-svelte';
  import {
    colourPalette,
    cellTransparency,
    colourScale,
    habitatSystem,
    normalizeColoursBySpecies,
    type HabitatSystem
  } from '$lib/stores/map';
  import { colourPalettes, type ColourPaletteId } from '$lib/map/colourPalettes';
  import { type ColourScaleMode } from '$lib/map/colourScales';

  let colourMenuOpen = $state(false);
  let colourControl: HTMLDivElement | undefined = $state();
  let colourButton: HTMLButtonElement;
  const scaleModes: Array<{ value: ColourScaleMode; label: string }> = [
    { value: 'linear', label: 'Linear' },
    { value: 'log', label: 'Logarithmic' },
    { value: 'jenks', label: 'Jenks natural breaks' }
  ];

  type Parameter = 'log' | 'gamma' | 'jenks';
  const parameters = {
    log: { key: 'logOffset', name: 'Log offset', min: 0.01, max: 1000, step: 0.1 },
    gamma: { key: 'logGamma', name: 'Gamma', min: 0.1, max: 5, step: 0.1 },
    jenks: { key: 'classes', name: 'Jenks classes', min: 2, max: 9, step: 1 }
  } as const;

  function setParameter(mode: Parameter, value: number) {
    const parameter = parameters[mode];
    if (!Number.isFinite(value)) return;
    if (value < parameter.min || value > parameter.max) return;
    if (mode === 'jenks' && !Number.isInteger(value)) return;
    colourScale.update((settings) => ({
      ...settings,
      [parameter.key]: value
    }));
  }

  function stepParameter(mode: Parameter, direction: 1 | -1) {
    const parameter = parameters[mode];
    const value = $colourScale[parameter.key];
    const next = Math.min(
      parameter.max,
      Math.max(parameter.min, Math.round((value + direction * parameter.step) * 100) / 100)
    );
    setParameter(mode, next);
  }
  const activePalette = $derived(
    colourPalettes.find((palette) => palette.id === $colourPalette) ?? colourPalettes[0]
  );
  const activeGradientStops = $derived(activePalette.cssGradient.match(/#[0-9a-f]{6}/gi) ?? []);

  function closeColourMenu() {
    colourMenuOpen = false;
  }

  function handleWindowClick(event: MouseEvent) {
    if (colourMenuOpen && !colourControl?.contains(event.target as Node)) closeColourMenu();
  }

  function handleWindowKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape' && colourMenuOpen) {
      closeColourMenu();
      colourButton?.focus({ preventScroll: true });
    }
  }

  const systems: Array<{ value: HabitatSystem; label: string }> = [
    { value: '', label: 'All' },
    { value: 'Terrestrial', label: 'Terrestrial' },
    { value: 'Freshwater', label: 'Freshwater' },
    { value: 'Marine', label: 'Marine' }
  ];
</script>

{#snippet scaleNumber(mode: Parameter)}
  {@const parameter = parameters[mode]}
  {@const value = $colourScale[parameter.key]}
  {@const name = parameter.name}
  <div class="scale-number-control">
    <div class="scale-number">
      <input
        type="number"
        min={parameter.min}
        max={parameter.max}
        step={mode === 'jenks' ? 1 : 'any'}
        aria-label={name}
        aria-describedby={`${mode}-parameter-tooltip`}
        {value}
        oninput={(event) => setParameter(mode, event.currentTarget.valueAsNumber)}
        onblur={(event) => {
          event.currentTarget.value = String(value);
        }}
        onkeydown={(event) => {
          if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
            event.preventDefault();
            stepParameter(mode, event.key === 'ArrowUp' ? 1 : -1);
          }
        }}
      />
      <div class="number-arrows">
        <button
          type="button"
          aria-label={`Increase ${name.toLowerCase()}`}
          disabled={value >= parameter.max}
          onclick={() => stepParameter(mode, 1)}
          ><ChevronUp size={12} strokeWidth={1.8} aria-hidden="true" /></button
        >
        <button
          type="button"
          aria-label={`Decrease ${name.toLowerCase()}`}
          disabled={value <= parameter.min}
          onclick={() => stepParameter(mode, -1)}
          ><ChevronDown size={12} strokeWidth={1.8} aria-hidden="true" /></button
        >
      </div>
    </div>
    <span id={`${mode}-parameter-tooltip`} class="parameter-tooltip" role="tooltip">
      {#if mode === 'log'}
        Log offset (0.01–1000). Higher values reduce the log boost and approach linear before
        contrast is applied.
      {:else if mode === 'gamma'}
        Gamma (0.1–5). Above 1 pushes medium scores lower so high scores stand out. 1 keeps the
        original log scale.
      {:else}
        Number of colour classes (2–9). More classes show finer differences between scores.
      {/if}
    </span>
  </div>
{/snippet}

<svelte:window onclick={handleWindowClick} onkeydown={handleWindowKeydown} />

<div class="map-options">
  <div class="system-filter no-margin-top">
    <div class="pill-tabs" aria-label="Ecosystem filter">
      <span
        class="pill-indicator"
        aria-hidden="true"
        style:--active-index={systems.findIndex((item) => item.value === $habitatSystem)}
      ></span>
      {#each systems as item (item.value)}
        <button
          type="button"
          class:is-active={$habitatSystem === item.value}
          class="pill-tab"
          aria-pressed={$habitatSystem === item.value}
          onclick={() => habitatSystem.set(item.value)}>{item.label}</button
        >
      {/each}
    </div>
  </div>
  <div class="normalization-control" class:is-open={colourMenuOpen} bind:this={colourControl}>
    <button
      type="button"
      class="normalization-button"
      bind:this={colourButton}
      class:is-active={colourMenuOpen}
      aria-label="Map colour and normalization options"
      aria-expanded={colourMenuOpen}
      aria-controls="map-colour-menu"
      aria-describedby="normalization-tooltip"
      onclick={() => {
        if (colourMenuOpen) closeColourMenu();
        else colourMenuOpen = true;
      }}
    >
      <svg
        class="gradient-bucket"
        width="20"
        height="20"
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
        shape-rendering="geometricPrecision"
      >
        <defs>
          <linearGradient
            id={`bucket-gradient-${activePalette.id}`}
            x1="2"
            y1="12"
            x2="22"
            y2="12"
            gradientUnits="userSpaceOnUse"
          >
            {#each activeGradientStops as colour, index}
              <stop
                offset={`${(index / Math.max(1, activeGradientStops.length - 1)) * 100}%`}
                stop-color={colour}
              />
            {/each}
          </linearGradient>
        </defs>
        <g
          stroke={`url(#bucket-gradient-${activePalette.id})`}
          stroke-width="2.15"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="M11 7 6 2" />
          <path d="M18.992 12H2.041" />
          <path
            d="M21.145 18.38A3.34 3.34 0 0 1 20 16.5a3.3 3.3 0 0 1-1.145 1.88c-.575.46-.855 1.02-.855 1.595A2 2 0 0 0 20 22a2 2 0 0 0 2-2.025c0-.58-.285-1.13-.855-1.595"
          />
          <path
            d="m8.5 4.5 2.148-2.148a1.205 1.205 0 0 1 1.704 0l7.296 7.296a1.205 1.205 0 0 1 0 1.704l-7.592 7.592a3.615 3.615 0 0 1-5.112 0l-3.888-3.888a3.615 3.615 0 0 1 0-5.112L5.67 7.33"
          />
        </g>
      </svg>
    </button>
    <span id="normalization-tooltip" class="normalization-tooltip" role="tooltip"
      >Map colour options</span
    >
    {#if colourMenuOpen}
      <div id="map-colour-menu" class="colour-menu" role="dialog" aria-label="Map colour options">
        <div class="colour-choices">
          <div class="colour-menu-heading">
            <strong>Map colours</strong>
            <span>Low → high priority</span>
          </div>
          <fieldset class="scale-options">
            <legend class="sr-only">Colour scale</legend>
            {#each scaleModes as mode (mode.value)}
              <div
                class={[
                  'scale-option',
                  {
                    'is-selected': $colourScale.mode === mode.value,
                    'has-gamma': mode.value === 'log' && $colourScale.mode === 'log'
                  }
                ]}
              >
                <label>
                  <input
                    type="radio"
                    name="map-colour-scale"
                    value={mode.value}
                    checked={$colourScale.mode === mode.value}
                    onchange={() =>
                      colourScale.update((settings) => ({
                        ...settings,
                        mode: mode.value
                      }))}
                  />
                  <span>{mode.label}</span>
                </label>
                {#if mode.value !== 'linear'}
                  {@render scaleNumber(mode.value)}
                {/if}
                {#if mode.value === 'log' && $colourScale.mode === 'log'}
                  <div class="gamma-option">
                    <span>Gamma</span>
                    {@render scaleNumber('gamma')}
                  </div>
                {/if}
              </div>
            {/each}
          </fieldset>
          <div class="palette-options" role="radiogroup" aria-label="Colour palette">
            {#each colourPalettes as palette (palette.id)}
              <button
                type="button"
                class="palette-option"
                class:is-selected={$colourPalette === palette.id}
                role="radio"
                aria-checked={$colourPalette === palette.id}
                onclick={() => colourPalette.set(palette.id as ColourPaletteId)}
              >
                <span
                  class="palette-option-swatch"
                  style:--palette-gradient={palette.cssGradient}
                  aria-hidden="true"
                ></span>
                <span>{palette.label.replace(' · accessible', '')}</span>
                <span class="palette-selected-mark" aria-hidden="true"></span>
              </button>
            {/each}
          </div>
          <button
            type="button"
            class="normalization-option"
            role="switch"
            aria-checked={$normalizeColoursBySpecies}
            onclick={() => normalizeColoursBySpecies.update((enabled) => !enabled)}
          >
            <span>
              <strong>Per species</strong>
              <small>Normalize scores by species count</small>
            </span>
            <span
              class="menu-switch"
              class:is-active={$normalizeColoursBySpecies}
              aria-hidden="true"><i></i></span
            >
          </button>
          <div class="transparency-control">
            <label for="cell-transparency">Transparency</label>
            <output for="cell-transparency">{$cellTransparency}%</output>
            <input
              id="cell-transparency"
              type="range"
              min="0"
              max="100"
              step="1"
              bind:value={$cellTransparency}
            />
          </div>
        </div>
      </div>
    {/if}
  </div>
</div>

<style>
  .map-options {
    --map-options-size: 3.2rem;
    position: relative;
    z-index: 6;
  }
  .system-filter {
    display: flex;
  }
  .pill-tabs {
    width: min(28.75rem, calc(100vw - 6.5rem));
    height: var(--map-options-size);
  }
  .pill-tab {
    width: auto;
    flex: 1;
    min-width: 0;
  }
  .pill-indicator {
    width: calc((100% - 1.25rem) / 4);
    left: calc(0.25rem + var(--active-index) * (100% - 0.25rem) / 4);
  }
  .normalization-button {
    width: var(--map-options-size);
    height: var(--map-options-size);
    border-radius: 50%;
  }
  .colour-menu {
    border-radius: var(--map-radius);
    top: calc(100% + 0.65rem);
    left: auto;
    right: 0;
    transform-origin: top right;
    animation: colour-pop 160ms ease-out;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    max-width: calc(100vw - 2rem);
    max-height: min(38rem, calc(100svh - 15rem));
    overflow-y: auto;
  }
  .scale-options {
    margin: 0;
    padding: 0.35rem 0.42rem 0.5rem;
    border: 0;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
  .scale-option {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.4rem;
    border: 1px solid transparent;
    border-radius: var(--border-radius-base);
    font-size: 0.75rem;
    cursor: pointer;
  }
  .scale-option.is-selected {
    background: var(--color-primary-green-5);
    border-color: var(--color-primary-green-20);
  }
  .scale-option.has-gamma {
    flex-wrap: wrap;
  }
  .gamma-option {
    display: flex;
    flex: 0 0 100%;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 0.35rem 0 0 1.25rem;
    border-top: 1px solid var(--color-primary-green-20);
    font-size: 0.75rem;
    color: var(--color-gray-700);
  }
  .scale-option input[type='radio'] {
    accent-color: var(--color-gray-700);
    margin: 0;
  }
  .scale-option label {
    display: flex;
    flex: 1;
    align-items: center;
    gap: 0.5rem;
    min-height: 1.5rem;
    cursor: pointer;
  }
  .scale-number-control {
    position: relative;
    flex: 0 0 4rem;
  }
  .scale-number {
    display: flex;
    overflow: hidden;
    border: 1px solid var(--color-gray-300);
    border-radius: var(--border-radius-base);
    background: var(--color-light-cream);
  }
  .scale-number input {
    width: 0;
    min-width: 0;
    flex: 1;
    padding: 0.2rem 0.3rem;
    border: 0;
    background: transparent;
    font-size: 0.75rem;
    appearance: textfield;
    -moz-appearance: textfield;
  }
  .scale-number input::-webkit-inner-spin-button,
  .scale-number input::-webkit-outer-spin-button {
    appearance: none;
    margin: 0;
  }
  .number-arrows {
    display: grid;
    grid-template-rows: repeat(2, 1fr);
  }
  .number-arrows button {
    display: grid;
    place-items: center;
    width: 1.15rem;
    padding: 0.05rem;
    color: var(--color-gray-700);
    cursor: pointer;
  }
  .number-arrows button:hover:not(:disabled) {
    background: var(--color-primary-green-10);
  }
  .number-arrows button:disabled {
    opacity: 0.3;
    cursor: default;
  }
  .number-arrows button:focus-visible {
    outline: 2px solid var(--color-primary-green);
    outline-offset: -2px;
  }
  .scale-number:focus-within {
    border-color: var(--color-primary-green);
  }
  .parameter-tooltip {
    position: absolute;
    z-index: 2;
    top: calc(100% + 0.35rem);
    right: 0;
    width: 13rem;
    padding: 0.55rem 0.65rem;
    border-radius: var(--border-radius-base);
    background: var(--color-gray-700);
    color: var(--color-light-cream);
    font-size: 0.6875rem;
    line-height: 1.5;
    visibility: hidden;
    opacity: 0;
    pointer-events: none;
  }
  .scale-number-control:hover .parameter-tooltip,
  .scale-number-control:focus-within .parameter-tooltip {
    visibility: visible;
    opacity: 1;
  }
  .transparency-control {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 0.6rem;
    padding: 0.75rem 0.85rem;
    border-top: 1px solid var(--color-gray-200);
    font-size: 0.75rem;
    color: var(--color-gray-700);
  }
  .transparency-control output {
    font-variant-numeric: tabular-nums;
  }
  .transparency-control input {
    grid-column: 1 / -1;
    width: 100%;
    margin: 0;
    accent-color: var(--color-primary-green);
    cursor: pointer;
  }
  .scale-option input:focus-visible,
  .transparency-control input:focus-visible {
    outline: 2px solid var(--color-primary-green);
    outline-offset: 2px;
  }
  .colour-menu-heading strong,
  .normalization-option strong {
    font-size: 0.8125rem;
  }
  .colour-menu-heading span,
  .normalization-option small {
    font-size: 0.6875rem;
    line-height: 1.4;
  }
  .palette-option > span:nth-child(2) {
    font-size: 0.75rem;
  }
  @media (max-width: 600px) {
    .pill-tab {
      font-size: 0.75rem;
    }
  }
  @media (max-width: 380px) {
    .pill-tabs {
      display: grid;
      grid-template-columns: repeat(4, auto);
      gap: 0.125rem;
    }
    .pill-tab {
      height: 100%;
      padding-inline: 0.25rem;
      font-size: 0.6875rem;
    }
    .pill-indicator {
      display: none;
    }
    .pill-tab.is-active {
      background: var(--color-cream);
      box-shadow:
        0 1px 2px rgba(0, 0, 0, 0.06),
        0 0 0 1px var(--color-tinted-cream);
    }
  }
  @keyframes colour-pop {
    from {
      opacity: 0;
      transform: translateY(-4px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .colour-menu {
      animation: none;
    }
    .normalization-button,
    .pill-indicator {
      transition: none;
    }
    .normalization-button:hover {
      transform: none;
    }
  }
</style>
