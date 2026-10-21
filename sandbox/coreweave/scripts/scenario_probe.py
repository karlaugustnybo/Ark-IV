"""Private real-generation API timings; never bind a port or load a database."""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from litestar import Litestar
from litestar.testing import TestClient

from ark_pipeline.runtime.provenance import atomic_json
from backend import analysis, dna_analysis


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dna-generation', type=Path, required=True)
    parser.add_argument('--phylogenetic-generation', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a fresh private output')
    settings = SimpleNamespace(dna_generation_path=args.dna_generation,
                               phylogenetic_generation_path=args.phylogenetic_generation)
    report = {'scope': 'Actual private generations, in-process HTTP, page 100; no browser/network latency',
              'requests': []}
    dna_analysis.completed_dna_scenario.cache_clear()
    dna_analysis.load_dna_generation.cache_clear()
    analysis.analytical_engine.cache_clear()
    with patch.object(dna_analysis, 'RequestSettings', return_value=settings), \
            patch.object(analysis, 'RequestSettings', return_value=settings), \
            TestClient(Litestar([dna_analysis.dna_evidence_scenario,
                                analysis.phylogenetic_scenario])) as client:
        for endpoint in ('dna', 'phylogenetic'):
            for rule in ('website', 'any-assembly', 'taxon-quality-proxy'):
                url = f'/api/analysis/{endpoint}?rule={rule}&per_page=100'
                def request(url=url):
                    start = time.perf_counter()
                    response = client.get(url)
                    seconds = time.perf_counter() - start
                    assert response.status_code == 200, response.text
                    data = response.json()
                    assert len(data['rows']) == min(100, data['total'])
                    return {'seconds': seconds, 'json_bytes': len(response.content),
                            **{key: value for key, value in data.items() if key not in ('rows', 'metadata')}}
                cold, warm = request(), request()
                assert cold['scenario_id'] == warm['scenario_id']
                report['requests'].append({'endpoint': endpoint, 'rule': rule,
                                           'cold': cold, 'warm': warm})
            with ThreadPoolExecutor(max_workers=4) as executor:
                start = time.perf_counter()
                concurrent = list(executor.map(lambda _: request(), range(4)))
                seconds = time.perf_counter() - start
            report[endpoint + '_four_concurrent_warm'] = {'seconds': seconds, 'requests': concurrent}
            assert client.get(f'/api/analysis/{endpoint}?per_page=101').status_code == 400
            assert client.get(f'/api/analysis/{endpoint}?expected_scenario=stale').status_code == 409
    report['status'] = 'passed'
    atomic_json(args.output, report)
    print(json.dumps({'status': 'passed', 'output': str(args.output)}))


if __name__ == '__main__':
    main()
