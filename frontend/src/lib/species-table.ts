export type SpeciesSortKey =
  | 'species_name'
  | 'family'
  | 'redlist_category'
  | 'dna_level'
  | 'unsequenced_myr'
  | 'edge2'
  | 'priority';

export type SpeciesTableRow = {
  gbif_accepted_id: string;
  iucn_sis_id?: string | null;
  iucn_assessment_id?: string | null;
  gbif_taxon_id?: string | null;
  goat_taxon_id?: string | null;
  species_name: string;
  family: string;
  redlist_category: string;
  metadata_status?: string;
  spatial_redlist_category?: string | null;
  iucn_assessment_latest?: boolean | null;
  dnaLabel: string;
  dnaStatus: 'data-deficient' | 'family' | 'genus' | 'species' | 'sampled';
  /** Null when the active score cannot rank this species (no safe tree placement). */
  priority: number | null;
  /** The shown gap is an upper bound for a withheld species (shown with ≤). */
  bounded?: boolean;
  /** Priority ranks a withheld species by that upper bound. */
  priority_bounded?: boolean;
  gap_myr?: number | null;
  edge2_myr_median?: number | null;
  edge2_myr_p025?: number | null;
  edge2_myr_p975?: number | null;
  phylogenetic_status?: string | null;
};

export function dnaStatus(value: string): SpeciesTableRow['dnaStatus'] {
  if (value.startsWith('GoaT Data Deficient')) return 'data-deficient';
  if (value.startsWith('Missing Family')) return 'family';
  if (value.startsWith('Missing Genus')) return 'genus';
  if (value.startsWith('Missing Species')) return 'species';
  return 'sampled';
}
