// @ts-expect-error -- Bun test types are excluded from the browser tsconfig
import { expect, test } from 'bun:test';
import { FineTileRenderQueue } from '../src/lib/map/fineTileRenderQueue';
import type { FineTileCommit } from '../src/lib/map/fineTileCoordinator';

function commit(keys: string[], sourceKey = 'source'): FineTileCommit {
  return {
    chunks: keys.map((key) => ({ key, cells: [[key, 1]] })),
    cellCount: keys.length,
    sourceKey,
    tileCount: keys.length,
    tileKeys: new Set(keys)
  };
}

function harness(limit = 128) {
  let frame: FrameRequestCallback | null = null;
  let stages = 0;
  const published: FineTileCommit[] = [];
  const queue = new FineTileRenderQueue({
    onStage: () => stages++,
    onPublish: (value) => published.push(value),
    maxResidentTiles: limit,
    requestFrame: (callback) => {
      frame = callback;
      return 1;
    },
    cancelFrame: () => {
      frame = null;
    }
  });
  return {
    queue,
    published,
    stages: () => stages,
    tick: () => {
      if (!frame) throw new Error('No pending frame');
      const callback = frame;
      frame = null;
      callback(0);
    }
  };
}

test('warms at most one tile per frame before atomically displaying the generation', () => {
  const h = harness();
  const value = commit(['a', 'b', 'c']);
  h.queue.submit(value);
  for (let i = 1; i <= 3; i++) {
    h.tick();
    expect(h.queue.chunks).toHaveLength(i);
    expect(h.stages()).toBe(i);
    expect(h.published).toHaveLength(0);
  }
  h.tick();
  expect(h.published).toEqual([value]);
  expect(h.queue.busy).toBe(false);
  h.queue.dispose();
});

test('supersedes incomplete work, reuses warm tiles, and never mixes filter sources', () => {
  const h = harness();
  const first = commit(['a', 'b']);
  h.queue.submit(first);
  h.tick();
  const next = {
    ...first,
    chunks: [first.chunks[0]],
    tileCount: 1,
    cellCount: 1,
    tileKeys: new Set(['a'])
  };
  h.queue.submit(next);
  h.tick();
  expect(h.published).toEqual([next]);
  expect(h.stages()).toBe(1);
  const filtered = commit(['a'], 'filtered');
  h.queue.submit(filtered);
  h.tick();
  expect(h.queue.chunks).toHaveLength(2);
  expect(h.published).toEqual([next]);
  h.tick();
  expect(h.published).toEqual([next, filtered]);
  h.queue.dispose();
});

test('keeps displayed coverage pinned when pruning resident buffers', () => {
  const h = harness(2);
  h.queue.submit(commit(['a', 'b']));
  h.tick();
  h.tick();
  h.tick();
  h.queue.submit(commit(['c', 'd']));
  h.tick();
  h.tick();
  expect(h.queue.chunks).toHaveLength(4);
  h.tick();
  expect(h.queue.chunks.map((chunk) => chunk.key)).toEqual(['c', 'd']);
  h.queue.dispose();
});

test('publishes visible coverage before warming a guard that arrives in the meantime', () => {
  const h = harness();
  const visible = commit(['a']);
  const guard = commit(['a', 'b', 'c']);
  guard.chunks[0] = visible.chunks[0];
  h.queue.submit(visible);
  h.queue.submit(guard);
  h.tick();
  h.tick();
  expect(h.published).toEqual([visible]);
  expect(h.queue.busy).toBe(true);
  h.tick();
  h.tick();
  h.tick();
  expect(h.published).toEqual([visible, guard]);
  h.queue.dispose();
});

test('disposal cancels staging and publishing, including empty coverage', () => {
  const h = harness();
  h.queue.submit(commit([]));
  h.tick();
  expect(h.published).toHaveLength(1);
  h.queue.submit(commit(['a']));
  h.queue.dispose();
  expect(h.queue.chunks).toHaveLength(0);
  h.queue.submit(commit(['b']));
  expect(h.queue.busy).toBe(false);
  expect(h.published).toHaveLength(1);
});
