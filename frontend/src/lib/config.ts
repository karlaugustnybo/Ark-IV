import { withCoverageProfile } from '$lib/map/coverageProfile';
import {
  PUBLIC_API_BASE_URL,
  PUBLIC_BASEMAP_URL,
  PUBLIC_MAP_METADATA_URL,
  PUBLIC_TILE_URL
} from '$env/static/public';

export const config = {
  apiBaseUrl: PUBLIC_API_BASE_URL,
  basemapUrl: PUBLIC_BASEMAP_URL,
  mapMetadataUrl: withCoverageProfile(PUBLIC_MAP_METADATA_URL),
  tileUrl: withCoverageProfile(PUBLIC_TILE_URL)
};
