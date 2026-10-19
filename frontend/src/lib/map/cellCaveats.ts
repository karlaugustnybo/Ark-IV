export const occupancyCaveat = 'Potential mapped occurrence — not confirmed occupancy';
export const cellCountsCaveat = 'cell counts, not observations';

export type CellKnowledgeCounts = {
  total: number;
  dd: number;
  ms: number;
  mg: number;
  mf: number;
  gdd: number;
};

export function shareText(count: number, total: number): string {
  if (!(total > 0) || !(count > 0)) return '0%';
  const share = Math.min(1, Math.max(0, count / total));
  return `${Math.round(share * 100)}%`;
}

export function knowledgeLimitsText(counts: CellKnowledgeCounts): string {
  return (
    `DD ${shareText(counts.dd, counts.total)} · ` +
    `GoaT deficient ${shareText(counts.gdd, counts.total)} · ` +
    `no species DNA ${shareText(counts.ms + counts.mg + counts.mf, counts.total)}`
  );
}

export function occupancyCaveatText(resolution: number): string {
  return resolution === 7 ? `${occupancyCaveat} · ${cellCountsCaveat}` : occupancyCaveat;
}
