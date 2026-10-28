# ARK-IV / DIKUs-Ark

ARK-IV is a conservation-priority map combining extinction risk, sequencing
evidence, evolutionary distinctiveness, and species distributions. Scientific
interpretability and a responsive map matter together.

## Start here

- `README.md` and `docs/README.md`: current architecture and commands.
- `methodology.md`: scoring, taxonomy, geometry assumptions, and validation limits.
- `DATA_POLICY.md`, `NOTICE.md`, `docs/publication_checklist.md`: release boundaries.
- `ark_pipeline/README.md`: pipeline code map.
- `docs/reference/schema.md`, `openapi.json`: serving and API contracts.
- `docs/roadmap.md`: remaining product/research work.

## Architecture

- `frontend/`: SvelteKit, TypeScript, Tailwind, MapLibre, deck.gl; Bun and Oxc tooling.
- `backend/`: Litestar/Granian API and PostgreSQL serving.
- `ark_pipeline/`: acquisition, taxonomy/crosswalks, H3 spatial coverage, aggregation,
  serving builders, tiles, progress/checkpointing, and provenance.
- `config/`: source inventories and spatial/scientific profiles.
- `tests/` and `frontend/tests/`: focused Python and Bun tests.
- Global resolution-3 context is currently served as Arrow; PMTiles remains a
  build/archive format. Fine resolution-7 cells arrive on demand from Parquet.
  Check the implementation before relying on an older architecture description.

## Scientific and operational invariants

- Missing evidence is not zero evidence. Keep unresolved taxonomy matches and
  uncertainty visible; do not silently transfer genomic or risk evidence.
- Scoring or coverage changes need explicit assumptions and comparison with a
  small reproducible fixture. Check H3 resolutions, CRS, antimeridian behavior,
  terrestrial/marine semantics, and aggregation denominators where relevant.
- Preserve source versions, lineage, validation receipts, and compatible checkpoints.
  Do not edit receipts or move data roots to accommodate a code change.
- This is a code release, not a data release. Local access does not imply permission
  to publish datasets, derived serving artifacts, API responses, screenshots, or tiles.
  Follow the repository's publication policy before any external release.
- Keep `data/`, `acquisition/`, `.tmp/`, and `archive/` local. Never weaken ignores
  or commit database contents, tokens, or restricted inputs.
- Prefer fixture-sized experiments and separate outputs. Do not run `just setup`,
  `data-build`, `data-update`, database loads, or cloud jobs merely to validate code.

## Scientific progress and decisions

- For scientific prototypes, lead with what the result means for ARK, what has
  actually been calculated, and the next decision. Distinguish identifier coverage,
  scored records, pilot versus global output, and website integration. Give
  denominators for coverage claims; do not use test counts as the main status report.
- Preserve agreed scientific inputs while validating new calculations. State which
  uncertainty remains without reopening settled choices. If new evidence warrants
  reconsideration, explain the evidence and propose the change explicitly.

## Implementation and verification

- Follow current `frontend/src/global.css`, `src/app.css`, and affected components
  for ARK's light cream surfaces, serif typography, restrained controls, and data colors.
- Before non-trivial UI, layout, or interface-copy changes, present several styled
  static mocks using permitted/synthetic data and wait for Karl August's selection
  before editing real components. Invisible performance/backend fixes do not require mocks.
- Keep map data compact and typed. Preserve complete viewport generations,
  cancellation/stale-response handling, caching, picking, and basemap attribution.
- When API behavior changes, update the server, `openapi.json`, generated frontend
  types (`bun run generate:api` in `frontend/`), clients, and relevant tests together.
- Use `uv` and `bun`; never npm/npx. Inspect `justfile` before running a recipe.
- Isolated dev servers/browser checks are allowed when useful; announce startup
  and stop servers you started after verification. Report cleanup or a requested
  running URL/port. Starting a dev server does not authorize data rebuilds or loads.
- Python tests use unittest: `uv run python -m unittest discover -s tests -p 'test_NAME.py'`.
  Lint changed Python files with `uv run ruff check <paths>`.
- In `frontend/`: `bun test tests/<file>.test.ts`, `bun run check`, `bun run lint`,
  `bun run format:check`, and `bun run build` as appropriate to the change.
- Inspect `just check` before using it: the current recipe also runs notebook checks
  and exports against local global snapshots. The README's data-free description
  currently lags that recipe. Prefer focused code checks when those inputs are absent
  or a data-dependent notebook run is outside the task.
- Update methodology for scientific changes and docs for user-visible behavior.
  Report actual checks and distinguish measured results from projections.
- State which affected surfaces applied: map/table views, search/filter/selection,
  tutorial/methodology explanations, API/generated types, coarse/fine data paths,
  reverse/reset states, and mobile/keyboard behavior. Trace shared behavior across
  its actual entry points; do not claim validation of paths you did not check.

For cross-project preferences use the global instructions. Skill routing lives in
`SKILLS.md`; the `ark-iv-workflow` global skill supports changes spanning layers.
