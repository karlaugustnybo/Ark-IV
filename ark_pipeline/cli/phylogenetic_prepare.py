"""Build optional mammal or complete-tree enrichment from checked identities."""

from __future__ import annotations

import argparse
import json
from importlib.metadata import distribution
from pathlib import Path

from ark_pipeline.builders.phylogenetic import build_generation
from ark_pipeline.cli.phylogenetic_gap import run as prepare_pilot
from ark_pipeline.edge import load_ncbi_merges, tip_identifiers
from ark_pipeline.edge_matching import normalize_name


def normalize_inputs(species, baseline):
    def clean(frame):
        # JSON round-trip removes NumPy scalars and NaN before content identity.
        return json.loads(frame.to_json(orient='records'))

    def genus_key(family, genus):
        family, genus = normalize_name(family), normalize_name(genus)
        return f'{family}/{genus}' if family and genus else None

    evidence = [{**r, 'genomic_transfer_safe': r['tree_join_status'] == 'linked',
                 'reviewed_genus_key': genus_key(r.get('family'), r.get('genus'))}
                for r in clean(baseline)]
    assessed = [{**r, 'genomic_transfer_safe': r['current_goat_join_status'] == 'linked',
                 'reviewed_genus_key': genus_key(r.get('current_ncbi_family'), r.get('current_ncbi_genus'))}
                for r in clean(species)]
    return assessed, evidence


def build(args):
    import tomllib

    config = tomllib.loads(args.config.read_text())
    origin = json.loads(distribution('dated-complete-tree').read_text('direct_url.json') or '{}')
    if origin.get('vcs_info', {}).get('commit_id') != config['engine_commit']:
        raise ValueError('Installed EDGE engine differs from configured pin')
    if getattr(args, 'scope', 'mammals') == 'global':
        from ark_pipeline.cli.phylogenetic_global import build_global

        return build_global(args)
    args.prepare_only = True
    args.skip_cached_review = True
    if args.input_dir is None and args.ott_index is None:
        raise ValueError("Registered-input mode requires --ott-index")
    tree, species, baseline, sources = prepare_pilot(args)

    assessed, evidence = normalize_inputs(species, baseline)
    tip_identifiers(tree, Path(sources["ott-taxonomy"]["path"]),
                    load_ncbi_merges(Path(sources["ncbi_merged"]["path"])))
    return build_generation(tree, evidence, assessed, args.output_root, sources=sources,
                            scope='Mammalia', root=config['clade'], risk_samples=args.risk_samples, seed=args.seed)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--crosswalk', type=Path, required=True)
    p.add_argument('--input-dir', type=Path, help='Legacy pinned input directory; omit to use registered dated-tree/OTT sources')
    p.add_argument('--ott-index', type=Path, help='Checked OTT index with companion receipt')
    p.add_argument('--tree-tips', type=Path, help='Relocated checked global tip index; checksum must match the identity receipt')
    p.add_argument('--links-dir', type=Path, required=True)
    p.add_argument('--baseline-review', type=Path)
    p.add_argument('--config', type=Path, default=Path('config/edge_prototype.toml'))
    p.add_argument('--output-root', type=Path, required=True)
    p.add_argument('--risk-samples', type=int, default=128)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--scope', choices=('mammals', 'global'), default='mammals',
                   help='Global retains every released tree occurrence and all assessed targets; unknown identities stay unavailable')
    return p


def main(argv=None):
    print(json.dumps(build(parser().parse_args(argv)), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
