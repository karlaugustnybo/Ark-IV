// @ts-expect-error -- Bun test types are excluded from the browser tsconfig
import { describe, expect, test } from 'bun:test';
import { selectedCoverageProfile, coverageUrl } from '../src/lib/map/coverageProfile';

describe('coverage profiles', () => {
  test('replaces previous profile and preserves query parameters and fragment', () => {
    const result = coverageUrl(
      '/map?coverage_profile=resident&system=marine#legend',
      'with-passage'
    );
    const url = new URL(result, 'http://localhost');
    expect(url.searchParams.getAll('coverage_profile')).toEqual(['with-passage']);
    expect(url.searchParams.get('system')).toBe('marine');
    expect(url.hash).toBe('#legend');
  });
  test('defaults to resident and selects explicit passage', () => {
    expect(selectedCoverageProfile('')).toBe('resident');
    expect(selectedCoverageProfile('?coverage_profile=with-passage')).toBe('with-passage');
  });
  test('separates API, PMTiles, metadata and fine tile cache URLs', () => {
    for (const url of [
      '/api/cells/abc/species?system=marine',
      '/tiles/priorities.pmtiles',
      '/tiles/map-metadata.json',
      '/tiles/res3-priorities-0123456789abcdef.arrow',
      '/api/tiles/res7/8/2/3?v=123'
    ]) {
      const resident = coverageUrl(url, 'resident');
      const passage = coverageUrl(url, 'with-passage');
      expect(resident).not.toBe(passage);
      expect(new URL(passage, 'http://localhost').searchParams.get('coverage_profile')).toBe(
        'with-passage'
      );
    }
  });
});
