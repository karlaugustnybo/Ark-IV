import { derived, writable } from 'svelte/store';
import type { operations } from '$lib/api/schema';

export const defaultWeights = {
  cr: 4,
  en: 3,
  vu: 2,
  nt: 1,
  dd: 2,
  lc: 0.1,
  sp: 2,
  gen: 3,
  fam: 4,
  gdd: 4,
  samp: 0
};

export type WeightKey = keyof typeof defaultWeights;
export const weights = writable({ ...defaultWeights });

export function resetWeights() {
  weights.set({ ...defaultWeights });
}

/**
 * How the DNA gap enters priority: GoaT buckets × policy weights, or unsequenced
 * branch length (Myr) × EDGE2 extinction probability (`expected-loss`).
 * `includeWithheld` ranks species whose stored gap is withheld (unsafe genomic
 * identity, incomplete baseline) at their upper bound (`expected-loss-upper`).
 */
export type DnaMeasure = 'buckets' | 'branch';
export type ScoreMode = NonNullable<
  NonNullable<operations['getSpecies']['parameters']['query']>['score']
>;

export const dnaMeasure = writable<DnaMeasure>('branch');
export const includeWithheld = writable(true);
export const scoreMode = derived([dnaMeasure, includeWithheld], ([measure, withheld]): ScoreMode =>
  measure === 'buckets' ? 'buckets' : withheld ? 'expected-loss-upper' : 'expected-loss'
);
