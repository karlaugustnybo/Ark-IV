<script lang="ts">
  import { ignoresWindowShortcut } from '$lib/map/keyboardShortcuts';
  import { onMount, type Snippet } from 'svelte';
  import { get } from 'svelte/store';
  import { H3HexagonLayer, MVTLayer } from '@deck.gl/geo-layers';
  import { StableH3HexagonLayer } from '$lib/map/StableH3HexagonLayer';
  import { MapboxOverlay } from '@deck.gl/mapbox';
  import {
    FullscreenControl,
    Map as MapLibreMap,
    NavigationControl,
    type StyleSpecification
  } from 'maplibre-gl';
  import 'maplibre-gl/dist/maplibre-gl.css';
  import { PMTiles } from 'pmtiles';
  import { cellToParent, cellToCenterChild } from 'h3-js';
  import { LoaderCircle } from 'lucide-svelte';
  import { config } from '$lib/config';
  import SpeciesHighlight from '$lib/components/SpeciesHighlight.svelte';
  import {
    colourPalette,
    colourScale,
    cellTransparency,
    habitatSystem,
    normalizeColoursBySpecies,
    resolution,
    selectedH3,
    selectedH3Resolution
  } from '$lib/stores/map';
  import { colourPaletteLuts, type ColourPaletteId } from '$lib/map/colourPalettes';
  import { colourScalePosition } from '$lib/map/colourScales';
  import {
    selectedSpecies,
    speciesHighlight,
    speciesHighlightGradient,
    type SelectedSpecies
  } from '$lib/stores/species';
  import { api } from '$lib/api/client';
  import { weights } from '$lib/stores/weights';
  import {
    compactMetric,
    compactMetricIndex,
    type CompactCell,
    type CompactMetricName,
    type CompactTile
  } from '$lib/map/compactTiles';
  import {
    FineTileCoordinator,
    fineTileIndices,
    type FineTileChunk,
    type FineTileIndex,
    type FineTileSource
  } from '$lib/map/fineTileCoordinator';
  import type { MapPerformanceMonitor, MapPerformanceResult } from '$lib/map/mapPerformance';
  import { FineTileRenderQueue, type ResidentFineTileChunk } from '$lib/map/fineTileRenderQueue';
  import type { MapPerformanceTraceSummary } from '$lib/map/mapPerformanceTrace';
  import {
    preloadMapBundle,
    type CoarseSnapshot,
    type MapMetadata,
    type ScoreDomain,
    type TileZoomRange
  } from '$lib/map/mapBundle';
  import { resolveMapPick } from '$lib/map/mapPicking';
  import type { CellSearchSelection } from '$lib/map/cellSearch';
  import { knowledgeLimitsText, occupancyCaveatText } from '$lib/map/cellCaveats';
  import {
    hasActiveSpatialFilters,
    scoreDomainForItems,
    selectScoreDomain
  } from '$lib/map/mapDomains';
  import {
    boundaryCollections,
    boundaryFilters,
    loadBoundaryCollection,
    loadBoundaryFrameworks,
    type BoundaryCollection
  } from '$lib/stores/boundaries';

  export let toolbar: Snippet | undefined = undefined;

  type MetricValues = {
    total: number;
    cr: number;
    en: number;
    vu: number;
    nt: number;
    dd: number;
    lc: number;
    ms: number;
    mg: number;
    mf: number;
    gdd: number;
  };
  const threatWeightKeys = ['cr', 'en', 'vu', 'nt', 'dd', 'lc'] as const;
  const dnaWeightKeys = ['gdd', 'fam', 'gen', 'sp', 'samp'] as const;
  type TileProperties = Record<string, string | number> & {
    h3_index: string;
    resolution: number;
    j?: string;
    a1?: string;
  };
  type Feature = { properties: TileProperties };
  type MapCamera = { longitude: number; latitude: number; zoom: number };

  // Switch when a coarse cell roughly fills the map frame.
  const RESOLUTION_SWITCH_ZOOM = 9;
  // Begin before the handoff so the first fine generation is ready while the
  // user is still seeing the global coarse snapshot. The forecast targets the
  // smaller first fine viewport, avoiding a large speculative GPU upload.
  const RESOLUTION_PRELOAD_ZOOM = RESOLUTION_SWITCH_ZOOM - 2.5;
  const INITIAL_FINE_ZOOM = RESOLUTION_SWITCH_ZOOM;
  const SEARCH_COARSE_ZOOM = 5.5;
  const SEARCH_FINE_ZOOM = RESOLUTION_SWITCH_ZOOM + 0.2;
  const SEARCH_FLIGHT_MS = 1400;
  const FINE_FORECAST_INTERVAL_MS = 180;
  const FINE_ACTIVE_INTERVAL_MS = 120;
  const PRIORITY_TILE_CACHE_SIZE = 64;
  const SPECIES_DISTRIBUTION_CACHE_SIZE = 12;
  const COARSE_CELL_ELEVATION = 2000;
  const FINE_CELL_ELEVATION = 500;
  // Keep backing-store resolution stable across interaction and settle states.
  const RENDER_PIXEL_RATIO = 2;

  function retinaRasterTileUrl(url: string): string {
    if (url.includes('{ratio}')) return url.replace('{ratio}', '@2x');
    return url.replace(/\.(png|jpe?g|webp)(?=\?|$)/i, '@2x.$1');
  }

  const basemapTileUrl = retinaRasterTileUrl(config.basemapUrl);

  let container: HTMLDivElement;
  let fsWrap: HTMLDivElement;
  let deck: MapboxOverlay | null = null;
  let map: MapLibreMap | null = null;
  let archive: PMTiles;
  let coarseSnapshot: CoarseSnapshot | null = null;
  let staticBasemap: GeoJSON.FeatureCollection | null = null;
  let currentResolution: 3 | 7 = 3;
  let currentWeights = get(weights);
  let currentSystem = get(habitatSystem);
  let currentMetricPrefix = metricPrefix(currentSystem);
  let weightedMetrics = compileWeightedMetrics();
  let currentNormalizeBySpecies = get(normalizeColoursBySpecies);
  let currentColourPalette = get(colourPalette);
  let currentColourScale = get(colourScale);
  let currentCellTransparency = get(cellTransparency);
  let cellAlpha = Math.round(255 * (1 - currentCellTransparency / 100));
  let classificationWorker: Worker | null = null;
  let classificationKey = '';
  let classificationRequest = 0;
  let classificationBreaks: number[] = [];
  let classificationRevision = 0;
  let currentSelected = get(selectedH3);
  let currentSelectedResolution = get(selectedH3Resolution);
  let currentSpecies = get(selectedSpecies);
  let currentSpeciesGradient = get(speciesHighlightGradient);
  let scoreDomains: Record<string, ScoreDomain> = {};
  let normalizedScoreDomains: Record<string, ScoreDomain> = {};
  let boundaryScoreDomains: Record<string, Record<string, Record<string, ScoreDomain>>> = {};
  let normalizedBoundaryScoreDomains: Record<
    string,
    Record<string, Record<string, ScoreDomain>>
  > = {};
  let currentBoundaryFilters: Record<string, string[]> = {};
  let currentBoundaryCollections: Record<string, BoundaryCollection> = {};
  let boundaryTileProperties: Record<string, string> = {
    admin0: 'j',
    admin1: 'a1',
    municipality: 'mun',
    eez: 'eez',
    conservation_framework: 'eco'
  };
  let availableResolutions: Array<3 | 7> = [3, 7];
  let resolutionTileRanges: Record<3 | 7, TileZoomRange> = {
    3: { min: 0, max: 6 },
    7: { min: 8, max: 12 }
  };
  let partialResolution7 = false;
  let dynamicResolution7 = false;
  let dynamicTileVersion = 0;
  let fineViewportReady = false;
  let fineCoverageReady = false;
  let fineTilesLoading = false;
  let committedFineChunks: FineTileChunk[] = [];
  let committedFineCellCount = 0;
  let committedFineTileCount = 0;
  let committedFineSource = '';
  let fineRequestCount = 0;
  let fineTileCoordinator: FineTileCoordinator | null = null;
  let fineTileRenderQueue: FineTileRenderQueue | null = null;
  let displayedFineTileKeys = new Set<string>();
  let fineWarmupChunk: ResidentFineTileChunk | null = null;
  let fineBuffersLoading = false;
  let currentZoom = 0;
  let currentPitch = 0;
  let currentCamera: MapCamera = { longitude: 0, latitude: 0, zoom: 0 };
  // Camera events can fire every animation frame. Keep their live values out
  // of Svelte's rendered state and publish diagnostics only at moveend.
  let liveZoom = currentZoom;
  let livePitch = currentPitch;
  let liveCamera: MapCamera = currentCamera;
  let lastFineForecastAt = 0;
  let lastFineActiveScheduleAt = 0;
  let domainMin = 0;
  let domainMax = 1;
  let cachedCoarseDomain: { key: string; domain: ScoreDomain | undefined } | null = null;
  let cachedFineDomain: {
    key: string;
    chunks: FineTileChunk[];
    domain: ScoreDomain | undefined;
  } | null = null;
  let staticDomainGeneration = 0;
  let staticDomainFrame: number | null = null;
  const staticDomainValues = new Map<string, number>();
  let mapError = '';
  let basemapReady = false;
  let subscriptions: Array<() => void> = [];
  const speciesDistributionCache = new Map<
    string,
    { resolution: 3 | 7; cells: readonly string[] }
  >();
  let matchingSpeciesCells = new Set<string>();
  let speciesHighlightResolution: 3 | 7 = 3;
  let speciesHighlightReady = false;
  let speciesRequestId = 0;
  let speciesAbortController: AbortController | null = null;
  let speciesHighlightRevision = 0;
  let isInteracting = false;
  let boundaryNames: Record<string, Map<string, string>> = {};
  let performanceMonitor: MapPerformanceMonitor | null = null;
  let performanceEnabled = false;
  let performanceStatus: 'disabled' | 'idle' | 'recording' | 'complete' = 'disabled';
  let performanceMode: 'gesture' | 'session' = 'gesture';
  let performanceSessionActive = false;
  let performanceResult: MapPerformanceResult | null = null;
  let performanceTraceRequested = false;
  let performanceTraceActive = false;
  let performanceTraceStatus: 'disabled' | 'idle' | 'running' | 'complete' | 'error' = 'disabled';
  let performanceTraceRunCount = 3;
  let performanceTraceCurrentRun = 0;
  let performanceTraceResult: MapPerformanceTraceSummary | null = null;
  let performanceTraceError = '';
  let performanceRenderer: 'full' | 'basemap-only' | 'cells-only' | 'empty' = 'full';
  const basemapPixelRatio = RENDER_PIXEL_RATIO;
  const cellPixelRatio = RENDER_PIXEL_RATIO;

  function startPerformanceMeasurement(mode: 'gesture' | 'session') {
    if (!performanceMonitor) return;
    performanceResult = null;
    performanceMode = mode;
    performanceSessionActive = mode === 'session';
    performanceStatus = 'recording';
    performanceMonitor.start();
  }

  function finishPerformanceMeasurement() {
    const result = performanceMonitor?.stop();
    performanceSessionActive = false;
    if (!result) return;
    performanceResult = result;
    performanceStatus = 'complete';
  }

  function metricPrefix(system: string): string {
    return system === 'Terrestrial'
      ? 't'
      : system === 'Freshwater'
        ? 'f'
        : system === 'Marine'
          ? 'm'
          : 'a';
  }

  type WeightedMetric = {
    property: string;
    compactIndex: number;
    compactName: CompactMetricName;
    multiplier: number;
  };

  function compileWeightedMetrics(): WeightedMetric[] {
    const compiled: WeightedMetric[] = [];
    for (const threat of threatWeightKeys) {
      for (const dna of dnaWeightKeys) {
        const multiplier = currentWeights[threat] * currentWeights[dna];
        if (multiplier !== 0) {
          compiled.push({
            property: `${currentMetricPrefix}_${threat}_${dna}`,
            compactName: `${threat}_${dna}` as CompactMetricName,
            compactIndex: compactMetricIndex(`${threat}_${dna}` as CompactMetricName),
            multiplier
          });
        }
      }
    }
    return compiled;
  }

  function updateScoreModel() {
    currentMetricPrefix = metricPrefix(currentSystem);
    weightedMetrics = compileWeightedMetrics();
  }

  function boundaryCodes(properties: TileProperties, framework: string): string[] {
    const propertyName = boundaryTileProperties[framework];
    if (!propertyName) return [];
    return String(properties[propertyName] ?? '')
      .split('|')
      .filter(Boolean);
  }

  function boundaryName(framework: string, code: string): string {
    return boundaryNames[framework]?.get(code) ?? code;
  }

  function escapeTooltipText(value: unknown): string {
    return String(value ?? '').replace(
      /[&<>"']/g,
      (character) =>
        ({
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#39;'
        })[character] ?? character
    );
  }

  function isIncluded(properties: TileProperties): boolean {
    return Object.entries(currentBoundaryFilters).every(([framework, selected]) => {
      if (!selected.length) return true;
      const memberships = new Set(boundaryCodes(properties, framework));
      return selected.some((code) => memberships.has(code));
    });
  }

  function metrics(properties: TileProperties): MetricValues {
    const value = (name: string) => Number(properties[`${currentMetricPrefix}_${name}`] ?? 0);
    return {
      total: value('total'),
      cr: value('cr'),
      en: value('en'),
      vu: value('vu'),
      nt: value('nt'),
      dd: value('dd'),
      lc: value('lc'),
      ms: value('ms'),
      mg: value('mg'),
      mf: value('mf'),
      gdd: value('gdd')
    };
  }

  function compactMetrics(cell: CompactCell): MetricValues {
    return {
      total: compactMetric(cell, 'total'),
      cr: compactMetric(cell, 'cr'),
      en: compactMetric(cell, 'en'),
      vu: compactMetric(cell, 'vu'),
      nt: compactMetric(cell, 'nt'),
      dd: compactMetric(cell, 'dd'),
      lc: compactMetric(cell, 'lc'),
      ms: compactMetric(cell, 'ms'),
      mg: compactMetric(cell, 'mg'),
      mf: compactMetric(cell, 'mf'),
      gdd: compactMetric(cell, 'gdd')
    };
  }

  function score(properties: TileProperties): number {
    let total = 0;
    for (const metric of weightedMetrics) {
      total += Number(properties[metric.property] ?? 0) * metric.multiplier;
    }
    return total;
  }

  function compactScore(cell: CompactCell): number {
    let total = 0;
    for (const metric of weightedMetrics) {
      total += Number(cell[metric.compactIndex] ?? 0) * metric.multiplier;
    }
    return total;
  }

  function coarseMetric(index: number, name: CompactMetricName): number {
    return coarseSnapshot?.metric(currentMetricPrefix, name, index) ?? 0;
  }

  function coarseMetrics(index: number): MetricValues {
    return {
      total: coarseMetric(index, 'total'),
      cr: coarseMetric(index, 'cr'),
      en: coarseMetric(index, 'en'),
      vu: coarseMetric(index, 'vu'),
      nt: coarseMetric(index, 'nt'),
      dd: coarseMetric(index, 'dd'),
      lc: coarseMetric(index, 'lc'),
      ms: coarseMetric(index, 'ms'),
      mg: coarseMetric(index, 'mg'),
      mf: coarseMetric(index, 'mf'),
      gdd: coarseMetric(index, 'gdd')
    };
  }

  function coarseScore(index: number): number {
    let total = 0;
    for (const metric of weightedMetrics) {
      total += coarseMetric(index, metric.compactName) * metric.multiplier;
    }
    return total;
  }

  function coarseIncluded(index: number): boolean {
    if (!coarseSnapshot) return false;
    return Object.entries(currentBoundaryFilters).every(([framework, selected]) => {
      if (!selected.length) return true;
      const memberships = new Set(coarseSnapshot?.boundaryCodes(framework, index) ?? []);
      return selected.some((code) => memberships.has(code));
    });
  }

  function paletteColour(normalized: number): [number, number, number, number] {
    const colours = colourPaletteLuts[currentColourPalette];
    const [red, green, blue] = colours[Math.round(Math.min(1, Math.max(0, normalized)) * 255)];
    return [red, green, blue, cellAlpha];
  }

  function scaleColour(
    value: number,
    domain: ScoreDomain = { min: domainMin, max: domainMax }
  ): [number, number, number, number] {
    return paletteColour(
      colourScalePosition(value, domain, currentColourScale, classificationBreaks)
    );
  }

  function updateClassification() {
    if (currentColourScale.mode !== 'jenks') return;
    // Use the entire filtered global snapshot as an explicit common reference
    // for every layer. Fine viewport contents never enter this classification.
    const key = JSON.stringify([
      currentSystem,
      currentNormalizeBySpecies,
      currentWeights,
      currentBoundaryFilters,
      currentColourScale.classes
    ]);
    if (key === classificationKey) return;
    classificationKey = key;
    const id = ++classificationRequest;
    classificationBreaks = [];
    classificationRevision += 1;
    if (!coarseSnapshot) {
      return;
    }
    if (!classificationWorker) {
      classificationWorker = new Worker(new URL('../map/colourScale.worker.ts', import.meta.url), {
        type: 'module'
      });
      classificationWorker.onmessage = (event: MessageEvent<{ id: number; breaks: number[] }>) => {
        if (event.data.id !== classificationRequest) return;
        classificationBreaks = event.data.breaks;
        classificationRevision += 1;
        refresh();
      };
      classificationWorker.onerror = () => {
        classificationBreaks = [];
        classificationRevision += 1;
        classificationKey = '';
        classificationWorker?.terminate();
        classificationWorker = null;
        refresh();
      };
    }
    const scores: number[] = [];
    for (const index of coarseSnapshot.indices) {
      if (coarseIncluded(index) && coarseMetric(index, 'total') > 0)
        scores.push(coarseColourScore(index));
    }
    const transferred = Float64Array.from(scores);
    classificationWorker.postMessage(
      { id, scores: transferred, classes: currentColourScale.classes },
      [transferred.buffer]
    );
  }

  function colourScore(properties: TileProperties): number {
    const rawScore = score(properties);
    const speciesCount = Number(properties[`${currentMetricPrefix}_total`] ?? 0);
    return currentNormalizeBySpecies && speciesCount > 0 ? rawScore / speciesCount : rawScore;
  }

  function compactColourScore(cell: CompactCell): number {
    const rawScore = compactScore(cell);
    const speciesCount = compactMetric(cell, 'total');
    return currentNormalizeBySpecies && speciesCount > 0 ? rawScore / speciesCount : rawScore;
  }

  function coarseColourScore(index: number): number {
    const rawScore = coarseScore(index);
    const speciesCount = coarseMetric(index, 'total');
    return currentNormalizeBySpecies && speciesCount > 0 ? rawScore / speciesCount : rawScore;
  }

  function isSpeciesMatch(properties: TileProperties): boolean {
    if (!currentSpecies || !speciesHighlightReady) return false;
    const cellResolution = Number(properties.resolution);
    const h3Index = String(properties.h3_index);
    if (cellResolution === speciesHighlightResolution) return matchingSpeciesCells.has(h3Index);
    if (cellResolution > speciesHighlightResolution) {
      try {
        return matchingSpeciesCells.has(cellToParent(h3Index, speciesHighlightResolution));
      } catch {
        return false;
      }
    }
    return false;
  }

  function fillColor(properties: TileProperties): [number, number, number, number] {
    if (!isIncluded(properties)) return [0, 0, 0, 0];
    const value = colourScore(properties);
    recordStaticDomainValue(properties, value);
    const normal = scaleColour(value);
    if (!currentSpecies || !speciesHighlightReady) return normal;
    if (!isSpeciesMatch(properties))
      return [normal[0], normal[1], normal[2], Math.round(cellAlpha * 0.28)];
    return currentSpeciesGradient ? normal : [0, 140, 255, cellAlpha];
  }

  function isCompactSpeciesMatch(cell: CompactCell): boolean {
    if (!currentSpecies || !speciesHighlightReady) return false;
    if (speciesHighlightResolution === 7) return matchingSpeciesCells.has(cell[0]);
    try {
      return matchingSpeciesCells.has(cellToParent(cell[0], speciesHighlightResolution));
    } catch {
      return false;
    }
  }

  function compactFillColor(
    cell: CompactCell,
    domain: ScoreDomain
  ): [number, number, number, number] {
    const value = compactColourScore(cell);
    const normal = scaleColour(value, domain);
    if (!currentSpecies || !speciesHighlightReady) return normal;
    if (!isCompactSpeciesMatch(cell))
      return [normal[0], normal[1], normal[2], Math.round(cellAlpha * 0.28)];
    return currentSpeciesGradient ? normal : [0, 140, 255, cellAlpha];
  }

  function coarseSpeciesMatch(index: number): boolean {
    if (!coarseSnapshot || !currentSpecies || !speciesHighlightReady) return false;
    return matchingSpeciesCells.has(coarseSnapshot.h3Indexes[index]);
  }

  function coarseFillColor(index: number, domain: ScoreDomain): [number, number, number, number] {
    if (!coarseIncluded(index)) return [0, 0, 0, 0];
    const value = coarseColourScore(index);
    const normal = scaleColour(value, domain);
    if (!currentSpecies || !speciesHighlightReady) return normal;
    if (!coarseSpeciesMatch(index))
      return [normal[0], normal[1], normal[2], Math.round(cellAlpha * 0.28)];
    return currentSpeciesGradient ? normal : [0, 140, 255, cellAlpha];
  }

  function applyScoreDomain(domain: ScoreDomain): boolean {
    if (domainMin === domain.min && domainMax === domain.max) return false;
    domainMin = domain.min;
    domainMax = domain.max;
    return true;
  }

  function fineCells(): Iterable<CompactCell> {
    return {
      *[Symbol.iterator]() {
        for (const chunk of committedFineChunks) yield* chunk.cells;
      }
    };
  }

  function renderedScoreDomain(cellResolution: 3 | 7 = currentResolution): ScoreDomain | undefined {
    const key = JSON.stringify([
      currentSystem,
      currentNormalizeBySpecies,
      currentWeights,
      currentBoundaryFilters
    ]);
    if (cellResolution === 3 && coarseSnapshot) {
      if (cachedCoarseDomain?.key !== key) {
        cachedCoarseDomain = {
          key,
          domain: scoreDomainForItems(coarseSnapshot.indices, coarseColourScore, coarseIncluded)
        };
      }
      return cachedCoarseDomain.domain;
    }
    if (
      cellResolution === 7 &&
      dynamicResolution7 &&
      hasActiveSpatialFilters(currentBoundaryFilters) &&
      hasFineDisplayCommit()
    ) {
      if (cachedFineDomain?.key !== key || cachedFineDomain.chunks !== committedFineChunks) {
        cachedFineDomain = {
          key,
          chunks: committedFineChunks,
          domain: scoreDomainForItems(fineCells(), compactColourScore)
        };
      }
      return cachedFineDomain.domain;
    }
    return undefined;
  }

  function layerScoreDomain(cellResolution: 3 | 7): ScoreDomain {
    return (
      renderedScoreDomain(cellResolution) ??
      selectScoreDomain({
        system: currentSystem,
        normalizeBySpecies: currentNormalizeBySpecies,
        filters: currentBoundaryFilters,
        scoreDomains,
        normalizedScoreDomains,
        boundaryScoreDomains,
        normalizedBoundaryScoreDomains
      })
    );
  }

  function resetStaticDomainCollection() {
    staticDomainGeneration += 1;
    staticDomainValues.clear();
    if (staticDomainFrame !== null) cancelAnimationFrame(staticDomainFrame);
    staticDomainFrame = null;
  }

  function usesStaticScoreCollection(): boolean {
    return (
      hasActiveSpatialFilters(currentBoundaryFilters) &&
      !dynamicResolution7 &&
      !(currentResolution === 3 && coarseSnapshot)
    );
  }

  function recordStaticDomainValue(properties: TileProperties, value: number) {
    if (!usesStaticScoreCollection() || Number(properties.resolution) !== currentResolution) return;
    const h3Index = String(properties.h3_index ?? '');
    if (!h3Index || !Number.isFinite(value) || staticDomainValues.get(h3Index) === value) return;
    staticDomainValues.set(h3Index, value);
    if (staticDomainFrame !== null) return;
    const generation = staticDomainGeneration;
    staticDomainFrame = requestAnimationFrame(() => {
      if (generation !== staticDomainGeneration) return;
      staticDomainFrame = null;
      const domain = scoreDomainForItems(staticDomainValues.values(), (score) => score);
      if (domain && applyScoreDomain(domain)) refresh();
    });
  }

  function useSystemDomain(system: string) {
    updateClassification();
    const renderedDomain = renderedScoreDomain();
    if (renderedDomain) {
      applyScoreDomain(renderedDomain);
      return;
    }
    const metadataDomain = selectScoreDomain({
      system,
      normalizeBySpecies: currentNormalizeBySpecies,
      filters: currentBoundaryFilters,
      scoreDomains,
      normalizedScoreDomains,
      boundaryScoreDomains,
      normalizedBoundaryScoreDomains
    });
    applyScoreDomain(metadataDomain);
    resetStaticDomainCollection();
  }

  async function pmtilesFetch(url: string, options?: RequestInit): Promise<Response> {
    const match = url.match(/\/(\d+)\/(\d+)\/(\d+)\.mvt$/);
    if (!match) return fetch(url, options);
    const tile = await archive.getZxy(
      Number(match[1]),
      Number(match[2]),
      Number(match[3]),
      options?.signal ?? undefined
    );
    const data = tile ? new Uint8Array(tile.data) : new Uint8Array();
    const body = data.buffer.slice(
      data.byteOffset,
      data.byteOffset + data.byteLength
    ) as ArrayBuffer;
    return new Response(body, {
      status: 200,
      headers: { 'Content-Type': 'application/vnd.mapbox-vector-tile' }
    });
  }

  function priorityLayer(cellResolution: 3 | 7, selected: string | null) {
    const layerName = `res${cellResolution}`;
    const tileRange = resolutionTileRanges[cellResolution];
    const isCoarseFallback =
      cellResolution === 3 &&
      currentResolution === 7 &&
      (partialResolution7 || !(dynamicResolution7 ? hasCurrentFineCommit() : fineViewportReady));
    const isFinePreload = cellResolution === 7 && currentResolution === 3;
    const visible = cellResolution === currentResolution || isCoarseFallback || isFinePreload;
    return new MVTLayer<TileProperties>({
      id: `priorities-${layerName}`,
      data: 'pmtiles://ark-iv/{z}/{x}/{y}.mvt',
      minZoom: tileRange.min,
      maxZoom: tileRange.max,
      // Keep the fine layer alive behind the render filter at the switch
      // boundary. It warms z8 parents behind the coarse layer, then swaps in
      // atomically once every visible tile is ready.
      visible,
      visibleMaxZoom:
        cellResolution === 3 && !partialResolution7 && !isCoarseFallback
          ? RESOLUTION_SWITCH_ZOOM + 1
          : null,
      visibleMinZoom: cellResolution === 7 ? RESOLUTION_PRELOAD_ZOOM : null,
      zoomOffset: cellResolution === 7 ? 1 : 0,
      maxCacheSize: PRIORITY_TILE_CACHE_SIZE,
      maxRequests: cellResolution === 7 ? 12 : 10,
      debounceTime: cellResolution === 7 && currentResolution === 7 ? 80 : 0,
      // The polygons are translucent, so fallback parent and child tiles must
      // not overlap while a new zoom level is loading.
      refinementStrategy: 'no-overlap',
      binary: true,
      pickable: cellResolution !== 7 || (currentResolution === 7 && fineViewportReady),
      autoHighlight: Object.keys(currentBoundaryFilters).length === 0,
      highlightColor: [255, 255, 255, cellAlpha],
      uniqueIdProperty: 'h3_index',
      highlightedFeatureId: selected,
      loadOptions: { fetch: pmtilesFetch as any, mvt: { layers: [layerName] } },
      filled: true,
      stroked: false,
      extruded: true,
      getElevation: cellResolution === 3 ? COARSE_CELL_ELEVATION : FINE_CELL_ELEVATION,
      getFillColor: (feature: Feature) => fillColor(feature.properties),
      updateTriggers: {
        getFillColor: [
          domainMin,
          domainMax,
          currentSystem,
          currentNormalizeBySpecies,
          currentColourPalette,
          cellAlpha,
          currentColourScale,
          classificationRevision,
          speciesHighlightRevision,
          staticDomainGeneration,
          JSON.stringify(currentWeights),
          JSON.stringify(currentBoundaryFilters)
        ]
      },
      onViewportLoad: cellResolution === 7 ? markStaticFineViewportReady : undefined,
      onTileError: cellResolution === 7 ? handleFineTileError : () => {},
      onClick: ({ object }: any) => {
        if (object?.properties && isIncluded(object.properties)) selectFeature(object);
      }
    });
  }

  function markStaticFineViewportReady() {
    if (dynamicResolution7 || liveZoom < RESOLUTION_PRELOAD_ZOOM || fineViewportReady) return;
    fineViewportReady = true;
    fineCoverageReady = true;
    syncDisplayedResolution();
    refresh();
  }

  function handleFineTileError(reason: unknown) {
    console.error('Unable to load a resolution-7 tile', reason);
  }

  function fineSourceParts() {
    const system = currentSystem.toLowerCase() || 'all';
    const boundaryEntries = Object.entries(currentBoundaryFilters)
      .filter(([, codes]) => codes.length)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([framework, codes]) => [framework, [...codes].sort()] as const);
    const jurisdictionQuery = boundaryEntries
      .map(([framework, codes]) => `&${framework}=${encodeURIComponent(codes.join(','))}`)
      .join('');
    return {
      system,
      jurisdictionQuery,
      key: `${dynamicTileVersion}:${system}:${boundaryEntries
        .map(([framework, codes]) => `${framework}=${codes.join(',')}`)
        .join('&')}`
    };
  }

  function hasCurrentFineCommit(): boolean {
    // Keep the last complete fine generation visible while the next camera
    // generation loads. Coverage readiness is tracked separately for
    // diagnostics; using it as a visibility switch caused res3 to flash on
    // every sufficiently large pan.
    return fineViewportReady && committedFineSource === fineTileSource().key;
  }

  function hasFineDisplayCommit(): boolean {
    // Camera generations may reuse the same filtered source. Other sources
    // must fall back to correctly filtered coarse data while loading.
    return hasCurrentFineCommit();
  }

  function fineTileSource(): FineTileSource {
    const { system, jurisdictionQuery, key } = fineSourceParts();
    const tileRange = resolutionTileRanges[7];
    // Resolution-7 cells do not change with web-tile zoom. Use one stable
    // delivery zoom so camera zooming reuses the same decoded cells instead of
    // downloading a differently partitioned copy of the viewport.
    // Two levels finer than the archive's minimum sharply reduces invisible
    // edge-cell overfetch at the handoff. The viewport still needs only a few
    // requests, while Deck uploads a much smaller first res7 generation.
    const deliveryZoom = Math.min(tileRange.max, tileRange.min + 2);
    return {
      key: `${key}:z${deliveryZoom}`,
      minZoom: deliveryZoom,
      maxZoom: deliveryZoom,
      load: async (index: FineTileIndex) => {
        fineRequestCount += 1;
        const url =
          `${config.apiBaseUrl}/api/tiles/res7/${index.z}/${index.x}/${index.y}` +
          `?system=${system}&v=${dynamicTileVersion}${jurisdictionQuery}`;
        const response = await fetch(url);
        if (!response.ok) {
          throw new Error(`Unable to load resolution-7 tile (${response.status})`);
        }
        return response.json() as Promise<CompactTile>;
      }
    };
  }

  function fineViewport(camera: MapCamera, useMapFootprint = true) {
    // A flat map has an exact rectangular web-tile footprint. The polygon
    // scan is reserved for genuinely pitched views; using it at pitch zero
    // can include many rows that only touch the rectangle's boundary.
    const useProjectedFootprint = useMapFootprint && livePitch > 0.5;
    const geographicBounds = useProjectedFootprint ? map?.getBounds() : undefined;
    const width = container?.clientWidth ?? 0;
    const height = container?.clientHeight ?? 0;
    const corners =
      useProjectedFootprint && map && width > 0 && height > 0
        ? [
            [0, 0],
            [width, 0],
            [width, height],
            [0, height]
          ].map(([x, y]) => {
            const point = map?.unproject([x, y]);
            return {
              longitude: point?.lng ?? camera.longitude,
              latitude: point?.lat ?? camera.latitude
            };
          })
        : undefined;
    return {
      ...camera,
      width,
      height,
      corners,
      footprintZoom: useProjectedFootprint ? liveZoom : camera.zoom,
      bounds: geographicBounds
        ? {
            west: geographicBounds.getWest(),
            south: geographicBounds.getSouth(),
            east: geographicBounds.getEast(),
            north: geographicBounds.getNorth()
          }
        : undefined
    };
  }

  function scheduleFineTiles(camera: MapCamera = liveCamera, useMapFootprint = true) {
    if (!dynamicResolution7 || camera.zoom < RESOLUTION_PRELOAD_ZOOM) return;
    const source = fineTileSource();
    const viewport = fineViewport(camera, useMapFootprint);
    const coverageReady = hasRenderedFineCoverage(viewport, source);
    fineCoverageReady = coverageReady;
    fineTileCoordinator?.schedule(viewport, source);
    return coverageReady;
  }

  function syncDisplayedResolution() {
    const requested = resolutionForZoom(liveZoom);
    if (dynamicResolution7 && requested === 7) {
      fineCoverageReady = hasRenderedFineCoverage(fineViewport(liveCamera), fineTileSource());
    }
    // The displayed resolution follows rendered coverage, not the zoom intent.
    // Keep the complete global layer until every visible fine tile is warm.
    const next =
      requested === 7 &&
      !(dynamicResolution7
        ? fineCoverageReady || (currentResolution === 7 && hasCurrentFineCommit())
        : fineViewportReady) &&
      availableResolutions.includes(3)
        ? 3
        : requested;
    if (next === currentResolution) return false;
    currentResolution = next;
    resolution.set(next);
    useSystemDomain(currentSystem);
    return true;
  }

  function hasRenderedFineCoverage(
    viewport: ReturnType<typeof fineViewport>,
    source: FineTileSource
  ): boolean {
    return (
      committedFineSource === source.key &&
      fineViewportReady &&
      fineTileIndices(viewport, source.minZoom, source.maxZoom, 0).every(({ z, x, y }) =>
        displayedFineTileKeys.has(`${z}/${x}/${y}`)
      )
    );
  }

  function selectFeature(object: any) {
    const h3Index = Array.isArray(object) ? object[0] : object?.properties?.h3_index;
    if (!h3Index) return;
    const selectedResolution = Array.isArray(object) ? 7 : Number(object.properties.resolution);
    if (selectedResolution === 3 || selectedResolution === 7) {
      resolution.set(selectedResolution);
      selectedH3Resolution.set(selectedResolution);
    }
    selectedH3.set(h3Index);
  }

  function selectCoarseFeature(index: number) {
    const h3Index = coarseSnapshot?.h3Indexes[index];
    if (!h3Index || !coarseIncluded(index)) return;
    resolution.set(3);
    selectedH3Resolution.set(3);
    selectedH3.set(h3Index);
  }

  function cellSearchUnavailability(selection: CellSearchSelection): string | null {
    if (!availableResolutions.includes(selection.resolution)) {
      return `Resolution ${selection.resolution} data is not loaded for this map`;
    }
    if (!coarseSnapshot) return null;
    let anchor: string | null = selection.h3Index;
    if (selection.resolution === 7) {
      try {
        anchor = cellToParent(selection.h3Index, 3);
      } catch {
        anchor = null;
      }
    }
    if (!anchor) return 'Not in the mapped dataset';
    const row = coarseSnapshot.h3Indexes.indexOf(anchor);
    if (row === -1) return 'Not in the mapped dataset';
    if (!coarseIncluded(row)) return 'Not in the selected jurisdiction set';
    return null;
  }

  function handleCellSearch(selection: CellSearchSelection): string | null {
    if (!map || !basemapReady) return 'The map is still loading';
    const unavailable = cellSearchUnavailability(selection);
    if (unavailable) return unavailable;
    resolution.set(selection.resolution);
    selectedH3Resolution.set(selection.resolution);
    selectedH3.set(selection.h3Index);
    map.flyTo({
      center: [selection.lng, selection.lat],
      zoom: selection.resolution === 3 ? SEARCH_COARSE_ZOOM : SEARCH_FINE_ZOOM,
      duration: SEARCH_FLIGHT_MS,
      essential: true
    });
    return null;
  }

  function selectedForResolution(cellResolution: 3 | 7): string | null {
    if (!currentSelected || !currentSelectedResolution) return null;
    if (currentSelectedResolution === cellResolution) return currentSelected;
    if (currentSelectedResolution === 7 && cellResolution === 3) {
      return cellToParent(currentSelected, 3);
    }
    return null;
  }

  function coarseResolution3Layer(selected: string | null) {
    if (!coarseSnapshot) return null;
    // A hidden layer keeps its own colour range and GPU colour buffer. Crossing
    // the res7 threshold must not recolour the entire global res3 snapshot.
    const domain = layerScoreDomain(3);
    const isCoarseFallback = currentResolution === 7 && !hasFineDisplayCommit();
    return new H3HexagonLayer<number>({
      id: 'priorities-res3-global',
      data: coarseSnapshot.indices,
      visible: currentResolution === 3 || isCoarseFallback,
      getHexagon: (index) => coarseSnapshot?.h3Indexes[index] ?? '',
      // Exact polygons avoid MVT tile edges and shared-mesh distortion at
      // the antimeridian.
      highPrecision: true,
      wrapLongitude: false,
      coverage: 1,
      filled: true,
      stroked: false,
      extruded: true,
      getElevation: COARSE_CELL_ELEVATION,
      pickable: true,
      autoHighlight: Object.keys(currentBoundaryFilters).length === 0,
      highlightColor: [255, 255, 255, cellAlpha],
      getFillColor: (index) =>
        coarseSnapshot?.h3Indexes[index] === selected
          ? [255, 255, 255, Math.min(255, Math.round(cellAlpha * 2.2))]
          : coarseFillColor(index, domain),
      updateTriggers: {
        getFillColor: [
          domain.min,
          domain.max,
          currentSystem,
          currentNormalizeBySpecies,
          currentColourPalette,
          cellAlpha,
          currentColourScale,
          classificationRevision,
          selected,
          speciesHighlightRevision,
          JSON.stringify(currentWeights),
          JSON.stringify(currentBoundaryFilters)
        ]
      },
      onClick: (info: any) => {
        const picked = resolveMapPick(info, coarseSnapshot?.length ?? 0);
        if (picked?.kind === 'coarse') selectCoarseFeature(picked.index);
      }
    });
  }

  function dynamicResolution7Layer(selected: string | null, chunk: ResidentFineTileChunk) {
    const domain = layerScoreDomain(7);
    const visible =
      currentResolution === 7 &&
      hasFineDisplayCommit() &&
      chunk.sourceKey === committedFineSource &&
      displayedFineTileKeys.has(chunk.key);
    return new StableH3HexagonLayer<CompactCell>({
      id: `priorities-res7-dynamic-${encodeURIComponent(chunk.sourceKey)}-${chunk.key.replaceAll('/', '-')}`,
      data: chunk.cells,
      visible,
      pickable: visible,
      autoHighlight: true,
      highlightColor: [255, 255, 255, cellAlpha] as [number, number, number, number],
      filled: true,
      stroked: false,
      extruded: true,
      getElevation: FINE_CELL_ELEVATION,
      getFillColor: (cell: CompactCell) =>
        cell[0] === selected
          ? ([255, 255, 255, Math.min(255, Math.round(cellAlpha * 2.2))] as [
              number,
              number,
              number,
              number
            ])
          : compactFillColor(cell, domain),
      updateTriggers: {
        getFillColor: [
          domain.min,
          domain.max,
          currentSystem,
          currentNormalizeBySpecies,
          currentColourPalette,
          cellAlpha,
          currentColourScale,
          classificationRevision,
          selected,
          speciesHighlightRevision,
          JSON.stringify(currentWeights)
        ]
      },
      onClick: ({ object }: any) => selectFeature(object),
      getHexagon: (cell) => cell[0],
      highPrecision: false,
      centerHexagon: chunk.cells[0][0],
      // The coordinator fetches canonical x=0 and x=max tiles together at
      // the dateline. Wrap point instances so cells stored at -179.x are
      // projected into the visible +180.x world copy (and vice versa).
      wrapLongitude: true,
      coverage: 1
    });
  }

  function basemapStyle(): StyleSpecification {
    return {
      version: 8,
      sources: {
        countries: {
          type: 'geojson',
          data: staticBasemap as GeoJSON.FeatureCollection
        },
        carto: {
          type: 'raster',
          tiles: [basemapTileUrl],
          tileSize: 256,
          minzoom: 0,
          maxzoom: 20,
          attribution: '&copy; OpenStreetMap contributors &copy; CARTO'
        }
      },
      layers: [
        {
          id: 'ocean-background',
          type: 'background',
          paint: { 'background-color': '#dce4e9' }
        },
        {
          id: 'country-fill',
          type: 'fill',
          source: 'countries',
          paint: { 'fill-color': '#f1f0eb', 'fill-opacity': 1 }
        },
        {
          id: 'country-boundaries',
          type: 'line',
          source: 'countries',
          paint: {
            'line-color': '#b8bdba',
            'line-opacity': 0.72,
            'line-width': ['interpolate', ['linear'], ['zoom'], 0, 0.35, 8, 0.8]
          }
        },
        {
          id: 'carto-light',
          type: 'raster',
          source: 'carto',
          paint: {
            'raster-opacity': 1,
            'raster-fade-duration': 220,
            'raster-resampling': 'linear'
          }
        }
      ]
    };
  }

  function layers() {
    const priorities = availableResolutions
      .filter((cellResolution) => cellResolution !== 3 || !coarseSnapshot)
      .filter((cellResolution) => cellResolution === 3 || !dynamicResolution7)
      .map((cellResolution) =>
        priorityLayer(cellResolution, selectedForResolution(cellResolution))
      );
    const coarse = coarseResolution3Layer(selectedForResolution(3));
    if (coarse) priorities.unshift(coarse as any);
    if (dynamicResolution7 && availableResolutions.includes(7)) {
      if (fineWarmupChunk) priorities.push(dynamicResolution7Layer(null, fineWarmupChunk) as any);
      priorities.push(
        ...(fineTileRenderQueue?.chunks ?? [])
          .filter((chunk) => chunk.cells.length > 0)
          .map((chunk) => dynamicResolution7Layer(selectedForResolution(7), chunk) as any)
      );
    }
    return priorities;
  }

  function refresh() {
    if (deck) deck.setProps({ layers: layers() });
  }

  function resolutionForZoom(zoom: number): 3 | 7 {
    if (zoom > RESOLUTION_SWITCH_ZOOM && availableResolutions.includes(7)) return 7;
    if (availableResolutions.includes(3)) return 3;
    return 7;
  }

  function cacheSpeciesDistribution(
    key: string,
    distribution: { resolution: 3 | 7; cells: readonly string[] }
  ) {
    speciesDistributionCache.delete(key);
    speciesDistributionCache.set(key, distribution);
    if (speciesDistributionCache.size > SPECIES_DISTRIBUTION_CACHE_SIZE) {
      const oldest = speciesDistributionCache.keys().next().value;
      if (oldest !== undefined) speciesDistributionCache.delete(oldest);
    }
  }

  function applySpeciesDistribution(distribution: { resolution: 3 | 7; cells: readonly string[] }) {
    speciesHighlightResolution = distribution.resolution;
    matchingSpeciesCells = new Set(distribution.cells);
    speciesHighlightReady = true;
    speciesHighlightRevision += 1;
    speciesHighlight.set({
      status: 'ready',
      count: distribution.cells.length,
      resolution: distribution.resolution
    });
    refresh();
  }

  async function loadSpeciesDistribution(species: SelectedSpecies | null) {
    const requestId = ++speciesRequestId;
    speciesAbortController?.abort();
    speciesAbortController = null;
    matchingSpeciesCells = new Set();
    speciesHighlightReady = false;
    speciesHighlightRevision += 1;
    if (!species) {
      speciesHighlight.set({ status: 'idle', count: 0, resolution: 3 });
      refresh();
      return;
    }
    speciesHighlight.set({ status: 'loading', count: 0, resolution: 3 });
    const cacheKey = `${species.gbif_accepted_id}:${currentResolution}`;
    const cached = speciesDistributionCache.get(cacheKey);
    if (cached) {
      speciesDistributionCache.delete(cacheKey);
      speciesDistributionCache.set(cacheKey, cached);
      applySpeciesDistribution(cached);
      return;
    }
    const controller = new AbortController();
    speciesAbortController = controller;
    try {
      const response = await api.speciesCells(
        species.gbif_accepted_id,
        currentResolution,
        controller.signal
      );
      if (requestId !== speciesRequestId) return;
      const distribution = {
        resolution: response.resolution as 3 | 7,
        cells: response.cells ?? []
      };
      cacheSpeciesDistribution(cacheKey, distribution);
      applySpeciesDistribution(distribution);
    } catch (reason) {
      if (requestId !== speciesRequestId) return;
      if (reason instanceof DOMException && reason.name === 'AbortError') return;
      speciesHighlight.set({
        status: 'error',
        count: 0,
        resolution: 3,
        message: reason instanceof Error ? reason.message : 'Distribution unavailable'
      });
      refresh();
    } finally {
      if (speciesAbortController === controller) speciesAbortController = null;
    }
  }

  function keydown(event: KeyboardEvent) {
    if (ignoresWindowShortcut(event)) return;
    if (event.key.toLowerCase() === 'p' && performanceEnabled) {
      event.preventDefault();
      if (performanceTraceActive) return;
      if (performanceSessionActive) finishPerformanceMeasurement();
      else startPerformanceMeasurement('session');
      return;
    }
    if (event.key.toLowerCase() === 'f') {
      if (document.fullscreenElement) document.exitFullscreen();
      else fsWrap?.requestFullscreen();
      return;
    }
    const systems = { '1': '', '2': 'Terrestrial', '3': 'Freshwater', '4': 'Marine' } as const;
    const system = systems[event.key as keyof typeof systems];
    if (system !== undefined) habitatSystem.set(system);
  }

  onMount(() => {
    let disposed = false;
    let resizeObserver: ResizeObserver | undefined;
    let resizeFrame: number | undefined;
    async function initialize() {
      const [frameworks, bundle] = await Promise.all([
        loadBoundaryFrameworks().catch((reason) => {
          console.error(reason);
          return [];
        }),
        preloadMapBundle()
      ]);
      await Promise.all(
        frameworks
          .filter(
            (framework) =>
              framework.status === 'ready' && Boolean(framework.catalog_url ?? framework.data_url)
          )
          .map((framework) =>
            loadBoundaryCollection(framework.id).catch((reason) => console.error(reason))
          )
      );
      const metadata: MapMetadata = bundle.metadata;
      coarseSnapshot = bundle.coarseSnapshot;
      staticBasemap = bundle.staticBasemap;
      if (!metadata.score_domains || !metadata.species_normalized_score_domains) {
        throw new Error('Map metadata is out of date; rebuild the map artifacts');
      }
      scoreDomains = metadata.score_domains;
      normalizedScoreDomains = metadata.species_normalized_score_domains;
      boundaryScoreDomains = metadata.boundary_score_domains ?? {
        admin0: metadata.jurisdiction_score_domains ?? {}
      };
      normalizedBoundaryScoreDomains = metadata.boundary_species_normalized_score_domains ?? {};
      boundaryTileProperties = metadata.boundary_tile_properties ?? boundaryTileProperties;
      if (metadata.available_resolutions?.length) {
        availableResolutions = metadata.available_resolutions;
      }
      for (const cellResolution of availableResolutions) {
        const range = metadata.resolution_tile_ranges?.[String(cellResolution)];
        if (
          range &&
          Number.isFinite(range.min) &&
          Number.isFinite(range.max) &&
          range.max >= range.min
        ) {
          resolutionTileRanges[cellResolution] = range;
        }
      }
      if (metadata.tile_layout !== 'wide-v2-joint-priority') {
        throw new Error('Map tiles are out of date; rebuild the map artifacts');
      }
      if (
        metadata.boundary_assignment !== 'cell-intersection' &&
        metadata.jurisdiction_assignment !== 'cell-intersection'
      ) {
        throw new Error('Map tiles do not contain boundary assignments; rebuild the map artifacts');
      }
      partialResolution7 =
        availableResolutions.includes(7) && !(metadata.complete_resolutions ?? []).includes(7);
      dynamicResolution7 = metadata.res7_delivery === 'dynamic-h3-v1';
      dynamicTileVersion = metadata.res7_coverage_version ?? metadata.tile_schema_version ?? 0;
      currentResolution = availableResolutions.includes(3) ? 3 : availableResolutions[0];
      resolution.set(currentResolution);
      useSystemDomain(currentSystem);
      if (disposed) return;

      const tileUrl = metadata.tile_schema_version
        ? `${config.tileUrl}${config.tileUrl.includes('?') ? '&' : '?'}v=${metadata.tile_schema_version}`
        : config.tileUrl;
      archive = new PMTiles(tileUrl);
      if (dynamicResolution7 && coarseSnapshot?.h3Indexes.length) {
        // Compile the fine-cell shader alongside initial map setup rather
        // than on the first fast zoom. This hidden singleton is never picked.
        fineWarmupChunk = {
          sourceKey: 'gpu-warmup',
          key: 'gpu-warmup',
          cells: [[cellToCenterChild(coarseSnapshot.h3Indexes[0], 7), 0]]
        };
      }
      fineTileRenderQueue = new FineTileRenderQueue({
        onStage: () => {
          fineBuffersLoading = true;
          refresh();
        },
        onPublish: ({ chunks, cellCount, sourceKey, tileCount, tileKeys }) => {
          committedFineChunks = chunks;
          displayedFineTileKeys = new Set(tileKeys);
          committedFineCellCount = cellCount;
          committedFineTileCount = tileCount;
          committedFineSource = sourceKey;
          fineViewportReady = true;
          fineBuffersLoading = fineTileRenderQueue?.busy ?? false;
          // Coverage is valid only for the current camera and source, which
          // may have moved while this complete generation warmed on the GPU.
          syncDisplayedResolution();
          useSystemDomain(currentSystem);
          refresh();
        }
      });
      fineTileCoordinator = new FineTileCoordinator({
        // Render two z10 tiles beyond the complete viewport so consecutive
        // drag gestures cannot outrun a single narrow ring. The coordinator
        // publishes this buffer only after it is complete, without delaying
        // the visible commit.
        guardTiles: 2,
        debounceMs: 90,
        maxCachedTiles: 256,
        onCommit: (commit) => {
          fineBuffersLoading = true;
          fineTileRenderQueue?.submit(commit);
        },
        onLoadingChange: (loading) => {
          fineTilesLoading = loading;
        },
        onError: handleFineTileError
      });
      map = new MapLibreMap({
        container,
        style: basemapStyle(),
        center: [0, 0],
        zoom: 0,
        pitch: 0,
        bounds: [
          [-180, -60],
          [180, 80]
        ],
        fitBoundsOptions: { padding: 12 },
        pixelRatio: RENDER_PIXEL_RATIO,
        minZoom: -2,
        maxZoom: 19,
        renderWorldCopies: true,
        // CARTO parents remain in place until their children have faded in.
        // A generous cache makes quick zoom reversals reuse decoded imagery.
        fadeDuration: 220,
        maxTileCacheZoomLevels: 12,
        refreshExpiredTiles: false,
        attributionControl: false
      });
      // A sidebar changes the container without firing a window resize event.
      resizeObserver = new ResizeObserver(() => {
        if (resizeFrame !== undefined) cancelAnimationFrame(resizeFrame);
        resizeFrame = requestAnimationFrame(() => {
          resizeFrame = undefined;
          if (!disposed) map?.resize();
        });
      });
      resizeObserver.observe(container);
      map.scrollZoom.setZoomRate(1 / 125);
      map.scrollZoom.setWheelZoomRate(1 / 600);
      deck = new MapboxOverlay({
        // MapLibre owns raster/world-copy rendering. A separate transparent
        // Deck canvas avoids custom-layer API coupling while preserving the
        // same synchronized camera and native map controls.
        interleaved: false,
        // Let mouse events pass through the overlay to MapLibre's canvas.
        // MapboxOverlay forwards MapLibre's mousedown/drag events to deck for
        // picking, so deck does not need direct pointer hit-testing.
        style: { pointerEvents: 'none' } as any,
        touchAction: 'auto' as any,
        useDevicePixels: RENDER_PIXEL_RATIO,
        layers: layers(),
        layerFilter: ({ layer, isPicking }) => {
          if (
            performanceTraceRequested &&
            (performanceRenderer === 'basemap-only' || performanceRenderer === 'empty')
          ) {
            return false;
          }
          if (isPicking && isInteracting) return false;
          if (!dynamicResolution7 && layer.id.startsWith('priorities-res7')) {
            return currentResolution === 7 && fineViewportReady;
          }
          return true;
        },
        getTooltip: (info: any) => {
          const picked = resolveMapPick(info, coarseSnapshot?.length ?? 0);
          if (!picked) return null;
          const coarseIndex = picked.kind === 'coarse' ? picked.index : null;
          const object = picked.kind === 'object' ? (picked.object as any) : null;
          const compact = Array.isArray(object) ? (object as CompactCell) : null;
          if (coarseIndex !== null && !coarseIncluded(coarseIndex)) return null;
          if (
            coarseIndex === null &&
            !compact &&
            (!object.properties || !isIncluded(object.properties))
          )
            return null;
          const values =
            coarseIndex !== null
              ? coarseMetrics(coarseIndex)
              : compact
                ? compactMetrics(compact)
                : metrics(object.properties);
          const objectBoundaryNames =
            coarseIndex !== null
              ? Object.keys(boundaryTileProperties).flatMap((framework) =>
                  (coarseSnapshot?.boundaryCodes(framework, coarseIndex) ?? []).map((code) =>
                    boundaryName(framework, code)
                  )
                )
              : compact
                ? []
                : Object.keys(boundaryTileProperties)
                    .flatMap((framework) =>
                      boundaryCodes(object.properties, framework).map((code) =>
                        boundaryName(framework, code)
                      )
                    )
                    .filter(Boolean);
          const visibleBoundaryNames = objectBoundaryNames.slice(0, 3).map(escapeTooltipText);
          const hiddenBoundaryCount = Math.max(
            0,
            objectBoundaryNames.length - visibleBoundaryNames.length
          );
          const h3Index = escapeTooltipText(
            coarseIndex !== null
              ? coarseSnapshot?.h3Indexes[coarseIndex]
              : compact
                ? compact[0]
                : object.properties.h3_index
          );
          const displayedScore =
            coarseIndex !== null
              ? coarseColourScore(coarseIndex)
              : compact
                ? compactColourScore(compact)
                : colourScore(object.properties);
          const rawScore =
            coarseIndex !== null
              ? coarseScore(coarseIndex)
              : compact
                ? compactScore(compact)
                : score(object.properties);
          const caveatedObjectResolution = Number(object?.properties?.resolution);
          const caveatedResolution =
            coarseIndex !== null
              ? 3
              : compact
                ? 7
                : Number.isFinite(caveatedObjectResolution)
                  ? caveatedObjectResolution
                  : currentResolution;
          return {
            html:
              `<div style="display:flex;align-items:baseline;justify-content:space-between;gap:.65rem;">` +
              `<strong style="color:var(--color-black);font-size:.72rem;">${currentNormalizeBySpecies ? 'Score per species' : 'Score'} ${displayedScore.toFixed(2)}</strong>` +
              `<code style="color:var(--color-gray-500);font-size:.56rem;">${h3Index}</code></div>` +
              (visibleBoundaryNames.length
                ? `<div style="margin-top:.2rem;color:var(--color-gray-600);font-size:.6rem;line-height:.82rem;">${visibleBoundaryNames.join(' · ')}${hiddenBoundaryCount ? ` · +${hiddenBoundaryCount}` : ''}</div>`
                : '') +
              `<div style="margin-top:.32rem;padding-top:.28rem;border-top:1px solid rgba(100,100,100,.16);color:var(--color-gray-600);font-size:.61rem;line-height:.88rem;">` +
              `<span style="color:var(--color-gray-500);">Threat</span> · CR ${values.cr} · EN ${values.en} · VU ${values.vu} · NT ${values.nt} · DD ${values.dd} · LC ${values.lc}<br>` +
              `<span style="color:var(--color-gray-500);">DNA gaps</span> · GoaT deficient ${values.gdd} · species ${values.ms} · genus ${values.mg} · family ${values.mf}` +
              (currentNormalizeBySpecies
                ? `<br><span style="color:var(--color-gray-500);">Raw score</span> · ${rawScore.toFixed(2)} · ${values.total} species`
                : '') +
              `<br><span style="color:var(--color-gray-500);">Knowledge limits</span> · ${knowledgeLimitsText(values)}` +
              `<br><em style="color:var(--color-gray-500);font-size:.56rem;">${occupancyCaveatText(caveatedResolution)}</em></div>`,
            style: {
              minWidth: '176px',
              maxWidth: '240px',
              fontFamily: 'var(--font-sans)',
              backgroundColor: 'rgba(252,251,247,.97)',
              color: 'var(--color-black)',
              padding: '.48rem .62rem',
              borderRadius: 'var(--border-radius-base)',
              fontSize: '.68rem',
              lineHeight: '1rem',
              boxShadow: 'var(--box-shadow-sm)',
              border: '1px solid var(--color-tinted-cream)'
            }
          };
        }
      });
      map.addControl(deck);
      // Ensure the overlay canvas does not intercept pointer events after async
      // Deck/Hammer initialization (Hammer sets touch-action, etc. post-creation).
      const ensureOverlayNonInteractive = () => {
        const curDeck = deck;
        const c = curDeck?.getCanvas() as HTMLCanvasElement | null;
        if (c) {
          if (c.style.pointerEvents !== 'none') c.style.pointerEvents = 'none';
          (c.style as any).touchAction = 'auto';
        } else if (curDeck) {
          requestAnimationFrame(ensureOverlayNonInteractive);
        }
      };
      requestAnimationFrame(ensureOverlayNonInteractive);
      map.addControl(
        new FullscreenControl({
          container: fsWrap
        }),
        'top-right'
      );
      map.addControl(
        new NavigationControl({
          showCompass: true,
          showZoom: true,
          visualizePitch: true
        }),
        'top-right'
      );

      const performanceParameters = new URLSearchParams(window.location.search);
      performanceEnabled = performanceParameters.get('mapPerf') === '1';
      performanceTraceRequested =
        performanceEnabled && performanceParameters.get('mapPerfTrace') === '1';
      const requestedRenderer = performanceParameters.get('mapPerfRenderer');
      performanceRenderer =
        requestedRenderer === 'basemap-only' ||
        requestedRenderer === 'cells-only' ||
        requestedRenderer === 'empty'
          ? requestedRenderer
          : 'full';
      if (
        performanceTraceRequested &&
        (performanceRenderer === 'cells-only' || performanceRenderer === 'empty')
      ) {
        const hideBasemapLayers = () => {
          for (const layer of map?.getStyle().layers ?? []) {
            if (map?.getLayer(layer.id)) map.setLayoutProperty(layer.id, 'visibility', 'none');
          }
        };
        if (map.isStyleLoaded()) hideBasemapLayers();
        else map.once('styledata', hideBasemapLayers);
      }
      const requestedTraceRuns = Number(performanceParameters.get('mapPerfRuns') ?? '3');
      performanceTraceRunCount = Number.isFinite(requestedTraceRuns)
        ? Math.min(10, Math.max(1, Math.floor(requestedTraceRuns)))
        : 3;
      if (performanceEnabled) {
        const { MapPerformanceMonitor: MapPerformanceMonitorClass } =
          await import('$lib/map/mapPerformance');
        performanceStatus = 'idle';
        performanceMonitor = new MapPerformanceMonitorClass({
          sample: () => ({
            longitude: liveCamera.longitude,
            latitude: liveCamera.latitude,
            zoom: liveZoom,
            resolution: currentResolution,
            fineCoverageReady,
            fineTilesLoading: fineTilesLoading || fineBuffersLoading,
            fineTileCount: committedFineTileCount,
            fineCellCount: committedFineCellCount,
            res7RequestCount: fineRequestCount,
            basemapPixelRatio,
            cellPixelRatio
          })
        });
        performanceTraceStatus = performanceTraceRequested ? 'idle' : 'disabled';
      }

      const syncMapCamera = (scheduleSettledViewport = false) => {
        if (!map) return;
        const center = map.getCenter();
        liveZoom = map.getZoom();
        livePitch = map.getPitch();
        liveCamera = {
          longitude: center.lng,
          latitude: center.lat,
          zoom: liveZoom
        };
        if (scheduleSettledViewport) {
          currentZoom = liveZoom;
          currentPitch = livePitch;
          currentCamera = liveCamera;
        }
        const next = resolutionForZoom(liveZoom);
        if (
          dynamicResolution7 &&
          currentResolution === 3 &&
          next === 3 &&
          liveZoom >= RESOLUTION_PRELOAD_ZOOM
        ) {
          // Warm the viewport the camera will occupy at the first fine zoom,
          // not the much larger current coarse footprint. The result is a
          // small, reusable generation that normally exists before crossing.
          const now = performance.now();
          if (scheduleSettledViewport || now - lastFineForecastAt >= FINE_FORECAST_INTERVAL_MS) {
            lastFineForecastAt = now;
            // The default view is flat, so forecast directly at the target
            // zoom. Reusing the current zoom's renderer bounds overestimates
            // the future footprint and uploads several invisible tile rows.
            scheduleFineTiles({ ...liveCamera, zoom: INITIAL_FINE_ZOOM }, false);
          }
        }
        // Fine data follows the camera; it never controls the camera. During
        // interaction the coordinator is throttled, and an atomic commit
        // replaces the previous generation when the visible tiles are ready.
        if (dynamicResolution7 && next === 7) {
          const now = performance.now();
          if (
            scheduleSettledViewport ||
            now - lastFineActiveScheduleAt >= FINE_ACTIVE_INTERVAL_MS
          ) {
            lastFineActiveScheduleAt = now;
            // Once fine cells are visible, load the actual viewport. A
            // narrower forecast leaves holes near the resolution boundary.
            scheduleFineTiles(liveCamera);
          }
        }
        if (syncDisplayedResolution()) {
          refresh();
        } else if (scheduleSettledViewport && usesStaticScoreCollection()) {
          useSystemDomain(currentSystem);
          refresh();
        }
      };
      map.on('movestart', () => {
        isInteracting = true;
        if (!performanceSessionActive && !performanceTraceActive) {
          startPerformanceMeasurement('gesture');
        }
      });
      map.on('move', () => syncMapCamera(false));
      map.on('moveend', () => {
        isInteracting = false;
        syncMapCamera(true);
        if (!performanceSessionActive && !performanceTraceActive) {
          finishPerformanceMeasurement();
        }
      });

      const startAutomaticPerformanceTrace = async () => {
        if (!performanceTraceRequested || !performanceMonitor || !map || disposed) return;
        performanceTraceActive = true;
        performanceTraceStatus = 'running';
        performanceTraceError = '';
        performanceTraceResult = null;
        try {
          const { runZoom10MapPerformanceTrace } = await import('$lib/map/mapPerformanceTrace');
          if (disposed || !map || !performanceMonitor) return;
          if (!dynamicResolution7 || !availableResolutions.includes(7)) {
            throw new Error('The performance trace requires dynamic res7 delivery');
          }
          performanceTraceResult = await runZoom10MapPerformanceTrace({
            map,
            monitor: performanceMonitor,
            runCount: performanceTraceRunCount,
            scenario: performanceParameters.get('mapPerfScenario'),
            locationOffset: Math.max(
              0,
              Math.floor(Number(performanceParameters.get('mapPerfLocationOffset')) || 0)
            ),
            isFineGuardReady: () =>
              fineCoverageReady &&
              !fineTilesLoading &&
              !fineTileRenderQueue?.busy &&
              committedFineTileCount > 0,
            isDisposed: () => disposed,
            onRunStart: (runNumber) => {
              performanceTraceCurrentRun = runNumber;
              performanceResult = null;
              performanceMode = 'session';
              performanceSessionActive = true;
              performanceStatus = 'recording';
            },
            onRunComplete: (result) => {
              performanceResult = result;
              performanceSessionActive = false;
              performanceStatus = 'complete';
            }
          });
          if (!disposed) performanceTraceStatus = 'complete';
        } catch (reason) {
          if (!disposed) {
            performanceTraceStatus = 'error';
            performanceTraceError =
              reason instanceof Error ? reason.message : 'Unable to run the map performance trace';
          }
        } finally {
          performanceTraceActive = false;
          performanceSessionActive = false;
          if (!disposed && performanceStatus === 'recording') performanceStatus = 'idle';
        }
      };
      map.once('idle', () => {
        basemapReady = true;
        // Capture the real initial camera. Fine data deliberately waits
        // until the preload approach, where the predicted fine viewport is
        // much smaller.
        syncMapCamera(true);
        if (performanceTraceRequested) void startAutomaticPerformanceTrace();
      });
      map.on('error', (event) => {
        console.error('Unable to render the basemap', event.error);
      });

      let subscriptionsReady = false;
      subscriptions = [
        weights.subscribe((value) => {
          currentWeights = value;
          updateScoreModel();
          useSystemDomain(currentSystem);
          if (subscriptionsReady) refresh();
        }),
        habitatSystem.subscribe((system) => {
          const sourceChanged = system !== currentSystem;
          currentSystem = system;
          updateScoreModel();
          useSystemDomain(system);
          if (subscriptionsReady) {
            if (dynamicResolution7 && sourceChanged) fineCoverageReady = false;
            scheduleFineTiles();
            syncDisplayedResolution();
            refresh();
          }
        }),
        normalizeColoursBySpecies.subscribe((normalize) => {
          currentNormalizeBySpecies = normalize;
          useSystemDomain(currentSystem);
          if (subscriptionsReady) refresh();
        }),
        colourPalette.subscribe((palette: ColourPaletteId) => {
          currentColourPalette = palette;
          if (subscriptionsReady) refresh();
        }),
        colourScale.subscribe((settings) => {
          currentColourScale = settings;
          updateClassification();
          if (subscriptionsReady) refresh();
        }),
        cellTransparency.subscribe((transparency) => {
          currentCellTransparency = Math.min(100, Math.max(0, transparency));
          cellAlpha = Math.round(255 * (1 - currentCellTransparency / 100));
          if (subscriptionsReady) refresh();
        }),
        selectedH3.subscribe((selected) => {
          currentSelected = selected;
          if (subscriptionsReady) refresh();
        }),
        selectedH3Resolution.subscribe((selectedResolution) => {
          currentSelectedResolution = selectedResolution;
          if (subscriptionsReady) refresh();
        }),
        boundaryFilters.subscribe((selected) => {
          currentBoundaryFilters = selected;
          for (const frameworkId of Object.keys(selected)) {
            if (!currentBoundaryCollections[frameworkId]) {
              void loadBoundaryCollection(frameworkId).catch((reason) => console.error(reason));
            }
          }
          useSystemDomain(currentSystem);
          if (subscriptionsReady) {
            if (dynamicResolution7) fineCoverageReady = false;
            scheduleFineTiles();
            syncDisplayedResolution();
            refresh();
          }
        }),
        boundaryCollections.subscribe((collections) => {
          currentBoundaryCollections = collections;
          boundaryNames = Object.fromEntries(
            Object.entries(collections).map(([framework, collection]) => [
              framework,
              new Map(
                collection.features.map((feature) => [
                  feature.properties.code,
                  feature.properties.name
                ])
              )
            ])
          );
        }),
        selectedSpecies.subscribe((species) => {
          currentSpecies = species;
          if (subscriptionsReady) void loadSpeciesDistribution(species);
        }),
        speciesHighlightGradient.subscribe((enabled) => {
          currentSpeciesGradient = enabled;
          if (subscriptionsReady && speciesHighlightReady) {
            speciesHighlightRevision += 1;
            refresh();
          }
        })
      ];
      subscriptionsReady = true;
      if (currentSpecies) void loadSpeciesDistribution(currentSpecies);
    }

    void initialize().catch((reason) => {
      console.error(reason);
      mapError = reason instanceof Error ? reason.message : 'Unable to initialize the map';
    });
    return () => {
      disposed = true;
      resizeObserver?.disconnect();
      if (resizeFrame !== undefined) cancelAnimationFrame(resizeFrame);
      subscriptions.forEach((unsubscribe) => unsubscribe());
      classificationWorker?.terminate();
      classificationWorker = null;
      speciesRequestId += 1;
      speciesAbortController?.abort();
      speciesDistributionCache.clear();
      performanceMonitor?.dispose();
      performanceMonitor = null;
      fineTileCoordinator?.dispose();
      fineTileCoordinator = null;
      fineTileRenderQueue?.dispose();
      fineTileRenderQueue = null;
      if (staticDomainFrame !== null) cancelAnimationFrame(staticDomainFrame);
      staticDomainFrame = null;
      deck?.finalize();
      deck = null;
      map?.remove();
      map = null;
    };
  });
</script>

<svelte:window on:keydown={keydown} />

<div class="fs-wrap" bind:this={fsWrap}>
  <div id="map" class="map-shell">
    <div
      class="map-canvas"
      bind:this={container}
      aria-label="Interactive H3 conservation priority map"
      data-resolution={currentResolution}
      data-fine-ready={fineViewportReady}
      data-fine-coverage-ready={fineCoverageReady}
      data-fine-loading={fineTilesLoading}
      data-fine-buffers-loading={fineBuffersLoading}
      data-fine-cells={committedFineCellCount}
      data-fine-tiles={committedFineTileCount}
      data-coarse-ready={Boolean(coarseSnapshot)}
      data-basemap-ready={basemapReady}
      data-longitude={currentCamera.longitude.toFixed(3)}
      data-latitude={currentCamera.latitude.toFixed(3)}
      data-zoom={currentCamera.zoom.toFixed(2)}
      data-pitch={currentPitch.toFixed(1)}
      data-cells-extruded="true"
      data-basemap-pixel-ratio={basemapPixelRatio}
      data-cell-pixel-ratio={cellPixelRatio}
      data-cell-transparency={currentCellTransparency}
      data-domain-min={domainMin}
      data-domain-max={domainMax}
      data-basemap-retina-tiles={basemapTileUrl.includes('@2x')}
      data-perf-enabled={performanceEnabled}
      data-perf-status={performanceStatus}
      data-perf-mode={performanceMode}
      data-perf-result={performanceResult ? JSON.stringify(performanceResult) : ''}
      data-perf-frame-count={performanceResult?.frameCount ?? ''}
      data-perf-p95-frame-ms={performanceResult?.p95FrameMs ?? ''}
      data-perf-p99-frame-ms={performanceResult?.p99FrameMs ?? ''}
      data-perf-under-18ms-percent={performanceResult?.under18MsPercent ?? ''}
      data-perf-worst-frame-ms={performanceResult?.worstFrameMs ?? ''}
      data-perf-over-budget-frames={performanceResult?.overBudgetFrameCount ?? ''}
      data-perf-long-frames={performanceResult?.longFrameCount ?? ''}
      data-perf-long-tasks={performanceResult?.longTaskCount ?? ''}
      data-perf-uncovered-frames={performanceResult?.uncoveredFrameCount ?? ''}
      data-perf-resolution-flashes={performanceResult?.resolutionFlashFrameCount ?? ''}
      data-perf-res7-requests={performanceResult?.res7RequestCount ?? ''}
      data-perf-trace-requested={performanceTraceRequested}
      data-perf-trace-status={performanceTraceStatus}
      data-perf-trace-run-count={performanceTraceRunCount}
      data-perf-renderer={performanceRenderer}
      data-perf-trace-current-run={performanceTraceCurrentRun || ''}
      data-perf-trace-result={performanceTraceResult ? JSON.stringify(performanceTraceResult) : ''}
      data-perf-trace-first-p95={performanceTraceResult?.firstPathRun.p95FrameMs ?? ''}
      data-perf-trace-repeat-median-p95={performanceTraceResult?.repeatRuns?.medianP95FrameMs ?? ''}
      data-perf-trace-baseline-frame-ms={performanceTraceResult?.environment.baselineFrameMs ?? ''}
      data-perf-trace-supports-60fps={performanceTraceResult?.environment.supports60FpsBudget ?? ''}
      data-perf-trace-error={performanceTraceError}
    ></div>
    {#if !basemapReady}<div class="map-reveal-cover" aria-hidden="true"></div>{/if}
    {#if !mapError && !basemapReady}
      <div class="map-loading" role="status" aria-live="polite">
        <LoaderCircle size={15} class="map-loading-spinner" aria-hidden="true" />
        <span>Loading map</span>
      </div>
    {/if}
    {#if toolbar}<section class="map-toolbar" aria-label="Map toolbar">
        {@render toolbar()}
      </section>{/if}
    <SpeciesHighlight cellSearch={handleCellSearch} />
    <aside class="map-attribution" aria-label="Basemap attribution">
      <a href="https://carto.com/attributions" target="_blank" rel="noopener noreferrer">CARTO</a>
      ·
      <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer"
        >OpenStreetMap</a
      >
    </aside>
    {#if mapError}<p class="error-message">{mapError}</p>{/if}
  </div>
</div>

<style>
  .map-loading {
    position: absolute;
    bottom: 2.25rem;
    left: 1rem;
    z-index: 5;
    display: flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.45rem 0.65rem;
    border-radius: 0.5rem;
    background: rgba(252, 251, 247, 0.94);
    box-shadow: var(--box-shadow-sm);
    font-size: 0.72rem;
    pointer-events: none;
  }
  .map-loading :global(.map-loading-spinner) {
    animation: map-loading-spin 0.85s linear infinite;
  }
  @keyframes map-loading-spin {
    to {
      transform: rotate(360deg);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .map-loading :global(.map-loading-spinner) {
      animation: none;
    }
  }
  .map-toolbar {
    position: absolute;
    top: 1rem;
    left: 4.5rem;
    z-index: 4;
  }
  @media (max-width: 760px) {
    .map-toolbar {
      top: 0.65rem;
      left: 4rem;
    }
  }
</style>
