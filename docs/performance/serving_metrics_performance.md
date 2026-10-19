# Serving metrics: native batch reduction

Historical harnesses and raw reports remain in the Git-ignored
`archive/` only. They are not required for the supported build or test suite.
Use `just data-benchmark` for current end-to-end measurements.

The current post-pair workflow starts with `just data-aggregate`, or
`just data-prepare` to include metadata and metrics. Pair/list aggregation now
uses the [combined streaming path](pair_aggregation_performance.md).
The archived PC scripts remain
historical reference: their per-pair string/parent conversions, polygon-attached
metadata, repeated deduplication, and old DNA scoring are not used by the new
serving build. Resolution 3 still comes from distinct parent/species membership,
so a species spanning several fine cells is counted once in the coarse cell.

`ark_pipeline/aggregation/metrics.py` replaces the expensive fine-cell metric reduction in
`ark_pipeline/builders/fine_metrics.py`. It follows the pair kernel's useful design choices:
native arrays, integer identifiers, immediate consumption, and bounded batches.

1. Evaluate the existing SQL metric predicates once per metadata species.
   NULL behavior and overlapping ecosystem membership are preserved.
2. Read Arrow batches of 16,384 cells and find species IDs in a sorted numeric
   lookup. Split unusually large batches around a target of 250,000
   relationships, without splitting an individual cell.
3. Encode exclusive threat and threat/DNA categories once per species, then
   count each category per cell and ecosystem with native arrays. The four
   DNA summaries remain independent flags with the canonical SQL NULL behavior.
   This avoids gathering relationships separately for all 164 metric columns;
   there is no expanded Python row list or partition-wide metric hash table.
4. Buffer up to roughly 16,384 output cells per Parquet write and convert H3
   indexes to the existing string API format through the native H3 library.
5. Run the existing lossless publication checks before replacing a partition.
   Unknown IDs cannot acquire another species' flags; missing relationships
   fail validation. Receipts cover the new reducer and NumPy version.

The Arrow input batch can exceed the target relationship count for very rich
cells; the relationship target bounds reduction slices, not total RSS. DuckDB's
memory limit also does not govern Arrow/NumPy. Each parallel worker has its own
lookup and buffers. Output follows source cell order, which the current list
exporter sorts; metric values do not depend on that order.

## Source-pinned calculation and publication

A build hashes its selected inputs before checking reuse. Readers consume private
copies: one shared species/system metadata snapshot and one partition snapshot
per active worker. Each copy must match its pinned hash before it is read;
calculation and validation use the same partition copy. Both the snapshots and
current original inputs must still match before publishing an output and receipt.
A changed input fails the build rather than attesting to a different generation.
The receipt identity format is unchanged; code fingerprints invalidate older
calculations normally, and unchanged current-build receipts remain reusable.
Standalone identity checks read current bytes even when a caller reuses its
argument object.

This adds checksum I/O and temporary disk space for metadata, active input
partitions, staged outputs, and a prior-output backup during publication. It does
not establish a wall-time gain. The category reduction reduces repeated array
work; its end-to-end effect needs a separately authorized benchmark.

An advisory output-directory lock excludes other cooperating metric builds.
Each validated partition and receipt is staged before publication; handled
publication errors restore the prior pair. The two replacements are not one
atomic filesystem transaction, and there is no power-loss durability guarantee.
After an interrupted replacement, receipt/output checksums reject a mismatched
pair on reuse. Other programs must not modify private snapshots or write this
output directory during a build. Original input replacement after the final
check can make an output immediately stale, but cannot change the bytes consumed
or the identity attested by its receipt; the next reuse check reads current input
hashes. A failed invocation can retain earlier successfully published partitions,
each with its own verified receipt.

Synthetic regression tests compare all 164 native metrics, their types and order,
with the canonical SQL expressions across threat categories, boolean/NULL DNA
combinations, ecosystem masks, batch boundaries and cells above 65,535
relationships. They also cover missing/noncanonical IDs, serial reader-boundary
mutations of each input kind, parallel shared metadata, receipt-boundary failures,
compatible resume, and interruption cleanup. These fixtures do not establish
global throughput, peak memory, or scientific input coverage.
