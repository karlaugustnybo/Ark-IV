import json
import tempfile
import time
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from ark_pipeline.cli.sources_download_iucn import IucnApi, read_iucn_token
from ark_pipeline.cli.sources_fetch_iucn_details import fetch_details, positive_id


class FakeApi:
    def __init__(self, failure=None, changed=False):
        self.calls = []
        self.failure = failure
        self.changed = changed

    def get(self, endpoint):
        self.calls.append(endpoint)
        if endpoint == "information/red_list_version":
            return {
                "red_list_version": "2026-2" if self.changed and len(self.calls) > 1 else "2026-1"
            }, {}
        if self.failure:
            raise urllib.error.HTTPError(
                "https://api.iucnredlist.org/", self.failure, "error", {}, None
            )
        return {"taxon": {"sis_id": 999, "scientific_name": "Candidate species"}}, {}


class IucnDetailTests(unittest.TestCase):
    def test_identity_validation_does_not_truncate_or_coerce_booleans(self):
        for value in (True, False, 1.5, 0, -1, "1.5", "-1", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                positive_id(value)
        self.assertEqual(positive_id("123"), 123)
        self.assertEqual(positive_id(123), 123)

    def test_parallel_requests_keep_one_shared_request_interval(self):
        starts = []

        class Response:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return b"{}"

        def response(*args, **kwargs):
            starts.append(time.monotonic())
            return Response()

        api = IucnApi("test-value", "https://api.iucnredlist.org/api/v4", 0.02)
        with (
            patch("urllib.request.urlopen", side_effect=response),
            ThreadPoolExecutor(max_workers=4) as executor,
        ):
            list(executor.map(lambda sid: api.get(f"taxa/sis/{sid}"), range(4)))
        self.assertEqual(len(starts), 4)
        self.assertTrue(all(second - first >= 0.018 for first, second in zip(starts, starts[1:])))

    def test_current_and_legacy_token_names_are_supported_without_exporting_dotenv(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("dotenv.dotenv_values", return_value={"IUCN_REDLIST_API_KEY": "test-value"}),
        ):
            self.assertEqual(read_iucn_token(), "test-value")
        with (
            patch.dict("os.environ", {"IUCN_REDLIST_KEY": "legacy-value"}, clear=True),
            patch("dotenv.dotenv_values", return_value={}),
        ):
            self.assertEqual(read_iucn_token(), "legacy-value")

    def test_latest_detail_fetch_requires_matching_taxon_and_global_scope(self):
        api = FakeApi()
        original = api.get

        def get(endpoint):
            if endpoint == "taxa/sis/1":
                api.calls.append(endpoint)
                return {
                    "taxon": {"sis_id": 1},
                    "assessments": [
                        {
                            "sis_taxon_id": sid,
                            "assessment_id": aid,
                            "latest": True,
                            "scopes": [{"code": scope}],
                        }
                        for sid, aid, scope in [(1, 10, "1"), (1, 11, "2"), (2, 12, "1")]
                    ],
                }, {}
            return original(endpoint)

        api.get = get
        with tempfile.TemporaryDirectory() as folder:
            fetch_details(api, {"sis_ids": [1]}, Path(folder), include_latest_global=True)
        self.assertIn("assessment/10", api.calls)
        self.assertNotIn("assessment/11", api.calls)
        self.assertNotIn("assessment/12", api.calls)

    def test_preserves_requested_and_returned_identity_without_name_based_merge(self):
        with tempfile.TemporaryDirectory() as folder:
            api = FakeApi()
            queue = {"sis_ids": [123, 123], "assessment_ids": [456]}
            report = fetch_details(api, queue, Path(folder))
            self.assertEqual(report["status"], "retrieved")
            saved = json.loads((Path(folder) / "taxa/sis/123.json").read_text())
            self.assertEqual(saved["endpoint"], "taxa/sis/123")
            self.assertEqual(saved["body"]["taxon"]["sis_id"], 999)
            self.assertEqual(api.calls.count("taxa/sis/123"), 1)
            second = FakeApi()
            fetch_details(second, queue, Path(folder))
            self.assertEqual(second.calls, ["information/red_list_version"] * 2)

    def test_missing_id_is_explicit_and_authentication_errors_abort(self):
        for status in (404, 401):
            with tempfile.TemporaryDirectory() as folder:
                api = FakeApi(failure=status)
                if status == 401:
                    with self.assertRaises(urllib.error.HTTPError):
                        fetch_details(api, {"sis_ids": [123]}, Path(folder))
                    self.assertEqual(
                        json.loads((Path(folder) / "report.json").read_text())["status"],
                        "incomplete",
                    )
                else:
                    report = fetch_details(api, {"sis_ids": [123]}, Path(folder))
                    self.assertEqual(report["records"][0]["status"], "not_found")

    def test_release_change_never_reports_success(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "release changed"):
                fetch_details(FakeApi(changed=True), {"sis_ids": [123]}, Path(folder))
