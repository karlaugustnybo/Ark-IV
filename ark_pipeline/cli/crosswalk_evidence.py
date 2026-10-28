"""Audit an isolated crosswalk against pinned COL XR, legacy GBIF, OTT and tree.

Name-concept agreement is review evidence, not identifier identity. Tree links
have independent cardinality gates and never grant genomic-transfer permission.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import tarfile
import time
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

from ark_pipeline.cli.crosswalk_catalogue import CATEGORY_LABELS
from ark_pipeline.cli.serving_metadata import source_file
from ark_pipeline.cli.sources_acquire import atomic_json, iso_now, load_manifest, sha256
from ark_pipeline.cli.taxonomy_review import name_index, normalise, pair_review


def canonical_id(value: str, merges: dict[str, str]) -> str | None:
    seen = set()
    while value in merges:
        if value in seen:
            return None
        seen.add(value)
        value = merges[value]
    return value


def ott_links(archive: Path, wanted: set[str], merges: dict[str, str]) -> dict:
    links = defaultdict(list)
    with tarfile.open(archive) as tar:
        members = [x for x in tar.getmembers() if x.name.endswith('/taxonomy.tsv')]
        if len(members) != 1:
            raise ValueError('Expected exactly one OTT taxonomy')
        with io.TextIOWrapper(tar.extractfile(members[0]), encoding='utf-8') as handle:
            fields = [v.strip() for v in next(handle).split('|')]
            for line in handle:
                row = dict(zip(fields, (v.strip() for v in line.split('|'))))
                ids = {canonical_id(v[5:], merges) for v in row['sourceinfo'].split(',')
                       if v.startswith('ncbi:')}
                for taxid in ids & wanted:
                    links[taxid].append({k: row[k] for k in ('uid', 'name', 'rank', 'flags', 'sourceinfo')})
    return dict(links)


def scoped_tree_tips(path: Path, wanted: set[str]) -> tuple[dict, dict]:
    # Leaf tokens start after '(' or ','; internal labels follow ')'. The
    # registered tree's labels use underscores and numeric OTT suffixes.
    text = path.read_text()
    counts, examples, total, placeholders = Counter(), {}, 0, 0
    for match in re.finditer(r"[,(]\s*([^():,;]*_ott([0-9]+))\s*:", text):
        total += 1
        uid = match[2]
        if int(uid) == 0:
            placeholders += 1
        if uid in wanted:
            counts[uid] += 1
            examples[uid] = match[1].strip()
    if not total:
        raise ValueError('No supported OTT leaf labels in registered tree')
    return ({uid: {'count': count, 'example': examples[uid]} for uid, count in counts.items()},
            {'parsed_leaf_labels': total, 'placeholder_leaf_labels': placeholders})


def screen_crosswalk(crosswalk: Path, evidence: Path, output: Path, *,
                     scope: Path | None = None) -> dict:
    """Withhold transfers on explicit COL concept conflicts; never add matches."""
    with duckdb.connect() as con:
        con.execute("SET threads=1; SET memory_limit='512MB'")
        con.read_parquet(str(crosswalk)).create_view('input_crosswalk')
        con.read_json(str(evidence), format='newline_delimited').create_view('evidence')
        scope_join, scope_select, scope_guard = '', '', 'true'
        if scope is not None:
            scope_data = json.loads(scope.read_text())
            source_summary = json.loads(crosswalk.with_name('match_summary.json').read_text())
            for name, filename in (('iucn_taxonomy', 'taxonomy.csv'), ('iucn_assessments', 'assessments.csv')):
                if scope_data['outputs'][filename] != source_summary['sources'][name]['sha256']:
                    raise ValueError('Catalogue scope does not belong to the matched IUCN inputs')
            import pyarrow as pa

            con.register('catalogue_scope', pa.Table.from_pylist([
                {'iucn_sis_id': row['iucn_sis_id'], 'presence': row['presence']}
                for row in scope_data['rows']
            ]))
            invalid_scope = con.execute('''
                SELECT count(*) FROM input_crosswalk c FULL JOIN catalogue_scope s USING(iucn_sis_id)
                WHERE c.iucn_sis_id IS NULL OR s.iucn_sis_id IS NULL
                   OR s.presence NOT IN ('SHARED','CATALOGUE_ONLY','TABULAR_ONLY')
            ''').fetchone()[0]
            count_scope, unique_scope = con.execute('SELECT count(*),count(DISTINCT iucn_sis_id) FROM catalogue_scope').fetchone()
            if invalid_scope or count_scope != unique_scope:
                raise ValueError('Catalogue scope identities do not reconcile')
            scope_join = 'JOIN catalogue_scope s USING(iucn_sis_id)'
            scope_select = 's.presence AS iucn_catalogue_presence,'
            scope_guard = "s.presence <> 'TABULAR_ONLY'"
        total, unique = con.execute('SELECT count(*),count(DISTINCT iucn_sis_id) FROM evidence').fetchone()
        mismatch = con.execute('''
            SELECT count(*) FROM (
                (SELECT iucn_sis_id FROM input_crosswalk EXCEPT SELECT iucn_sis_id FROM evidence)
                UNION ALL
                (SELECT iucn_sis_id FROM evidence EXCEPT SELECT iucn_sis_id FROM input_crosswalk)
            )
        ''').fetchone()[0]
        pair_mismatch = con.execute('''
            SELECT count(*) FROM input_crosswalk c JOIN evidence e USING(iucn_sis_id)
            WHERE coalesce(c.matched_ncbi_species_taxid,c.review_candidate_ncbi_taxid,'')
                  <> coalesce(e.ncbi_taxid,'')
        ''').fetchone()[0]
        if total != unique or mismatch or pair_mismatch:
            raise ValueError('External evidence does not reconcile with crosswalk identities')
        category_cases = ' '.join(f"WHEN '{code}' THEN '{label}'" for code, label in CATEGORY_LABELS.items())
        con.execute(f'''
            CREATE TEMP VIEW screened AS SELECT
                c.* EXCLUDE(safe_for_automatic_species_trait_transfer,iucn_redlist_category),
                CASE upper(c.iucn_redlist_category) {category_cases}
                    ELSE c.iucn_redlist_category END AS iucn_redlist_category,
                c.safe_for_automatic_species_trait_transfer AS pre_external_review_transfer_eligible,
                (c.safe_for_automatic_species_trait_transfer AND NOT e.col_concept_conflict AND {scope_guard})
                    AS safe_for_automatic_species_trait_transfer,
                {scope_select}
                e.col_xr.status AS col_xr_concept_status,
                e.col_xr.iucn_name_concepts AS col_xr_iucn_name_concepts,
                e.col_xr.goat_name_concepts AS col_xr_goat_name_concepts,
                CASE WHEN NOT ({scope_guard}) THEN 'NOT_IN_CURRENT_IUCN_CATALOGUE'
                     WHEN e.col_concept_conflict THEN 'COL_XR_CONCEPT_CONFLICT' END
                    AS external_transfer_block_reason,
                e.tree_status AS ott_tree_review_status
            FROM input_crosswalk c JOIN evidence e USING(iucn_sis_id)
            {scope_join}
        ''')
        con.execute('COPY screened TO ? (FORMAT PARQUET, COMPRESSION ZSTD)', [str(output)])
        return dict(zip(('rows', 'transfer_eligible', 'withheld_for_col_conflict', 'withheld_for_catalogue_scope'), con.execute('''
            SELECT count(*),count(*) FILTER (WHERE safe_for_automatic_species_trait_transfer),
                   count(*) FILTER (WHERE pre_external_review_transfer_eligible
                                      AND external_transfer_block_reason='COL_XR_CONCEPT_CONFLICT'),
                   count(*) FILTER (WHERE pre_external_review_transfer_eligible
                                      AND external_transfer_block_reason='NOT_IN_CURRENT_IUCN_CATALOGUE')
            FROM screened
        ''').fetchone()))


def audit(root: Path, crosswalk: Path, output: Path, *, scope: Path | None = None) -> dict:
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(root)
    paths, inputs = {}, {}
    for key, filename in (
        ('col-xr', 'col-xr-dwca.zip'), ('gbif-backbone', 'backbone.zip'),
        ('ott-taxonomy', 'ott3.7.3.tgz'), ('dated-tree', 'equal_splits_median_tree.tre'),
        ('ncbi-taxonomy', 'merged.dmp'),
    ):
        record = manifest['sources'][key]
        item = next(x for x in record['files'] if x['logical_name'] == filename)
        paths[key] = source_file(root, manifest, key, filename, root / 'unused')
        inputs[key] = {'release': record['release'], 'sha256': item['sha256']}
    crosswalk_hash = sha256(crosswalk)
    with duckdb.connect() as con:
        con.execute("SET threads=1; SET memory_limit='512MB'")
        table = con.read_parquet(str(crosswalk))
        rows = table.project('iucn_sis_id,iucn_scientific_name,iucn_kingdom,iucn_family,'
                             'coalesce(goat_scientific_name,review_candidate_goat_name) AS goat_scientific_name,'
                             'coalesce(matched_ncbi_species_taxid,review_candidate_ncbi_taxid) AS ncbi_taxid,'
                             'safe_for_automatic_species_trait_transfer').fetchdf().to_dict('records')
    for row in rows:
        for field in ('iucn_kingdom', 'iucn_family', 'goat_scientific_name', 'ncbi_taxid'):
            if not isinstance(row[field], str):
                row[field] = ''
    names = {normalise(row[key]) for row in rows
             for key in ('iucn_scientific_name', 'goat_scientific_name') if row[key]}
    print(f'Indexing COL XR for {len(names)} names', flush=True)
    col = name_index(paths['col-xr'], names)
    print('Indexing legacy GBIF for comparison', flush=True)
    legacy = name_index(paths['gbif-backbone'], names)
    merges = {}
    with paths['ncbi-taxonomy'].open() as handle:
        for line in handle:
            old, new, *_ = [v.strip() for v in line.split('|')]
            merges[old] = new
    print('Joining explicit OTT source identifiers', flush=True)
    links = ott_links(paths['ott-taxonomy'], {row['ncbi_taxid'] for row in rows} - {''}, merges)
    tips, tree_counts = scoped_tree_tips(paths['dated-tree'],
                                         {x['uid'] for values in links.values() for x in values})
    summary = {'col_xr': Counter(), 'legacy_gbif': Counter(), 'tree': Counter()}
    with (output / 'evidence.jsonl').open('w') as handle:
        for row in rows:
            left, right = pair_review(row, col), pair_review(row, legacy)
            linked = links.get(row['ncbi_taxid'], [])
            species = [x for x in linked if x['rank'] == 'species']
            eligible = [x for x in species if tips.get(x['uid'], {}).get('count') == 1]
            tree_status = ('UNIQUE_SPECIES_TIP' if len(species) == 1 and len(eligible) == 1
                           else 'AMBIGUOUS' if len(species) > 1
                           else 'NO_UNIQUE_SPECIES_TIP')
            item = {'iucn_sis_id': int(row['iucn_sis_id']), 'ncbi_taxid': row['ncbi_taxid'],
                    'col_xr': left, 'legacy_gbif': right, 'ott_records': linked,
                    'tree_status': tree_status,
                    'tree_tips': [tips[x['uid']] for x in species if x['uid'] in tips],
                    'input_transfer_eligible': bool(row['safe_for_automatic_species_trait_transfer']),
                    'col_concept_conflict': left['status'] == 'concept_conflict',
                    'identifier_identity_confirmed_by_col': False}
            handle.write(json.dumps(item, separators=(',', ':')) + '\n')
            summary['col_xr'][left['status']] += 1
            summary['legacy_gbif'][right['status']] += 1
            summary['tree'][tree_status] += 1
    latest = load_manifest(root)
    if any(latest['sources'].get(key) != manifest['sources'][key] for key in paths):
        raise ValueError('Selected source changed during evidence audit')
    if sha256(crosswalk) != crosswalk_hash:
        raise ValueError('Crosswalk changed during evidence audit')
    screening = screen_crosswalk(crosswalk, output / 'evidence.jsonl', output / 'screened_crosswalk.parquet', scope=scope)
    result = {'schema_version': 1, 'generated_at': iso_now(), 'inputs': inputs,
              'crosswalk_sha256': crosswalk_hash, 'rows': len(rows),
              'counts': {k: dict(v) for k, v in summary.items()}, 'tree_parse': tree_counts,
              'seconds': time.monotonic() - started,
              'evidence_sha256': sha256(output / 'evidence.jsonl'),
              'screening': screening,
              'catalogue_scope_sha256': sha256(scope) if scope else None,
              'screened_crosswalk_sha256': sha256(output / 'screened_crosswalk.parquet'),
              'code_sha256': {p.name: sha256(p) for p in (Path(__file__), Path(__file__).with_name('taxonomy_review.py'))},
              'policy': 'COL concept conflict withholds transfer; agreement creates no matches; tree links never authorize DNA transfer'}
    atomic_json(output / 'summary.json', result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--crosswalk', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scope', type=Path, help='Scope receipt from crosswalk_catalogue')
    args = parser.parse_args()
    print(json.dumps(audit(args.root, args.crosswalk, args.output, scope=args.scope)))


if __name__ == '__main__':
    main()
