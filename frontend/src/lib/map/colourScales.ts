import type { ScoreDomain } from './mapBundle';

export type ColourScaleMode = 'linear' | 'log' | 'jenks';
export type ColourScaleSettings = {
  mode: ColourScaleMode;
  logOffset: number;
  logGamma: number;
  classes: number;
};
export const defaultColourScale: ColourScaleSettings = {
  mode: 'log',
  logOffset: 1,
  logGamma: 1,
  classes: 5
};

const clamp = (value: number) => Math.min(1, Math.max(0, value));

export function colourScalePosition(
  value: number,
  domain: ScoreDomain,
  settings: ColourScaleSettings,
  breaks: readonly number[] = []
): number {
  // Non-positive scores share the lowest colour in every mode.
  if (!Number.isFinite(value) || value <= 0) return 0;
  if (settings.mode === 'jenks' && breaks.length) {
    if (breaks.length <= 2) return breaks[breaks.length - 1] > 0 ? 1 : 0;
    let index = 0;
    while (index < breaks.length - 2 && value > breaks[index + 1]) index += 1;
    return index / (breaks.length - 2);
  }
  const min = Math.max(0, domain.min);
  const max = Math.max(min, domain.max);
  if (max <= min) return 1;
  if (settings.mode === 'log') {
    const offset =
      Number.isFinite(settings.logOffset) && settings.logOffset > 0
        ? settings.logOffset
        : defaultColourScale.logOffset;
    const low = Math.log1p(min / offset);
    const position = clamp((Math.log1p(value / offset) - low) / (Math.log1p(max / offset) - low));
    const gamma =
      Number.isFinite(settings.logGamma) && settings.logGamma > 0
        ? settings.logGamma
        : defaultColourScale.logGamma;
    return position ** gamma;
  }
  return clamp((value - min) / (max - min));
}

/** Exact 1-D variance minimization (Fisher–Jenks), with weighted duplicate values.
 * Divide-and-conquer optimization of the monotone DP avoids the quadratic
 * algorithm: O(k n log n) time, O(k n) memory. No random sampling or binning.
 * See https://arxiv.org/abs/1701.07204 for optimal interval clustering.
 * Returns [minimum, class-1 maximum, ..., last-class maximum]; upper bounds
 * are inclusive, so equal scores always receive the same colour.
 */
export function jenksBreaks(input: ArrayLike<number>, requestedClasses: number): number[] {
  const sorted = Array.from(input)
    .filter(Number.isFinite)
    .map((value) => Math.max(0, value))
    .sort((a, b) => a - b);
  if (!sorted.length) return [];
  const values: number[] = [];
  const counts: number[] = [];
  for (const value of sorted) {
    if (values.length && value === values[values.length - 1]) counts[counts.length - 1] += 1;
    else {
      values.push(value);
      counts.push(1);
    }
  }
  const n = values.length;
  const k = Math.min(n, Math.max(1, Math.round(requestedClasses)));
  // Scale to [0, 1] before prefix sums to limit cancellation and overflow.
  const span = values[n - 1] - values[0] || 1;
  const weight = new Float64Array(n + 1);
  const sum = new Float64Array(n + 1);
  const square = new Float64Array(n + 1);
  for (let i = 0; i < n; i += 1) {
    const x = (values[i] - values[0]) / span;
    weight[i + 1] = weight[i] + counts[i];
    sum[i + 1] = sum[i] + x * counts[i];
    square[i + 1] = square[i] + x * x * counts[i];
  }
  const variance = (start: number, end: number) => {
    const s = sum[end] - sum[start];
    return Math.max(0, square[end] - square[start] - (s * s) / (weight[end] - weight[start]));
  };
  let previous = new Float64Array(n + 1).fill(Infinity);
  previous[0] = 0;
  const splits: Int32Array[] = [];
  for (let classes = 1; classes <= k; classes += 1) {
    const next = new Float64Array(n + 1).fill(Infinity);
    const split = new Int32Array(n + 1);
    const solve = (left: number, right: number, first: number, last: number) => {
      if (left > right) return;
      const middle = (left + right) >> 1;
      let best = first;
      for (let j = first; j <= Math.min(last, middle - 1); j += 1) {
        const cost = previous[j] + variance(j, middle);
        if (cost < next[middle]) {
          next[middle] = cost;
          best = j;
        }
      }
      split[middle] = best;
      solve(left, middle - 1, first, best);
      solve(middle + 1, right, best, last);
    };
    solve(classes, n, classes - 1, n - 1);
    splits.push(split);
    previous = next;
  }
  const maxima: number[] = [];
  let end = n;
  for (let classes = k; classes > 0; classes -= 1) {
    maxima.push(values[end - 1]);
    end = splits[classes - 1][end];
  }
  return [values[0], ...maxima.reverse()];
}
