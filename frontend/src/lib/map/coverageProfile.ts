export type CoverageProfile = 'resident' | 'with-passage';

export function selectedCoverageProfile(search: string): CoverageProfile {
  return new URLSearchParams(search).get('coverage_profile') === 'with-passage'
    ? 'with-passage'
    : 'resident';
}

export function coverageUrl(value: string, profile: CoverageProfile): string {
  const [withoutHash, hash] = value.split('#', 2);
  const index = withoutHash.indexOf('?');
  const path = index < 0 ? withoutHash : withoutHash.slice(0, index);
  const query = new URLSearchParams(index < 0 ? '' : withoutHash.slice(index + 1));
  query.set('coverage_profile', profile);
  return `${path}?${query}${hash === undefined ? '' : '#' + hash}`;
}

export const coverageProfile: CoverageProfile =
  typeof window === 'undefined' ? 'resident' : selectedCoverageProfile(window.location.search);

export function withCoverageProfile(value: string): string {
  return coverageUrl(value, coverageProfile);
}
