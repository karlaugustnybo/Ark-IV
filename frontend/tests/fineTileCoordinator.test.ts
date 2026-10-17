// Bun supplies this module at test runtime; the frontend production tsconfig
// intentionally does not include Bun's ambient server-side types.
// @ts-expect-error -- available to `bun test`, excluded from the browser bundle
import { describe, expect, test } from 'bun:test';
import {
  FineTileCoordinator,
  fineTileIndices,
  type FineTileIndex
} from '../src/lib/map/fineTileCoordinator';
import type { CompactTile } from '../src/lib/map/compactTiles';

const settle = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));
const tileKey = ({ z, x, y }: FineTileIndex) => `${z}/${x}/${y}`;

test('obsolete guards cannot evict the current viewport or valid empty tiles', async () => {
  const view = { longitude: 0, latitude: 0, zoom: 11, width: 1024, height: 640 };
  const pending = new Map<string, (empty?: boolean) => void>();
  const commits: Array<{ keys: string[]; chunks: number; cells: number }> = [];
  const source = {
    key: 'pressure',
    minZoom: 10,
    maxZoom: 10,
    load: (index: FineTileIndex) =>
      new Promise<CompactTile>((resolve) => {
        const key = tileKey(index);
        pending.set(key, (empty = false) => resolve({ cells: empty ? [] : [[key, 1]] }));
      })
  };
  const coordinator = new FineTileCoordinator({
    debounceMs: 0,
    onCommit: ({ tileKeys, chunks, cellCount }) =>
      commits.push({ keys: [...tileKeys], chunks: chunks.length, cells: cellCount })
  });
  const finish = (key: string, empty = false) => {
    pending.get(key)?.(empty);
    pending.delete(key);
  };
  for (let i = 0; i < 12; i += 1) {
    const moved = { ...view, longitude: i * 5 };
    coordinator.schedule(moved, source);
    await settle(3);
    for (const index of fineTileIndices(moved, 10, 10, 0)) finish(tileKey(index));
    await settle();
  }
  const latest = { ...view, longitude: 100 };
  coordinator.schedule(latest, source);
  await settle(3);
  const visible = fineTileIndices(latest, 10, 10, 0).map(tileKey);
  visible.slice(0, -1).forEach((key, i) => finish(key, i === 0));
  await settle();
  const obsolete = [...pending.keys()].filter((key) => !visible.includes(key));
  expect(obsolete.length).toBeGreaterThan(256);
  obsolete.forEach((key) => finish(key));
  await settle();
  finish(visible[visible.length - 1]);
  await settle();
  expect(commits.at(-1)).toEqual({
    keys: visible,
    chunks: visible.length,
    cells: visible.length - 1
  });
  expect(coordinator.hasCoverage(latest, source)).toBe(true);
  const commitCount = commits.length;
  coordinator.dispose();
  for (const finish of pending.values()) finish();
  await settle();
  expect(commits).toHaveLength(commitCount);
  expect(coordinator.hasCoverage(latest, source)).toBe(false);
});

test('repeated failure retries once and source replacement/disposal cancel delayed retries', async () => {
  for (const mode of ['failure', 'replacement', 'dispose'] as const) {
    let calls = 0;
    let errors = 0;
    const view = { ...camera, width: 1, height: 1, longitude: 10.1, latitude: 20.1, zoom: 11 };
    const source = {
      key: 'failed',
      minZoom: 10,
      maxZoom: 10,
      load: async () => {
        calls += 1;
        throw new Error('Synthetic failure');
      }
    };
    const coordinator = new FineTileCoordinator({
      guardTiles: 0,
      debounceMs: 0,
      onCommit: () => {},
      onError: () => {
        errors += 1;
      }
    });
    coordinator.schedule(view, source);
    await settle(10);
    const firstCalls = calls;
    if (mode === 'replacement')
      coordinator.schedule(view, { ...source, key: 'new', load: async () => ({ cells: [] }) });
    if (mode === 'dispose') coordinator.dispose();
    await settle(250);
    expect(calls).toBe(firstCalls * (mode === 'failure' ? 2 : 1));
    expect(errors).toBe(mode === 'failure' ? 1 : 0);
    await settle(220);
    expect(calls).toBe(firstCalls * (mode === 'failure' ? 2 : 1));
    coordinator.dispose();
  }
});

const camera = {
  longitude: 179.8,
  latitude: 0,
  zoom: 8,
  width: 1024,
  height: 640
};

describe('fineTileIndices', () => {
  test('canonicalizes and deduplicates tiles across the antimeridian', () => {
    const indices = fineTileIndices(camera, 9, 9, 3);
    const scale = 2 ** 9;
    expect(indices.length).toBeGreaterThan(0);
    expect(indices.every(({ x }) => x >= 0 && x < scale)).toBe(true);
    expect(new Set(indices.map(({ z, x, y }) => `${z}/${x}/${y}`)).size).toBe(indices.length);
    expect(indices.some(({ x }) => x === 0)).toBe(true);
    expect(indices.some(({ x }) => x === scale - 1)).toBe(true);
  });

  test('uses a fixed delivery zoom when the source range is fixed', () => {
    expect(fineTileIndices(camera, 9, 9).every(({ z }) => z === 9)).toBe(true);
    expect(fineTileIndices({ ...camera, zoom: 14 }, 9, 9).every(({ z }) => z === 9)).toBe(true);
  });

  test('uses renderer bounds for pitched and rotated viewports', () => {
    const flat = fineTileIndices(camera, 9, 9, 0);
    const pitched = fineTileIndices(
      {
        ...camera,
        corners: [
          { longitude: 175, latitude: 12 },
          { longitude: 184, latitude: 12 },
          { longitude: 182, latitude: -5 },
          { longitude: 177, latitude: -5 }
        ]
      },
      9,
      9,
      0
    );
    expect(pitched.length).toBeGreaterThan(flat.length);
    expect(pitched.some(({ x }) => x === 0)).toBe(true);
    expect(pitched.some(({ x }) => x === 511)).toBe(true);
  });
});

describe('FineTileCoordinator', () => {
  test('publishes only after the complete guarded generation resolves', async () => {
    const resolvers: Array<() => void> = [];
    const commits: number[] = [];
    const coordinator = new FineTileCoordinator({
      guardTiles: 0,
      debounceMs: 0,
      onCommit: ({ tileCount }) => commits.push(tileCount)
    });
    coordinator.schedule(
      { ...camera, width: 256, height: 256 },
      {
        key: 'test',
        minZoom: 9,
        maxZoom: 9,
        load: (index: FineTileIndex) =>
          new Promise((resolve) => {
            resolvers.push(() => resolve({ cells: [[`${index.z}-${index.x}-${index.y}`, 1]] }));
          })
      }
    );
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(resolvers.length).toBeGreaterThan(1);
    resolvers.slice(0, -1).forEach((resolve) => resolve());
    await Promise.resolve();
    expect(commits).toEqual([]);
    resolvers.at(-1)?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(commits).toEqual([resolvers.length]);
    coordinator.dispose();
  });

  test('publishes the visible generation before its complete rendered guard', async () => {
    const testCamera = { ...camera, width: 256, height: 256 };
    const visible = fineTileIndices(testCamera, 9, 9, 0);
    const guarded = fineTileIndices(testCamera, 9, 9, 1);
    const resolvers = new Map<string, () => void>();
    const commits: number[] = [];
    const coordinator = new FineTileCoordinator({
      guardTiles: 1,
      debounceMs: 0,
      onCommit: ({ tileCount }) => commits.push(tileCount)
    });
    coordinator.schedule(testCamera, {
      key: 'visible-first',
      minZoom: 9,
      maxZoom: 9,
      load: (index: FineTileIndex) =>
        new Promise((resolve) => {
          const key = `${index.z}/${index.x}/${index.y}`;
          resolvers.set(key, () => resolve({ cells: [[key, 1]] }));
        })
    });
    await new Promise((resolve) => setTimeout(resolve, 5));
    for (const index of visible) {
      resolvers.get(`${index.z}/${index.x}/${index.y}`)?.();
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(commits).toEqual([visible.length]);
    for (const resolve of resolvers.values()) resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(guarded.length).toBeGreaterThan(visible.length);
    expect(commits).toEqual([visible.length, guarded.length]);
    coordinator.dispose();
  });

  test('never lets an obsolete guard replace a newer camera generation', async () => {
    const firstCamera = { ...camera, longitude: 10, width: 256, height: 256 };
    const secondCamera = { ...firstCamera, longitude: 40 };
    const firstVisibleKeys = new Set(
      fineTileIndices(firstCamera, 9, 9, 0).map(({ z, x, y }) => `${z}/${x}/${y}`)
    );
    const firstGuardKeys = new Set(
      fineTileIndices(firstCamera, 9, 9, 1).map(({ z, x, y }) => `${z}/${x}/${y}`)
    );
    const secondGuardKeys = new Set(
      fineTileIndices(secondCamera, 9, 9, 1).map(({ z, x, y }) => `${z}/${x}/${y}`)
    );
    const pending = new Map<string, Array<() => void>>();
    const commits: Array<ReadonlySet<string>> = [];
    const source = {
      key: 'stale-guard',
      minZoom: 9,
      maxZoom: 9,
      load: (index: FineTileIndex) =>
        new Promise<{ cells: [[string, number]] }>((resolve) => {
          const key = `${index.z}/${index.x}/${index.y}`;
          const resolvers = pending.get(key) ?? [];
          resolvers.push(() => resolve({ cells: [[key, 1]] }));
          pending.set(key, resolvers);
        })
    };
    const coordinator = new FineTileCoordinator({
      guardTiles: 1,
      debounceMs: 0,
      onCommit: ({ tileKeys }) => commits.push(new Set(tileKeys))
    });

    coordinator.schedule(firstCamera, source);
    await new Promise((resolve) => setTimeout(resolve, 5));
    for (const key of firstVisibleKeys) pending.get(key)?.forEach((resolve) => resolve());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(commits).toHaveLength(1);

    coordinator.schedule(secondCamera, source);
    await new Promise((resolve) => setTimeout(resolve, 5));
    const secondVisibleKeys = new Set(
      fineTileIndices(secondCamera, 9, 9, 0).map(({ z, x, y }) => `${z}/${x}/${y}`)
    );
    for (const key of secondVisibleKeys) pending.get(key)?.forEach((resolve) => resolve());
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (const key of secondGuardKeys) pending.get(key)?.forEach((resolve) => resolve());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(commits.at(-1)).toEqual(secondGuardKeys);

    for (const key of firstGuardKeys) pending.get(key)?.forEach((resolve) => resolve());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(commits.at(-1)).toEqual(secondGuardKeys);
    coordinator.dispose();
  });

  test('keeps the warmed generation when zooming within guarded coverage', async () => {
    const commits: unknown[][] = [];
    const source = {
      key: 'stable',
      minZoom: 9,
      maxZoom: 9,
      load: async (index: FineTileIndex) => ({
        cells: [[`${index.z}-${index.x}-${index.y}`, 1] as [string, ...number[]]]
      })
    };
    const coordinator = new FineTileCoordinator({
      guardTiles: 1,
      debounceMs: 0,
      onCommit: ({ chunks }) => commits.push(chunks)
    });
    coordinator.schedule(camera, source);
    await new Promise((resolve) => setTimeout(resolve, 5));
    const warmedCommitCount = commits.length;
    expect(warmedCommitCount).toBeGreaterThanOrEqual(1);
    coordinator.schedule({ ...camera, zoom: 12 }, source);
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(commits).toHaveLength(warmedCommitCount);
    expect(coordinator.hasCoverage({ ...camera, zoom: 12 }, source)).toBe(true);
    coordinator.dispose();
  });

  test('invalidates coverage when a large pan leaves the committed guard', async () => {
    const source = {
      key: 'coverage',
      minZoom: 9,
      maxZoom: 9,
      load: async (index: FineTileIndex) => ({
        cells: [[`${index.z}-${index.x}-${index.y}`, 1] as [string, ...number[]]]
      })
    };
    const coordinator = new FineTileCoordinator({
      guardTiles: 1,
      debounceMs: 0,
      onCommit: () => {}
    });
    coordinator.schedule(camera, source);
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(coordinator.hasCoverage(camera, source)).toBe(true);
    expect(coordinator.hasCoverage({ ...camera, longitude: 120 }, source)).toBe(false);
    coordinator.dispose();
  });
});

describe('fine tile loading recovery', () => {
  test('publishes every loaded tile even when the viewport exceeds the cache', async () => {
    const tileCounts: number[] = [];
    const coordinator = new FineTileCoordinator({
      guardTiles: 1,
      debounceMs: 0,
      maxCachedTiles: 2,
      onCommit: ({ chunks, tileCount, cellCount }) => {
        tileCounts.push(tileCount);
        expect(chunks).toHaveLength(tileCount);
        expect(cellCount).toBe(tileCount);
      }
    });
    const source = {
      key: 'small-cache',
      minZoom: 9,
      maxZoom: 9,
      load: async (index: FineTileIndex) => ({
        cells: [[`${index.z}/${index.x}/${index.y}`, 1] as [string, ...number[]]]
      })
    };
    coordinator.schedule(camera, source);
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(tileCounts).toHaveLength(2);
    expect(tileCounts[0]).toBeGreaterThan(2);
    expect(coordinator.hasCoverage(camera, source)).toBe(true);
    coordinator.dispose();
  });

  test('retries a transient failure without requiring camera movement', async () => {
    let attempts = 0;
    const errors: unknown[] = [];
    const coordinator = new FineTileCoordinator({
      guardTiles: 0,
      debounceMs: 0,
      onCommit: () => {},
      onError: (reason) => errors.push(reason)
    });
    const source = {
      key: 'transient',
      minZoom: 9,
      maxZoom: 9,
      load: async () => {
        if (++attempts === 1) throw new Error('Temporary tile failure');
        return { cells: [] };
      }
    };
    coordinator.schedule(camera, source);
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(coordinator.hasCoverage(camera, source)).toBe(true);
    expect(errors).toHaveLength(0);
    coordinator.dispose();
  });

  test('can reschedule the same viewport after requests fail', async () => {
    let failing = true;
    const errors: unknown[] = [];
    const coordinator = new FineTileCoordinator({
      guardTiles: 0,
      debounceMs: 0,
      onCommit: () => {},
      onError: (reason) => errors.push(reason)
    });
    const source = {
      key: 'recover',
      minZoom: 9,
      maxZoom: 9,
      load: async () => {
        if (failing) throw new Error('Tile unavailable');
        return { cells: [] };
      }
    };
    coordinator.schedule(camera, source);
    await new Promise((resolve) => setTimeout(resolve, 250));
    expect(errors).toHaveLength(1);
    expect(coordinator.hasCoverage(camera, source)).toBe(false);
    failing = false;
    coordinator.schedule(camera, source);
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(coordinator.hasCoverage(camera, source)).toBe(true);
    coordinator.dispose();
  });
});
