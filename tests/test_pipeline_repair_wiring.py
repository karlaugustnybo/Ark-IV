"""Metadata plan checks without provider, database or cloud operations."""

import tempfile
import unittest
from pathlib import Path

from ark_pipeline.builders.iucn_metadata import evidence_identity
from ark_pipeline.cli.serving_prepare import parser, preparation_plan


class MetadataWiringTests(unittest.TestCase):
    def test_local_options_reach_metadata_subprocess(self):
        with tempfile.TemporaryDirectory() as folder:
            intake = Path(folder) / "intake"
            evidence = Path(folder) / "reviewed.json"
            args = parser().parse_args(["--iucn-details-dir", str(intake), "--iucn-metadata-mode", "cache-only", "--reviewed-evidence-path", str(evidence)])
            _, env = preparation_plan(args)
            self.assertEqual(env["GLOBAL_IUCN_DETAILS_DIR"], str(intake.resolve()))
            self.assertEqual(env["GLOBAL_IUCN_METADATA_MODE"], "cache-only")
            self.assertEqual(env["GLOBAL_IUCN_REVIEWED_EVIDENCE_PATH"], str(evidence.resolve()))
            missing = evidence_identity(evidence)
            evidence.write_text('{"entries": []}')
            present = evidence_identity(evidence)
            evidence.write_text('{"entries": [], "fixture": true}')
            self.assertNotEqual(missing, present)
            self.assertNotEqual(present, evidence_identity(evidence))
