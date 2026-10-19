# Exact H3 hierarchy research plan

This is research context, not a production migration specification. The
[implemented pilot](h3_tree.md) compacts accepted resolution-7 leaves and compares
serving-compatible lists and metrics. The current production pair path remains
in place. This code release establishes neither global speedup nor scientific
acceptance of a different coverage policy.

## Contracts and decision rule

Advance only when exact membership and species identities survive the change,
and paired sustained-load measurements show a useful gain across the entire
affected stage. Compaction must start from proven exact leaves or an independently
validated equivalent kernel; a parent polygon does not prove child membership.
Preserve profile exclusions, source precedence, metadata joins and provenance.
Union repeated species across components/sources before fine or coarse counting.

Compare canonical fine and coarse lists and every metric field against the
current reducers. Explicit fixtures should include boundaries, incomplete
subtrees, pentagons, poles, antimeridian crossings and source duplicates. A
complete serving migration would also require boundary products, selected-cell
responses and tile/API equivalence; the current pilot does not validate these.
Benchmark products must never become the global preview generation.

## Representation and validation phases

1. Freeze a baseline schema, code revision and authorized fixture. Record exact
   memberships, relationship bytes, kernel/sort/reducer/write time, peak RSS,
   scratch, workers, hardware and cold/warm state. Use alternating route order
   and row-level comparisons, not only hashes.
2. Produce versioned mixed-resolution covers with stable species ordinals,
   provenance keys and a fingerprinted dictionary. Validate expansion per
   polygon and base cell. Boundary-heavy geometry may receive little compaction;
   any future routing rule needs measured benefit rather than a size-band guess.
3. Compare the compatible tree reducer over a dense base and the whole fixture.
   Include duplicate and cross-source species. Account for cover production,
   sorting, expansion and all output writes. The existing output format still
   requires expanded fine lists. Receipt reuse requires matching inputs, code,
   configuration and output hashes; incomplete outputs must not be selected.

A future production implementation would need spillable partitioning, bounded
largest-partition behavior, deterministic subdivision if justified by evidence,
and complete-generation validation. The pilot's partition record cap and output
buffers are not a global memory solution.

## Optional later work and rollback

A compact serving representation with inherited parent sets and sparse deltas
would be a separate versioned API/reader migration. Consider it only after the
compatible experiment passes and query latency, cache behavior, full build time
and reconstruction correctness are measured. Keep the existing format available
until a complete authorized build passes its acceptance gates.

Any production replacement requires zero semantic differences on agreed
fixtures, bounded-resource cold/warm reruns, a net whole-stage wall-time gain,
and a complete build with provenance and validation receipts. Publish by an
atomic generation switch only after validation; failed or slower candidates
leave the production path and current preview intact. A modest fixture gain is
reason to stop the redesign, not evidence for a global forecast.
