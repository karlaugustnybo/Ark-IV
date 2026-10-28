"""Fetch raw Red List identity provenance omitted by GoaT v3 search responses.

This is a versioned supplement, never a mutation of a registered GoaT snapshot.
Batch caches are keyed by requested IDs; returned record identities are checked
against the documents themselves, not the API's positional record labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ark_pipeline.cli.sources_acquire import atomic_json, iso_now, sha256

API = 'https://goat.genomehubs.org/api/v3/record/batch'
FIELDS = ('redlist_category', 'redlist_population_trend', 'redlist_scopes')


def normalize_batch(body: dict, requested: list[str]) -> list[dict]:
    if body.get('status', {}).get('success') is not True:
        raise ValueError('GoaT batch request was not successful')
    result, seen = [], set()
    for item in body.get('records', []):
        record = item.get('record')
        if not isinstance(record, dict):
            raise ValueError('GoaT batch has no record document')
        taxid = str(record.get('taxon_id', ''))
        if taxid not in requested or taxid in seen:
            raise ValueError('GoaT returned an unexpected or duplicate document identity')
        seen.add(taxid)
        attrs = [a for a in record.get('attributes', []) if a.get('key') in FIELDS]
        if len({a['key'] for a in attrs}) != len(attrs):
            raise ValueError('GoaT returned duplicate Red List attributes')
        provenance, row = {}, {'ncbi_taxid': taxid,
                               'goat_scientific_name': record.get('scientific_name'),
                               'goat_taxon_rank': record.get('taxon_rank')}
        for attr in attrs:
            key = attr['key']
            value = attr.get('keyword_value', [])
            row[key] = ';'.join(map(str, value)) if isinstance(value, list) else str(value)
            provenance[key] = {k: v for k, v in attr.items()
                               if k not in {'key', 'keyword_value', 'values'}}
            provenance[key]['rawValues'] = attr.get('values', [])
        row['field_provenance_json'] = json.dumps(provenance, separators=(',', ':'))
        result.append(row)
    if seen != set(requested):
        raise ValueError(f'GoaT returned {len(seen)}/{len(requested)} requested documents')
    return result


def fetch(ids: list[str], output: Path, snapshot_sha256: str, *, batch_size: int = 500,
          workers: int = 2) -> dict:
    if not 1 <= batch_size <= 1000 or not ids or len(set(ids)) != len(ids):
        raise ValueError('Require unique nonempty IDs and batch size 1..1000')
    if len(snapshot_sha256) != 64:
        raise ValueError('Require the registered GoaT snapshot SHA-256')
    if not 1 <= workers <= 2:
        raise ValueError('Use one or two bounded batch workers')
    ids = sorted(ids)
    cache = output.parent / f'.{output.name}.pages'
    cache.mkdir(parents=True, exist_ok=True)
    rows, pages = [], []
    def batch_page(batch):
        digest = hashlib.sha256(json.dumps(batch).encode()).hexdigest()
        page = cache / f'{digest}.json'
        if page.exists():
            saved = json.loads(page.read_text())
            if saved['requested_ids'] != batch or saved['api'] != API:
                raise ValueError('GoaT batch cache identity changed')
        else:
            for attempt in range(6):
                request = urllib.request.Request(API, data=json.dumps({
                    'record_ids': batch, 'result': 'taxon',
                }).encode(), headers={'Content-Type': 'application/json',
                                      'User-Agent': 'ARK-IV-provenance/1.0'})
                try:
                    with urllib.request.urlopen(request, timeout=120) as response:
                        body = json.load(response)
                    normalize_batch(body, batch)
                    break
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                    if attempt == 5:
                        raise
                    time.sleep(min(30, 2 ** attempt))
            saved = {'api': API, 'requested_ids': batch, 'fetched_at': iso_now(), 'body': body}
            atomic_json(page, saved)
        return normalize_batch(saved['body'], batch), {
            'file': str(page.resolve()), 'sha256': sha256(page),
            'fetched_at': saved['fetched_at'],
        }

    batches = [ids[offset:offset + batch_size] for offset in range(0, len(ids), batch_size)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for batch_rows, page in pool.map(batch_page, batches):
            rows.extend(batch_rows)
            pages.append(page)
            print(f'GoaT raw provenance: {len(rows)}/{len(ids)}', flush=True)
    result = {'schema_version': 1, 'api': API, 'goat_snapshot_sha256': snapshot_sha256,
              'generated_at': iso_now(), 'pages': pages, 'rows': rows}
    atomic_json(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ids', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--goat-snapshot-sha256', required=True)
    parser.add_argument('--batch-size', type=int, default=500)
    args = parser.parse_args()
    ids = json.loads(args.ids.read_text())
    if not isinstance(ids, list) or any(not isinstance(x, str) or not x for x in ids):
        raise ValueError('IDs must be a JSON array of nonempty strings')
    fetch(ids, args.output, args.goat_snapshot_sha256, batch_size=args.batch_size)


if __name__ == '__main__':
    main()
