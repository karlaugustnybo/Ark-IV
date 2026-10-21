from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.config import Settings
from backend.coverage_profiles import (
    CoverageProfileMiddleware,
    RequestSettings,
    current_profile,
    current_settings,
    load_profiles,
)
from backend.db import get_pool


class CoverageProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_requests_keep_all_data_and_sql_pools_in_the_same_profile(self):
        profiles = {
            key: Settings(
                _env_file=None,
                export_dir=Path("/" + key),
                tile_dir=Path("/" + key),
                map_metadata_path=Path("/" + key + "/map-metadata.json"),
                res7_parts_dir=Path("/" + key + "/cells"),
                res7_aggregate_parts_dir=Path("/" + key + "/metrics"),
                spatial_analysis_generation_path=Path('/' + key + '/analysis'),
                membership_generation_path=Path('/' + key + '/memberships'),
            )
            for key in ("resident", "with-passage")
        }
        state = SimpleNamespace(
            coverage_settings=profiles,
            coverage_pools={"resident": "resident-db", "with-passage": "passage-db"},
        )

        async def handler(scope, receive, send):
            await asyncio.sleep(0)
            settings = RequestSettings()
            result = {
                "selected": current_profile.get(),
                "database": get_pool(state),
                "tiles": str(settings.tile_dir),
                "metadata": str(settings.map_metadata_path),
                "cells": str(settings.res7_parts_dir),
                "metrics": str(settings.res7_aggregate_parts_dir),
                "analysis": await asyncio.to_thread(lambda: str(RequestSettings().spatial_analysis_generation_path)),
                "memberships": str(settings.membership_generation_path),
            }
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": json.dumps(result).encode()})

        middleware = CoverageProfileMiddleware(app=handler)

        async def request(query):
            messages = []

            async def send(message):
                messages.append(message)

            async def receive():
                return {"type": "http.request", "body": b""}

            await middleware(
                {
                    "type": "http",
                    "query_string": query.encode(),
                    "app": SimpleNamespace(state=state),
                },
                receive,
                send,
            )
            return messages[0]["status"], json.loads(messages[1]["body"])

        resident, passage = await asyncio.gather(
            request("coverage_profile=resident"), request("coverage_profile=with-passage")
        )
        self.assertEqual(resident[1]["database"], "resident-db")
        self.assertEqual(passage[1]["database"], "passage-db")
        for name in ("tiles", "metadata", "cells", "metrics", "analysis", "memberships"):
            self.assertTrue(resident[1][name].startswith("/resident"))
            self.assertTrue(passage[1][name].startswith("/with-passage"))
        self.assertEqual(current_profile.get(), "resident")
        self.assertIsNone(current_settings.get())
        self.assertEqual((await request("coverage_profile=unknown"))[0], 400)
        self.assertEqual(
            (await request("coverage_profile=resident&coverage_profile=with-passage"))[0], 400
        )
        self.assertEqual((await request("coverage_profile="))[0], 400)

    async def test_unbuilt_passage_never_silently_falls_back_to_resident(self):
        state = SimpleNamespace(coverage_settings={"resident": Settings(_env_file=None)})

        async def handler(*args):
            self.fail("Unsupported profile reached a data handler")

        messages = []

        async def send(message):
            messages.append(message)

        middleware = CoverageProfileMiddleware(app=handler)
        await middleware(
            {
                "type": "http",
                "query_string": b"coverage_profile=with-passage",
                "app": SimpleNamespace(state=state),
            },
            None,
            send,
        )
        self.assertEqual(messages[0]["status"], 409)

    def test_profile_registration_requires_matching_seasons_and_separate_databases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = root / "metadata.json"
            metadata.write_text(json.dumps({"spatial_semantics": {"seasonality": [1, 2, 3, 5]}}))
            manifest = root / "profiles.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "profiles": {
                            key: {
                                "serving_config": key + ".json",
                                "database_url_env": key.upper().replace("-", "_") + "_DB",
                            }
                            for key in ("resident", "with-passage")
                        },
                    }
                )
            )
            settings = Settings(_env_file=None, coverage_profiles_path=manifest,
                spatial_analysis_generation_path=root / 'default-analysis',
                membership_generation_path=root / 'default-memberships')
            config = {"environment": {"MAP_METADATA_PATH": str(metadata), "EXPORT_DIR": str(root)}}
            with (
                patch("ark_pipeline.cli.map_serving.validate_config", return_value=config),
                patch("ark_pipeline.cli.map_serving.reject_benchmark_bundle"),
                patch.dict(
                    "os.environ",
                    {"RESIDENT_DB": "postgresql://a", "WITH_PASSAGE_DB": "postgresql://b"},
                ),
            ):
                with self.assertRaisesRegex(ValueError, "migration-inclusive"):
                    load_profiles(settings)
            resident = root / "resident-metadata.json"
            resident.write_text(json.dumps({"spatial_semantics": {"seasonality": [1, 2, 3, 5]}}))
            metadata.write_text(json.dumps({"spatial_semantics": {"seasonality": [1, 2, 3, 4, 5]}}))

            def configuration(path):
                return {
                    "environment": {
                        "EXPORT_DIR": str(root),
                        **({'SPATIAL_ANALYSIS_GENERATION_PATH': str(root / 'resident-analysis')}
                           if path.stem == 'resident' else {}),
                        "MAP_METADATA_PATH": str(
                            metadata if path.stem == "with-passage" else resident
                        )
                    }
                }

            with (
                patch("ark_pipeline.cli.map_serving.validate_config", side_effect=configuration),
                patch("ark_pipeline.cli.map_serving.reject_benchmark_bundle"),
                patch.dict(
                    "os.environ",
                    {"RESIDENT_DB": "postgresql://a", "WITH_PASSAGE_DB": "postgresql://b"},
                ),
            ):
                profiles = load_profiles(settings)
                self.assertEqual(set(profiles), {"resident", "with-passage"})
                self.assertEqual(profiles['resident'].spatial_analysis_generation_path, root / 'resident-analysis')
                self.assertIsNone(profiles['with-passage'].spatial_analysis_generation_path)
                self.assertTrue(all(p.membership_generation_path is None for p in profiles.values()))
                with patch.dict("os.environ", {"WITH_PASSAGE_DB": "postgresql://a"}):
                    with self.assertRaisesRegex(ValueError, "separate serving databases"):
                        load_profiles(settings)
