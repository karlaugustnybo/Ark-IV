// Bun supplies this module at test runtime; the frontend production tsconfig
// intentionally does not include Bun's ambient server-side types.
// @ts-expect-error -- available to `bun test`, excluded from the browser bundle
import { describe, expect, test } from 'bun:test';
import { cellToLatLng, isValidCell, latLngToCell } from 'h3-js';
import { CELL_SEARCH_FAILURE_MESSAGES, parseCellSearch } from '../src/lib/map/cellSearch';

const RES3_CELL = '831f05fffffffff';
const RES7_CELL = '871f05831ffffff';

describe('cell search', () => {
  test('resolves a valid resolution-3 cell with its centre', () => {
    expect(isValidCell(RES3_CELL)).toBe(true);
    const [lat, lng] = cellToLatLng(RES3_CELL);
    expect(parseCellSearch(RES3_CELL)).toEqual({
      ok: true,
      h3Index: RES3_CELL,
      resolution: 3,
      lat,
      lng
    });
  });

  test('resolves a valid resolution-7 cell with its centre', () => {
    expect(isValidCell(RES7_CELL)).toBe(true);
    const [lat, lng] = cellToLatLng(RES7_CELL);
    expect(parseCellSearch(RES7_CELL)).toEqual({
      ok: true,
      h3Index: RES7_CELL,
      resolution: 7,
      lat,
      lng
    });
  });

  test('normalizes uppercase text, stray whitespace, and a 0x prefix', () => {
    const result = parseCellSearch(`  0X ${RES7_CELL.toUpperCase()} \u2028`);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.h3Index).toBe(RES7_CELL);
      expect(result.resolution).toBe(7);
    }
    expect(parseCellSearch(`\t${RES3_CELL.toUpperCase()}\n`)).toMatchObject({
      ok: true,
      h3Index: RES3_CELL,
      resolution: 3
    });
  });

  test('rejects resolutions outside the mapped set', () => {
    for (let resolution = 0; resolution <= 15; resolution += 1) {
      if (resolution === 3 || resolution === 7) continue;
      const cell = latLngToCell(55.68, 12.57, resolution);
      expect(parseCellSearch(cell)).toEqual({
        ok: false,
        reason: 'unsupported-resolution',
        message: CELL_SEARCH_FAILURE_MESSAGES['unsupported-resolution']
      });
    }
  });

  test('rejects strings that are not valid H3 indexes', () => {
    expect(parseCellSearch('871f1d489fffffg')).toMatchObject({ ok: false, reason: 'invalid' });
    expect(parseCellSearch('ffffffffffffffff')).toMatchObject({ ok: false, reason: 'invalid' });
    expect(parseCellSearch('89deadbeefffffff')).toMatchObject({ ok: false, reason: 'invalid' });
    expect(parseCellSearch(latLngToCell(55.68, 12.57, 12) + 'ff')).toMatchObject({
      ok: false,
      reason: 'invalid'
    });
    expect(parseCellSearch('871f05831fffff')).toMatchObject({ ok: false, reason: 'invalid' });
  });

  test('rejects empty and whitespace-only input', () => {
    expect(parseCellSearch('')).toEqual({
      ok: false,
      reason: 'empty',
      message: 'Enter an H3 index'
    });
    expect(parseCellSearch('   ')).toMatchObject({ ok: false, reason: 'empty' });
  });
});
