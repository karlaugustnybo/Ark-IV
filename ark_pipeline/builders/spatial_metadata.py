"""Recover source-scoped metadata without conflating spatial and assessed IDs."""
from __future__ import annotations

import csv
import io
import json
import struct
import zipfile
from collections import defaultdict
from pathlib import Path

from ark_pipeline.cli.sources_fetch_iucn_details import positive_id
from ark_pipeline.runtime.provenance import sha256


def dbf_rows(stream):
    header = stream.read(32)
    if len(header) != 32:
        raise ValueError('Truncated DBF header')
    count = struct.unpack('<I', header[4:8])[0]
    header_len, row_len = struct.unpack('<HH', header[8:12])
    descriptors = stream.read(header_len - 32)
    fields, offset = [], 1
    for index in range(0, len(descriptors) - 1, 32):
        field = descriptors[index:index + 32]
        name = field[:11].split(b'\0')[0].decode('ascii').lower()
        length = field[16]
        fields.append((name, offset, length))
        offset += length
    if offset != row_len:
        raise ValueError('Invalid DBF row length')
    for _ in range(count):
        raw = stream.read(row_len)
        if len(raw) != row_len:
            raise ValueError('Truncated DBF row')
        if raw[:1] == b'*':
            continue
        yield {name: raw[start:start + length].decode('utf-8', errors='replace').strip()
               for name, start, length in fields}


def collect_spatial_metadata(root: Path, targets: set[str]) -> list[dict]:
    """Read only authoritative row attributes; never infer realms from filenames.

    Conflicting names/families/categories are left empty and recorded. Realm
    membership is the union of affirmative polygon flags, not a negative claim
    when a source row omits a flag. No identity joins by scientific name occur.
    """
    values = defaultdict(lambda: defaultdict(set))
    manifest_path = root / "acquisition/current.json"
    manifest_hash = sha256(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    for source in ('iucn-spatial', 'iucn-spatial-tables'):
        registration = manifest.get('sources', {}).get(source, {})
        if registration.get('validation_status', 'passed') != 'passed':
            raise ValueError('Spatial source is not validated')
        for item in registration.get('files', []):
            path = root / item['path']
            if path.stat().st_size != item['bytes'] or sha256(path) != item['sha256']:
                raise ValueError(f'Registered spatial source changed: {path}')
            # Missing registered archives must fail instead of silently publishing
            # an incomplete metadata recovery.
            with zipfile.ZipFile(path) as archive:
                for member in archive.namelist():
                    if member.lower().endswith('.dbf'):
                        with archive.open(member) as stream:
                            _collect(dbf_rows(stream), targets, values, item['path'], member)
                    elif member.lower().endswith('.csv'):
                        with archive.open(member) as stream:
                            rows = csv.DictReader(io.TextIOWrapper(stream, encoding='utf-8-sig'))
                            _collect(rows, targets, values, item['path'], member)
            if path.stat().st_size != item['bytes'] or sha256(path) != item['sha256']:
                raise ValueError(f'Registered spatial source changed: {path}')
            print(f"Metadata scanned {item['logical_name']}", flush=True)
    if sha256(manifest_path) != manifest_hash:
        raise ValueError("Spatial source registration changed during metadata recovery")
    result = []
    for sid, fields in sorted(values.items()):
        conflicts = [field for field in ('sci_name', 'family', 'category') if len(fields[field]) > 1]
        def unique(field):
            return next(iter(fields[field])) if len(fields[field]) == 1 else None
        result.append(dict(gbif_accepted_id=sid, species_name=unique('sci_name'),
                           family=unique('family'), source_category=unique('category'),
                           systems=sorted(fields['systems']),
                           provenance=json.dumps(sorted(fields['provenance'])),
                           conflicts=json.dumps(conflicts)))
    return result


def _collect(rows, targets, values, path, member):
    for row in rows:
        sid = str(row.get('id_no', '')).strip().removesuffix('.0')
        if not sid:
            continue
        sid = str(positive_id(sid))
        if sid not in targets:
            continue
        record = values[sid]
        for field in ('sci_name', 'family', 'category'):
            value = str(row.get(field, '') or '').strip()
            if value:
                record[field].add(value)
        for flag, system in (('marine', 'Marine'), ('terrestria', 'Terrestrial'), ('freshwater', 'Freshwater')):
            if str(row.get(flag, '')).strip().lower() in ('1', 'true', 't', 'yes'):
                record['systems'].add(system)
        record['provenance'].add(f'{path}::{member}')
