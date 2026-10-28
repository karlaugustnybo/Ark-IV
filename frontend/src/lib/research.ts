/**
 * Research studies shown at /research, modelled on the digital garden's essays.
 * Each entry has its own route at /research/<slug>; add the entry here when a
 * new study page is added. Dates are ISO days.
 */
export type GrowthStage = 'seedling' | 'budding' | 'evergreen';

export interface ResearchEntry {
  slug: string;
  title: string;
  description: string;
  growthStage: GrowthStage;
  planted: string;
  tended: string;
  topics: string[];
}

export const growthStageHelp: Record<GrowthStage, string> = {
  seedling:
    'Seedlings are first passes: results from one build that still need checking and may change.',
  budding:
    'Buddings have been revised and checked against more than one build, but still need refinement.',
  evergreen: 'Evergreens are settled results that have shaped how Ark-IV scores or displays data.'
};

const entries: ResearchEntry[] = [
  {
    slug: 'hotspot-robustness',
    title: 'How robust are Ark-IV’s hotspots?',
    description:
      'Scoring every coarse cell under 288 alternative choices, and what missing DNA evidence and shared branches do to where priority falls.',
    growthStage: 'seedling',
    planted: '2026-10-26',
    tended: '2026-10-28',
    topics: ['Scoring', 'Phylogeny', 'Sensitivity']
  }
];

/** Newest first by last tended date. */
export const researchEntries = [...entries].sort((a, b) => b.tended.localeCompare(a.tended));

export function researchEntry(slug: string): ResearchEntry {
  const entry = researchEntries.find((candidate) => candidate.slug === slug);
  if (!entry) throw new Error(`Unknown research entry: ${slug}`);
  return entry;
}

const relative = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });
const units: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365],
  ['month', 30],
  ['week', 7],
  ['day', 1]
];

/** "3 days ago"-style label for an ISO day, as the garden shows it. */
export function relativeDay(iso: string, now = new Date()): string {
  const days = Math.round((Date.parse(iso) - now.getTime()) / 86_400_000);
  const [unit, size] = units.find(([, size]) => Math.abs(days) >= size) ?? ['day', 1];
  return relative.format(Math.round(days / size), unit);
}
