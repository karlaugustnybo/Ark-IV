# Replaying coarse exports

`scripts/benchmark_coarse_exports.py` replays actual-main coarse export functions
from an explicit benchmark database and boundary configuration. It opens the
source database read-only, requires a fresh output directory, and records input
hashes, dependency versions, thread count, phase timings, process peak RSS and
artifact hashes. It does not rerun aggregation or compile Tippecanoe.

```sh
uv run --frozen python -m scripts.benchmark_coarse_exports \
  --benchmark-root /private/fixture-benchmark --output-root /private/new-replay
```

Run baseline and candidate against identical inputs. Compare every artifact
hash before comparing timing. Record hardware, fixture, resolutions, workers,
cache state and run order. Use fresh processes and alternating trial order;
a faster microbenchmark or one export-stage result does not prove a global
pipeline speedup. Optional `--profile` changes timing overhead.

The source and generated reports may contain restricted source values or paths.
Keep them private under the publication policy. Synthetic fixture validation
exercises the exporter contract without publishing historical data or results.

When changing boundary-index reuse, compare catalogue order, empty membership,
boundary-only contact and antimeridian fixtures as well as every exported byte.
Any cache must retain the intended resolution and lifetime scope; prepared
geometry memory depends on the selected catalogue's complexity. This replay
calls the selected checkout's implementation and makes no claim that an
unmerged optimization or a historical speedup is present in actual main.
