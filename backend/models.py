from dataclasses import dataclass, field


@dataclass
class HealthResponse:
    status: str
    database: str
    tiles: str


@dataclass
class StatsResponse:
    total: int
    critically_endangered: int
    edge_species: int
    needs_dna_sampling: int
    res3_cells: int
    res7_cells: int


@dataclass
class SpeciesRow:
    gbif_accepted_id: str
    iucn_sis_id: str | None
    iucn_assessment_id: str | None
    gbif_taxon_id: str | None
    goat_taxon_id: str | None
    species_name: str
    family: str
    redlist_category: str
    threat_score: float
    dna_level: str
    priority: float
    metadata_status: str = "assessed"
    spatial_redlist_category: str | None = None
    iucn_assessment_latest: bool | None = None


@dataclass
class SpeciesPage:
    rows: list[SpeciesRow] = field(default_factory=list)
    page: int = 1
    total_pages: int = 1
    total: int = 0
    suggested: bool = False


@dataclass
class SpeciesSuggestion:
    gbif_accepted_id: str
    species_name: str
    family: str


@dataclass
class SpeciesSuggestions:
    rows: list[SpeciesSuggestion] = field(default_factory=list)
    suggested: bool = False


@dataclass
class SpeciesCellsResponse:
    gbif_accepted_id: str
    species_name: str
    resolution: int
    cells: list[str] = field(default_factory=list)
    include_passage: bool = False
    passage_available: bool = False


@dataclass
class CellSpeciesRow:
    gbif_accepted_id: str
    iucn_sis_id: str | None
    iucn_assessment_id: str | None
    gbif_taxon_id: str | None
    goat_taxon_id: str | None
    species_name: str
    family: str
    redlist_category: str
    dna_level: str
    metadata_status: str = "assessed"
    spatial_redlist_category: str | None = None
    iucn_assessment_latest: bool | None = None


@dataclass
class CellStats:
    total: int = 0
    CR: int = 0
    EN: int = 0
    VU: int = 0
    NT: int = 0
    DD: int = 0
    LC: int = 0
    missing_species_dna: int = 0
    missing_genus_dna: int = 0
    missing_family_dna: int = 0
    goat_data_deficient: int = 0


@dataclass
class CellBoundaryMembership:
    framework: str
    framework_name: str
    code: str
    name: str


@dataclass
class CellDetailsResponse:
    h3_index: str
    resolution: int
    boundaries: list[CellBoundaryMembership] = field(default_factory=list)
    species: list[CellSpeciesRow] = field(default_factory=list)
    stats: CellStats | None = field(default_factory=CellStats)
    species_data_unavailable: bool = False


@dataclass
class CoverageProfile:
    id: str
    label: str
    available: bool


@dataclass
class CoverageProfilesResponse:
    selected: str
    profiles: list[CoverageProfile]
