// @ts-expect-error -- Bun test runtime is excluded from the browser tsconfig
import { describe, expect, test } from 'bun:test';
import { colourScalePosition, defaultColourScale, jenksBreaks } from '../src/lib/map/colourScales';

const domain = { min: 0, max: 100 };
const log = { ...defaultColourScale, mode: 'log' as const, logGamma: 1 };
const linear = { ...defaultColourScale, mode: 'linear' as const };
const jenks = { ...defaultColourScale, mode: 'jenks' as const };

describe('map colour scale transformations', () => {
  test('defaults to log with gamma 1 and clamps endpoints in every mode', () => {
    expect(defaultColourScale).toEqual({ mode: 'log', logOffset: 1, logGamma: 1, classes: 5 });
    expect(colourScalePosition(50, domain, linear)).toBe(0.5);
    for (const settings of [defaultColourScale, linear, log, jenks]) {
      for (const value of [0, -10, NaN, Infinity])
        expect(colourScalePosition(value, domain, settings)).toBe(0);
      expect(colourScalePosition(1000, domain, settings)).toBe(1);
    }
  });

  test('contrast separates medium from highest scores while preserving order and endpoints', () => {
    const medium = colourScalePosition(50, domain, log);
    const contrasted = colourScalePosition(50, domain, { ...log, logGamma: 2 });
    expect(contrasted).toBeCloseTo(medium ** 2);
    expect(1 - contrasted).toBeGreaterThan(1 - medium);
    for (const gamma of [0.1, 1, 2, 5]) {
      const settings = { ...log, logGamma: gamma };
      const positions = [0, 10, 25, 50, 75, 100].map((value) =>
        colourScalePosition(value, domain, settings)
      );
      expect(positions[0]).toBe(0);
      expect(positions[5]).toBe(1);
      for (let i = 1; i < positions.length; i++)
        expect(positions[i]).toBeGreaterThan(positions[i - 1]);
    }
    expect(colourScalePosition(50, domain, { ...log, logGamma: NaN })).toBe(medium);
    expect(colourScalePosition(50, domain, { ...linear, logGamma: 5 })).toBe(0.5);
  });

  test('supports zero with log1p and uses offset to control low-score contrast', () => {
    expect(colourScalePosition(1, domain, log)).toBeCloseTo(Math.log(2) / Math.log(101));
    expect(colourScalePosition(10, domain, { ...log, logOffset: 0.1 })).toBeGreaterThan(
      colourScalePosition(10, domain, log)
    );
    expect(colourScalePosition(10, domain, { ...log, logOffset: 1000 })).toBeLessThan(
      colourScalePosition(10, domain, log)
    );
  });

  test('handles constant domains and includes exact class upper boundaries', () => {
    expect(colourScalePosition(2, { min: 2, max: 2 }, log)).toBe(1);
    const breaks = [1, 3, 12, 100];
    expect(colourScalePosition(3, domain, jenks, breaks)).toBe(0);
    expect(colourScalePosition(3.01, domain, jenks, breaks)).toBe(0.5);
    expect(colourScalePosition(12, domain, jenks, breaks)).toBe(0.5);
    expect(colourScalePosition(13, domain, jenks, breaks)).toBe(1);
    expect(colourScalePosition(1, domain, jenks, [1, 1])).toBe(1);
  });
});

describe('exact Jenks natural breaks', () => {
  test('separates natural clusters, preserves duplicate weights and reduces class count', () => {
    expect(jenksBreaks([1, 2, 3, 10, 11, 12, 90, 95, 100], 3)).toEqual([1, 3, 12, 100]);
    expect(jenksBreaks([2, 2, 2], 5)).toEqual([2, 2]);
    expect(jenksBreaks([1, 1, 2, 2], 5)).toEqual([1, 1, 2]);
    expect(jenksBreaks([], 5)).toEqual([]);
    expect(jenksBreaks([NaN, Infinity, -1, 0], 5)).toEqual([0, 0]);
  });

  test('matches an exhaustive weighted partition oracle on deterministic populations', () => {
    const cost = (xs: number[]) => {
      const mean = xs.reduce((sum, x) => sum + x, 0) / xs.length;
      return xs.reduce((sum, x) => sum + (x - mean) ** 2, 0);
    };
    const optimal = (xs: number[], k: number): number => {
      if (k === 1) return cost(xs);
      let best = Infinity;
      for (let i = 1; i < xs.length; i += 1) {
        if (xs[i] === xs[i - 1] || new Set(xs.slice(i)).size < k - 1) continue;
        best = Math.min(best, cost(xs.slice(0, i)) + optimal(xs.slice(i), k - 1));
      }
      return best;
    };
    let seed = 123;
    for (let run = 0; run < 60; run += 1) {
      const xs = Array.from({ length: 12 }, () => {
        seed = (seed * 1664525 + 1013904223) >>> 0;
        return seed % 25;
      }).sort((a, b) => a - b);
      for (const k of [2, 3, 4]) {
        const breaks = jenksBreaks(xs, k);
        const groups = breaks
          .slice(1)
          .map((upper, i) => xs.filter((x) => x <= upper && (i === 0 || x > breaks[i])));
        expect(groups.reduce((sum, group) => sum + cost(group), 0)).toBeCloseTo(
          optimal(xs, Math.min(k, new Set(xs).size)),
          8
        );
        expect(jenksBreaks([...xs].reverse(), k)).toEqual(breaks);
      }
    }
  });
});
