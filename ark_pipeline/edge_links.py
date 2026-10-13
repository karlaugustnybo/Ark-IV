"""Auditable species links to the released global dated Tree of Life.

Index Newick leaves without constructing millions of ETE node objects. Taxon
identity links are separate from accepting tree dates or computing EDGE scores.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.edge import canonical_taxid
from ark_pipeline.runtime.provenance import atomic_json, sha256


def newick_tips(text: str):
    """Read unquoted, uncommented release-style leaves and terminal lengths."""
    text = text.strip()
    if any(c in text for c in "'\"[]") or not text.endswith(";") or text.count(";") != 1:
        raise ValueError("Expected one unquoted, uncommented Newick tree")
    depth = 0
    for token in re.finditer(r"[()]", text):
        depth += 1 if token[0] == "(" else -1
        if depth < 0:
            raise ValueError("Unbalanced Newick parentheses")
    if depth or not text.startswith("("):
        raise ValueError("Unbalanced or nonbranching Newick tree")
    count = 0
    for match in re.finditer(r"(?<=[(,])([^(),:;]+):([^(),:;]+)", text):
        label = match[1].strip()
        ott = re.fullmatch(r"[^\s:;(),]+_ott(\d+)", label)
        if ott is None:
            raise ValueError(f"Leaf lacks a release-style OTT identifier: {label}")
        length = float(match[2])
        if not math.isfinite(length) or length < 0:
            raise ValueError(f"Invalid terminal branch length: {label}")
        count += 1
        yield {"tree_tip": label, "ott_id": str(int(ott[1])), "terminal_myr": length}
    if count != text.count(",") + 1 or count < 2:
        raise ValueError("Missing, malformed or unsupported Newick leaves")


def build_tip_index(tree: Path, output: Path) -> Path:
    stamp = output.with_suffix(".receipt.json")
    identity = {"schema_version": 2, "tree_sha256": sha256(tree)}
    if output.is_file() and stamp.is_file():
        saved = json.loads(stamp.read_text())
        if saved.get("input") == identity:
            if sha256(output) != saved["output_sha256"]:
                raise ValueError("Global leaf index checksum changed")
            return output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".raw.part")
    indexed = output.with_suffix(".part")
    schema = pa.schema(
        [
            ("leaf_index", pa.int64()),
            ("tree_tip", pa.string()),
            ("ott_id", pa.string()),
            ("terminal_myr", pa.float64()),
        ]
    )
    count = 0
    try:
        with pq.ParquetWriter(temporary, schema, compression="zstd") as writer:
            batch = []
            for tip in newick_tips(tree.read_text()):
                batch.append({"leaf_index": count, **tip})
                count += 1
                if len(batch) == 100_000:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                    batch.clear()
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
        with duckdb.connect() as c:
            c.execute("SET memory_limit='1GB'")
            c.read_parquet(str(temporary)).create_view("raw_tips")
            c.execute(
                "COPY (SELECT *,count(*) OVER(PARTITION BY tree_tip) AS label_occurrences FROM raw_tips) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                [str(indexed)],
            )
        indexed.replace(output)
        atomic_json(stamp, {"input": identity, "rows": count, "output_sha256": sha256(output)})
    finally:
        temporary.unlink(missing_ok=True)
        indexed.unlink(missing_ok=True)
    return output


def direct_identifiers(
    tips: Path, taxonomy: Path, taxids: set[str], merges: dict[str, str]
) -> tuple[dict[str, set[str]], set[str]]:
    """Collect ALL species-level OTT leaf candidates for target canonical IDs."""
    if not taxids:
        return {}, set()
    with duckdb.connect() as c:
        c.execute("SET memory_limit='1GB'")
        c.register("targets", pd.DataFrame({"canonical_id": sorted(taxids)}))
        c.register(
            "merges",
            pd.DataFrame(
                [{"old_id": old, "canonical_id": canonical_taxid(old, merges)} for old in merges],
                columns=["old_id", "canonical_id"],
                dtype="str",
            ),
        )
        c.read_parquet(str(tips)).create_view("tips")
        c.read_parquet(str(taxonomy)).create_view("taxonomy")
        data = c.execute("""
            WITH ids AS (
                SELECT tips.tree_tip,tips.label_occurrences,substring(source,6) AS old_id
                FROM tips JOIN taxonomy USING(ott_id),
                     unnest(string_split(taxonomy.sourceinfo,',')) AS s(source)
                WHERE taxonomy.taxon_rank='species' AND starts_with(source,'ncbi:')
            )
            SELECT DISTINCT ids.tree_tip,targets.canonical_id,ids.label_occurrences
            FROM ids LEFT JOIN merges USING(old_id)
            JOIN targets ON targets.canonical_id=coalesce(merges.canonical_id,ids.old_id)
        """).fetchall()
    identifiers = defaultdict(set)
    repeated = set()
    for tip, taxid, occurrences in data:
        identifiers[taxid].add(tip)
        if occurrences > 1:
            repeated.add(taxid)
    return dict(identifiers), repeated


def exact_name_candidates(tips: Path, taxonomy: Path, names: dict[str, set[str]]) -> pd.DataFrame:
    """Keep every matching leaf, including homonyms, for lineage/uniqueness checks."""
    search = pd.DataFrame(
        {"normalized_name": sorted({name for values in names.values() for name in values})}
    )
    if search.empty:
        return pd.DataFrame(columns=["tree_tip", "ott_id"])
    with duckdb.connect() as c:
        c.execute("SET memory_limit='1GB'")
        c.register("names", search)
        return c.execute(
            """
            SELECT DISTINCT tips.tree_tip,tips.ott_id,tips.label_occurrences
            FROM read_parquet(?) tips JOIN read_parquet(?) t USING(ott_id)
            JOIN names ON lower(regexp_replace(trim(t.taxon_name),'\\s+',' ','g'))=names.normalized_name
            WHERE t.taxon_rank='species'
        """,
            [str(tips), str(taxonomy)],
        ).fetchdf()


def current_ncbi_ranks(nodes: Path, taxids: set[str]) -> dict[str, str]:
    result = {}
    with nodes.open() as handle:
        for line in handle:
            parts = [part.strip() for part in line.split("|")[:3]]
            if parts[0] in taxids:
                result[parts[0]] = parts[2]
    return result
