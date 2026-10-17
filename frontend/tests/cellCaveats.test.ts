// Bun supplies this module at test runtime; the frontend production tsconfig
// intentionally does not include Bun's ambient server-side types.
// @ts-expect-error -- available to `bun test`, excluded from the browser bundle
import { describe, expect, test } from 'bun:test';
import {
  cellCountsCaveat,
  knowledgeLimitsText,
  occupancyCaveat,
  occupancyCaveatText,
  shareText
} from '../src/lib/map/cellCaveats';

describe('cell occupancy caveat copy', () => {
  test('states potential mapped occurrence, not confirmed occupancy', () => {
    expect(occupancyCaveat).toBe('Potential mapped occurrence — not confirmed occupancy');
  });

  test('anchors membership sources and relationship counts, not observations', () => {
    expect(cellCountsCaveat).toBe('cell counts, not observations');
    expect(occupancyCaveat).toContain('mapped occurrence');
  });

  test('appends the count clause only for resolution-7 cells', () => {
    expect(occupancyCaveatText(3)).toBe(occupancyCaveat);
    expect(occupancyCaveatText(7)).toBe(`${occupancyCaveat} · ${cellCountsCaveat}`);
    expect(occupancyCaveatText(7)).toBe(
      'Potential mapped occurrence — not confirmed occupancy · cell counts, not observations'
    );
  });
});

describe('knowledge share formatting', () => {
  test('formats shares as whole percentages', () => {
    expect(shareText(25, 200)).toBe('13%');
    expect(shareText(0, 40)).toBe('0%');
    expect(shareText(40, 40)).toBe('100%');
  });

  test('rounds and clamps shares into the group range', () => {
    expect(shareText(1, 3)).toBe('33%');
    expect(shareText(150, 100)).toBe('100%');
  });

  test('stays finite when a cell has no species at all', () => {
    expect(shareText(0, 0)).toBe('0%');
    expect(shareText(3, 0)).toBe('0%');
  });
});

describe('knowledge limits line', () => {
  test('lists data-deficient, GoaT and species-level gap shares', () => {
    expect(knowledgeLimitsText({ total: 100, dd: 12, ms: 5, mg: 4, mf: 3, gdd: 40 })).toBe(
      'DD 12% · GoaT deficient 40% · no species DNA 12%'
    );
  });

  test('reports a fully gapped cell as entirely unknown', () => {
    expect(knowledgeLimitsText({ total: 10, dd: 10, ms: 6, mg: 6, mf: 6, gdd: 0 })).toBe(
      'DD 100% · GoaT deficient 0% · no species DNA 100%'
    );
  });

  test('renders zero shares instead of NaN for an empty cell', () => {
    expect(knowledgeLimitsText({ total: 0, dd: 0, ms: 0, mg: 0, mf: 0, gdd: 0 })).toBe(
      'DD 0% · GoaT deficient 0% · no species DNA 0%'
    );
  });
});
