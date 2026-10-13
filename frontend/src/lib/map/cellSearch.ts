import { cellToLatLng, getResolution, isValidCell } from 'h3-js';

export type CellSearchSelection = {
  h3Index: string;
  resolution: 3 | 7;
  lng: number;
  lat: number;
};

export type CellSearchFailureReason = 'empty' | 'invalid' | 'unsupported-resolution';

export type CellSearchResult =
  | ({ ok: true } & CellSearchSelection)
  | { ok: false; reason: CellSearchFailureReason; message: string };

export const CELL_SEARCH_FAILURE_MESSAGES: Record<CellSearchFailureReason, string> = {
  empty: 'Enter an H3 index',
  invalid: 'Not a valid H3 index',
  'unsupported-resolution': 'Unsupported resolution — only resolutions 3 and 7 are mapped'
};

export function normalizeCellSearchInput(rawInput: string): string {
  return rawInput.trim().replace(/\s+/g, '').replace(/^0x/i, '').toLowerCase();
}

export function cellSearchFailureMessage(reason: CellSearchFailureReason): string {
  return CELL_SEARCH_FAILURE_MESSAGES[reason];
}

export function parseCellSearch(rawInput: string): CellSearchResult {
  const h3Index = normalizeCellSearchInput(rawInput);
  if (!h3Index) return cellSearchFailure('empty');
  if (!isValidCell(h3Index)) return cellSearchFailure('invalid');
  const resolution = getResolution(h3Index);
  if (resolution !== 3 && resolution !== 7) {
    return cellSearchFailure('unsupported-resolution');
  }
  const [lat, lng] = cellToLatLng(h3Index);
  return { ok: true, h3Index, resolution: resolution === 3 ? 3 : 7, lat, lng };
}

function cellSearchFailure(reason: CellSearchFailureReason): CellSearchResult {
  return { ok: false, reason, message: cellSearchFailureMessage(reason) };
}
