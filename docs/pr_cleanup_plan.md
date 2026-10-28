# Split the local work into vetted PRs and clean up main

Agreed plan, updated 10 October 2026. This document authorizes the execution
described below when Karl August starts the goal; writing it does not start that
execution. Repository and global instructions still apply, with the explicit
decisions recorded here taking precedence over their defaults.

## Outcome and agreed decisions

Create roughly **10–12 focused local-change PRs**, plus evidence-backed cleanup
PRs from an extensive audit of `main`. Every PR targets `main` and receives
individual vetting. The count is an estimate, not a quota.

At planning time the checkout had 102 modified tracked files, substantial
untracked code, and exposed local artifacts caused by weakened ignore rules.
GitHub's application CI on `main` failed because two benchmark tests required
`tippecanoe`. Reconfirm the repository state and CI evidence before execution.

- Account for all publishable local work, including research prototypes,
  notebooks, and sandbox tooling, but assess scripts for usefulness and bloat.
- Perform a deep audit and focused fixes, not a general architecture rewrite.
- Preserve the current local UI as the intended design. Skip mock rounds for
  this task; include obsolete mock artifacts in the deletion assessment.
- Present an exact deletion list with reasons and wait for Karl August's
  approval before deleting anything. Continue independent work while waiting.
- Merge only the baseline CI repair after its full review loop and checks pass.
  Leave all other PRs open; their merging needs separate authorization.
- Keep restricted data, credentials, source-derived screenshots, receipts,
  generated outputs, and private execution records outside public Git history.
- Use `gpt-6.1-sol` with **medium** reasoning for authoring and fixes, and
  `gpt-6.1-sol` with **high** reasoning for independent reviews. These choices
  override the PR skill's different-model default for this task.

## 1. Preserve the work and establish a healthy baseline

- Record the original commit, working-tree changes, untracked source inventory,
  and file hashes. Store recovery material privately outside the repository;
  avoid copying large datasets. Preserve originals before proposing deletions.
- Leave the original dirty checkout as the reference. Implement and verify in
  isolated worktrees. Do not reset, clean, stash, or overwrite unrelated work.
- Maintain a persistent private ledger containing the original brief, PR/file
  ownership, extraction decisions, findings, reviewed commits, checks, and
  unresolved blockers. Record its location in progress reports so later sessions
  can resume without reconstructing the work.
- Classify every local change as publishable work, a proposed deletion, a
  local-only artifact, or blocked pending a specific decision. Inspect contents,
  including JSON, notebooks, images, and symlinks; extensions alone do not
  establish publication safety. Preserve legitimate redistributable assets and
  their attribution.
- Create a small baseline CI repair PR from clean `main`. Reproduce the benchmark
  failures and isolate orchestration unit tests from external executable
  availability. Preserve meaningful exporter integration coverage instead of
  hiding failures through blanket skips.
- Run the full review loop on that PR, verify CI on its latest head, and merge
  through GitHub under the authorization above. Leave the original dirty checkout
  untouched and refresh the base of subsequent worktrees.

Restore the existing publication exclusions in extracted work. Do not carry the
local `.gitignore` regression into any public branch. Use explicit file staging;
never stage the original checkout wholesale. Run publication checks before each
push, not merely at the end of the project.

## 2. Audit main independently

Review clean `main` before importing local changes, so existing defects remain
distinguishable from regressions. Use sequential review passes with explicit
coverage recorded for each subsystem:

- **Scientific pipeline:** source acquisition, taxonomy transfers, unresolved
  evidence, spatial semantics, aggregation denominators, and scoring.
- **Spatial correctness:** H3 resolutions, CRS, antimeridian handling,
  terrestrial/marine membership, HydroBASINS, and simplification assumptions.
- **Operational behavior:** checkpoints, cancellation, source identity,
  provenance, worker lifecycle, reproducibility, and benchmark claims.
- **Serving contracts:** PostgreSQL schema/loaders, API responses, OpenAPI,
  generated types, coarse/fine delivery, and configuration.
- **Frontend behavior:** viewport generations, stale responses, caching, picking,
  search/filter/selection, reset states, accessibility, and rendering cost.
- **Repository maintenance:** dead paths, duplicated scripts, dependency/toolchain
  consistency, release guards, documentation, and commands that unexpectedly
  require local data.

Record concrete evidence and severity for findings. Fix reproducible defects and
simplify demonstrably unnecessary machinery. Put independent fixes into focused
cleanup PRs. When a local-change PR already fixes a finding, assign it there
instead of creating overlapping changes. Preserve settled scientific inputs;
propose consequential scientific changes explicitly rather than silently changing
the model during cleanup.

## 3. Extract the local PR series

Use these initial review boundaries:

1. **Publication and repository hygiene:** ignore rules, release checks,
   policy/document consistency, and verification of the changed licence text.
2. **Frontend quality tooling:** Oxc configuration, scripts, CI, and necessary
   dependency/lockfile changes.
3. **Acquisition and taxonomy matching:** GoaT/IUCN download improvements,
   crosswalk evidence, identifiers, and trait-transfer safeguards.
4. **Species metadata reconciliation:** spatial-only records, IUCN enrichment,
   uncertainty/provenance, and corresponding serving/UI fields.
5. **Spatial coverage repairs:** geometry handling, source inventories, basin
   handling, profiles, and reproducible spatial fixtures.
6. **Aggregation and tile export performance:** metric reduction, tile workers,
   partitioning, and equivalent-output validation.
7. **Benchmark and runtime reporting:** estimates, progress, checkpoint behavior,
   and documentation distinguishing measured results from projections.
8. **Resident/passage coverage:** profile preparation, database separation,
   request handling, passage overlays, and frontend selection.
9. **Map rendering performance:** tile coordination, render queues, stable H3
   layers, colour computation, picking, and performance tracing.
10. **Current UI and interactions:** controls, boundaries, species search/tables,
    cell details, navigation, styles, tutorial, and methodology routes.
11. **EDGE and phylogenetic research:** scoring, matching, synonyms, validation,
    reference benchmarks, notebooks, and explicit prototype limitations.
12. **Reusable research/cloud tooling and documentation:** retain useful
    reproducible entry points; propose removal of superseded probes, duplicate
    notebooks, mock artifacts, and incident scripts.

Each PR includes its tests and relevant documentation. Extract shared-file
changes by behavior, not by copying entire files. Separate mechanical formatting
from behavioral changes where doing so improves review without creating another
dependency. Existing files and untracked local source must all receive a ledger
disposition; do not silently leave a group out.

**Dependency rule:** each PR must build and pass its applicable checks against its
actual `main` base. Combine inseparable vertical slices; split independent
behavior. Do not duplicate prerequisite commits across PRs or rely on untracked
files from the original checkout. If a later dependency cannot be resolved by
combining coupled work, record the blocker and seek a specific merge decision;
do not silently broaden merge authorization.

For API changes, keep the server, schema/loaders where applicable, OpenAPI,
generated frontend types, clients, and tests together. Keep optional research
dependencies separate from the ordinary application workflow, and preserve the
research prototypes' separation from production scoring. Preserve compatible
checkpoints, source versions, lineage, and uncertainty; do not edit receipts or
move data roots to accommodate code changes.

## 4. Independent review and verification

Use one delegated child at a time:

**Author → fresh reviewer → fresh fixer, if needed → fresh reviewer → ready**

The coordinator owns the ledger and sequencing. Authors/fixers own only the
assigned PR's files and worktree; the coordinator does not edit those files while
they run. Reviewers are read-only. Children must not spawn additional agents,
create duplicate PRs, merge, publish data, or start parallel servers.

### Model settings and context isolation

- Discover the live runtime catalog. Select `gpt-6.1-sol`, medium reasoning for
  author/fixer rounds and high reasoning for reviewer rounds. Do not silently
  substitute another model or reasoning level if unavailable.
- Use a fresh subagent/session for every review and fix round. Do not reuse or
  resume an author, reviewer, or fixer as the next independent reviewer.
- Give each child only its explicit handoff. Do not inherit the parent's full
  conversation or the author's private reasoning. With native delegation use
  `fork_turns="none"`; T3 `delegate_task` supplies only its task prompt.
- Use a new T3 delegated task and client request ID for every round; retain its
  task ID. Do not continue review rounds by messaging the backing child thread.
- The review handoff includes the user requirements, accepted decisions,
  repository instructions, PR URL, exact base/head SHAs, factual prior findings
  and dispositions, checks, and remaining limitations. It excludes speculative
  author explanations that could prime the reviewer.
- Freeze the reviewed head. Prefer a separate detached review worktree so the
  reviewer does not observe edits in progress. Findings must identify the SHA
  reviewed; a changed head invalidates readiness until it is reviewed again.

This supplies independence of context and agent role. Using the same model at a
higher reasoning setting does not provide diversity of model biases or guarantee
correctness; evidence, checks, and review coverage remain necessary.

### Review/fix loop

- Review the entire PR and surrounding contracts on the latest head, not only the
  last fix. Return either an explicit clean review with coverage/limitations or
  findings with IDs, severity, file/line, concrete evidence, and a suggested fix.
- Distinguish actionable defects from optional preferences. A fresh fixer must
  verify findings, fix valid issues, and retain evidence for dismissals. Carry
  unresolved objections into the next review.
- Repeat until a fresh reviewer reports no actionable issues and required checks
  pass for the latest head. Pause for Karl August's decision if two consecutive
  fix rounds cannot resolve the same material issue.
- Run focused Python unittest/Ruff and Bun checks first. Run applicable CI checks
  before declaring readiness; exercise optional research dependencies for their
  PRs. Do not call unavailable or incomplete checks green.
- Use synthetic spatial/scoring fixtures. Compare equivalent outputs before
  reporting performance gains, and record hardware/fixture/resolution/workers
  and cold/warm conditions for measured comparisons.
- Check affected browser flows using synthetic/permitted data: map/table entry
  points, coarse/fine paths, search/filter/selection, profile changes,
  reset/error states, keyboard behavior, and responsive layouts. Record which
  surfaces actually applied and which were verified.
- Register every PR with the thread immediately and verify registration at
  handoff. Use the native CI watcher; end the turn while waiting and resume on
  its wakeup instead of polling. Unwatch when handing work back.

### Combined verification

Assemble proposed changes in a temporary integration branch without merging the
feature PRs. Run the broader data-free suite, frontend build, contract-generation
consistency, release/history guards, and selected cross-feature flows. Inspect
`just check` first; its data-dependent notebook exports are not a routine gate.
Return failures to their owning PRs and re-vet affected heads. Compare the
assembled result against the preserved local work, accounting for intentional
fixes, simplifications, approved deletions, and excluded private artifacts.

Do not run global pipelines, paid cloud jobs, live database loads, or deploy data
as validation. Fixture-sized isolated outputs and synthetic test databases are
allowed. Stop all verification servers started during the work; preserve
user-owned processes and tabs.

## Completion and handoff

- Every original local change has a recorded disposition.
- The baseline CI repair is independently vetted, green, and merged.
- Every remaining PR has a clear purpose, latest-head review, check results, and
  explicit limitations. Unresolved material issues prevent a ready designation.
- The `main` audit has a findings ledger with resolved issues or clearly
  identified outstanding decisions.
- Combined verification exposes no unresolved integration defects.
- Deletions happen only after approval of the concrete list. Pending deletions
  stay recorded and preserved; elapsed time is not approval.
- The final handoff lists PRs, suggested merge order/conflicts, scientific
  limitations, approved deletions, recovery/ledger locations, and process cleanup.

Work autonomously through authorized steps and checkpoint after each PR/round.
Ask only for consequential missing decisions, deletion approval, unresolved
material disputes, or actions outside the authorization here. Runtime
interruptions are resumable; do not claim completion merely because one session
ends. No additional time/token budget is assumed.

## Prompt for /goal

```text
/goal Execute docs/pr_cleanup_plan.md in this repository. Read AGENTS.md and the plan, then carry it through to its completion criteria, resuming from its private ledger if one exists. Use fresh sequential subagents: gpt-6.1-sol medium for authoring/fixes and gpt-6.1-sol high for independent reviews, with no inherited authoring conversation. Preserve the original dirty checkout, account for all local work, and keep restricted data private. Every PR targets main. Merge only the baseline CI repair after its full fresh-review loop and latest-head CI pass; leave every other PR open. The current local UI is the intended design; skip mock rounds. Propose exact deletions with reasons and wait for my approval before deleting. Continue independent authorized work while decisions are pending, checkpoint after each PR/round, and report real blockers and verification limitations. Do not stop at an audit or partial PR extraction; finish the main audit, PR series, individual vetting, and combined integration checks described in the plan.
```
