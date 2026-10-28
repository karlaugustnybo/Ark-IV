"""Offline regression coverage for acquisition repair and upstream discovery."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ark_pipeline.cli import sources_acquire as acquire
from ark_pipeline.cli import sources_sync as sync


class SourceUpdateTests(unittest.TestCase):
    def test_unavailable_optional_taxonomy_monitor_does_not_block_required_acquisition(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = acquire.Source({"id": "fixture", "access": "public-download"})
            optional = acquire.Source({"id": "ott-taxonomy", "update_policy": "pinned"})
            catalogue = acquire.Catalogue(
                {source.id: source, optional.id: optional},
                {"authorized": {"required_sources": [source.id]}},
            )
            warning = sync.SourceStatus(
                "ott-taxonomy-release", "check-failed", detail="unavailable"
            )
            with (
                patch.object(
                    sync,
                    "run_public_updates",
                    return_value=[sync.SourceStatus(source.id, "current")],
                ),
                patch.object(sync, "check_upstream_releases", return_value=[warning]),
            ):
                statuses, complete = sync.synchronize(
                    Path(temporary), catalogue, mode="update", authorized=False
                )
            self.assertTrue(complete)
            self.assertIn(warning, statuses)

    def test_truncated_http_download_resumes_only_a_validated_range(self):
        class Response(io.BytesIO):
            def __init__(self, body, status=200, **headers):
                super().__init__(body)
                self.status = status
                self.headers = headers

            def geturl(self):
                return "https://fixture.invalid/archive"

        for mode in ("resume", "changed", "bad-range"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "archive.zip"
                source = acquire.Source(
                    {"id": "fixture", "download_url": "https://fixture.invalid/archive"}
                )
                with (
                    patch.object(
                        acquire.urllib.request,
                        "urlopen",
                        return_value=Response(b"abc", **{"Content-Length": "6", "ETag": '"v1"'}),
                    ),
                    self.assertRaisesRegex(ValueError, "Incomplete download"),
                ):
                    acquire.download(source, None, output)
                self.assertEqual(output.read_bytes(), b"abc")
                if mode == "changed":
                    response = Response(b"newnew", **{"Content-Length": "6", "ETag": '"v2"'})
                else:
                    response = Response(
                        b"def",
                        status=206,
                        **{
                            "Content-Length": "3",
                            "ETag": '"v1"',
                            "Content-Range": "bytes 0-2/6"
                            if mode == "bad-range"
                            else "bytes 3-5/6",
                        },
                    )
                with patch.object(
                    acquire.urllib.request, "urlopen", return_value=response
                ) as request:
                    if mode == "bad-range":
                        with self.assertRaisesRegex(ValueError, "Invalid or changed HTTP range"):
                            acquire.download(source, None, output)
                        self.assertEqual(output.read_bytes(), b"abc")
                    else:
                        acquire.download(source, None, output)
                        self.assertEqual(
                            output.read_bytes(), b"newnew" if mode == "changed" else b"abcdef"
                        )
                    self.assertEqual(request.call_args.args[0].get_header("Range"), "bytes=3-")
                    self.assertEqual(request.call_args.args[0].get_header("If-range"), '"v1"')

    def test_stale_download_merges_other_sources_but_rejects_competing_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = acquire.load_manifest(root)
            acquire.activate(root, base, {"tree": {"release": "tree-1"}})
            acquire.activate(root, base, {"goat": {"release": "goat-1"}})
            selected = acquire.load_manifest(root)
            self.assertEqual(set(selected["sources"]), {"tree", "goat"})
            with self.assertRaisesRegex(ValueError, "active selection changed"):
                acquire.activate(root, base, {"goat": {"release": "goat-2"}})
            self.assertEqual(acquire.load_manifest(root), selected)

    def fixture(self, root: Path, *, policy: str = "monthly"):
        remote = root / "remote.txt"
        remote.write_text("source data\n")
        source = acquire.Source(
            {
                "id": "fixture",
                "access": "public-download",
                "update_policy": policy,
                "interval_days": 28,
                "release": "fixture-1",
                "download_url": remote.as_uri(),
                "output_file": "input.txt",
            }
        )
        catalogue = acquire.Catalogue(
            {source.id: source}, {"authorized": {"required_sources": [source.id]}}
        )
        args = argparse.Namespace(root=root, source=[source.id], force=False, dry_run=False)
        return source, catalogue, args

    def update(self, args, catalogue):
        with contextlib.redirect_stdout(io.StringIO()):
            acquire.update_public(args, catalogue)

    def test_missing_pinned_file_is_reacquired_without_http_validators(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, catalogue, args = self.fixture(root, policy="pinned-discontinued")
            self.update(args, catalogue)
            record = acquire.load_manifest(root)["sources"][source.id]
            selected = root / record["files"][0]["path"]
            selected.unlink()
            with patch.object(acquire, "download", wraps=acquire.download) as download:
                self.update(args, catalogue)
            self.assertIsNone(download.call_args.args[1])
            self.assertEqual(selected.read_text(), "source data\n")

    def test_damaged_selected_file_never_reports_current_or_overwrites_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, catalogue, args = self.fixture(root)
            self.update(args, catalogue)
            manifest = acquire.load_manifest(root)
            selected = root / manifest["sources"][source.id]["files"][0]["path"]
            selected.write_text("damaged")
            with self.assertRaisesRegex(ValueError, "Immutable snapshot collision"):
                self.update(args, catalogue)
            self.assertEqual(acquire.load_manifest(root), manifest)
            self.assertEqual(selected.read_text(), "damaged")

    def test_not_modified_records_check_time_and_preserves_acquisition_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, catalogue, args = self.fixture(root)
            self.update(args, catalogue)
            manifest = acquire.load_manifest(root)
            old = {**manifest["sources"][source.id], "acquired_at": "2000-01-01T00:00:00Z"}
            acquire.activate(root, manifest, {source.id: old})
            with patch.object(acquire, "download", return_value=None) as download:
                self.update(args, catalogue)
                self.update(args, catalogue)
            self.assertEqual(download.call_count, 1)
            record = acquire.load_manifest(root)["sources"][source.id]
            self.assertEqual(record["acquired_at"], old["acquired_at"])
            self.assertIn("checked_at", record)
            self.assertEqual(record["files"], old["files"])
            (root / record["files"][0]["path"]).unlink()
            with (
                patch.object(acquire, "download", return_value=None),
                self.assertRaisesRegex(ValueError, "without valid local files"),
            ):
                self.update(args, catalogue)

    def release_catalogue(self, root: Path):
        geo = root / "geo.toml"
        geo.write_text(
            'schema_version = 1\nrelease = "geo-1"\n[[files]]\nlogical_name = "geo.zip"\n[[countries]]\niso = "AAA"\nboundary_id = "AAA-1"\n'
        )
        hydro = root / "hydro.toml"
        hydro.write_text(
            'schema_version = 1\nrelease = "v1c"\n[[files]]\nlogical_name = "hybas_aa_lev01-12_v1c.zip"\ndownload_url = "https://fixture.invalid/hydrobasins/standard/hybas_aa_lev01-12_v1c.zip"\n'
        )
        sources = {
            name: acquire.Source(
                {
                    "id": name,
                    "inventory_file": str(path),
                    "release_check_url": f"https://fixture.invalid/{name}",
                }
            )
            for name, path in [("geoboundaries-adm2", geo), ("hydrobasins", hydro)]
        }
        return acquire.Catalogue(sources, {})

    def test_provider_metadata_checks_detect_changes_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            catalogue = self.release_catalogue(Path(temporary))
            geo = [{"boundaryISO": "AAA", "boundaryID": "AAA-1"}]
            hydro = (
                '<a href="/hydrobasins/standard/hybas_aa_lev01-12_v1c.zip">download</a>'
                '<a href="/hydrobasins/pour_point/hybas_pour_lev01-12_v1_shp.zip">other product</a>'
            )
            for geo_body, hydro_body, expected in [
                (json.dumps(geo), hydro, ["current", "current"]),
                (
                    json.dumps([{**geo[0], "boundaryID": "AAA-2"}]),
                    hydro.replace("v1c", "v2"),
                    ["action-required", "action-required"],
                ),
                (
                    json.dumps(geo + geo),
                    "<html>maintenance</html>",
                    ["check-failed", "check-failed"],
                ),
            ]:
                with (
                    self.subTest(expected=expected),
                    patch.object(sync, "read_release_page", side_effect=[geo_body, hydro_body]),
                ):
                    self.assertEqual(
                        [row.status for row in sync.check_upstream_releases(catalogue)], expected
                    )

    def test_iucn_checks_every_inventory_without_fetching_catalogue(self):
        sources = {
            name: acquire.Source({"id": name, "release": release})
            for name, release in [
                ("iucn-red-list-tabular", "2099-1"),
                ("iucn-spatial", "2099-2"),
                ("hydrobasins-iucn-islands", "2023-08"),
            ]
        }
        catalogue = acquire.Catalogue(sources, {})
        with (
            patch("ark_pipeline.cli.sources_download_iucn.read_iucn_token", return_value="fixture"),
            patch.object(
                sync, "read_release_page", return_value='{"red_list_version": "2099-1"}'
            ) as request,
        ):
            statuses = sync.check_upstream_releases(catalogue)
            self.assertEqual(statuses[0].status, "action-required")
            self.assertEqual(request.call_count, 1)
            self.assertTrue(request.call_args.args[0].endswith("information/red_list_version"))
        with patch("ark_pipeline.cli.sources_download_iucn.read_iucn_token", return_value=None):
            self.assertEqual(sync.check_upstream_releases(catalogue)[0].status, "unchecked")
        with (
            patch("ark_pipeline.cli.sources_download_iucn.read_iucn_token", return_value="fixture"),
            patch.object(sync, "read_release_page", side_effect=OSError("provider unavailable")),
        ):
            self.assertEqual(sync.check_upstream_releases(catalogue)[0].status, "check-failed")

    def test_release_warning_survives_action_plan_without_stale_download_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            statuses = [
                sync.SourceStatus("iucn-release", "action-required", "2099-2", "new release")
            ]
            path = sync.write_action_plan(root, acquire.Catalogue({}, {}), statuses, False)
            action = json.loads(path.read_text())["actions"][0]
            self.assertEqual(action["release"], "2099-2")
            self.assertEqual(action["detail"], "new release")
            self.assertIn("inventory", action["action"])
            self.assertNotIn("files", action)

    def test_check_mode_is_read_only_and_reports_due_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, catalogue, args = self.fixture(root)
            self.update(args, catalogue)
            manifest = acquire.load_manifest(root)
            acquire.activate(
                root,
                manifest,
                {
                    source.id: {
                        **manifest["sources"][source.id],
                        "acquired_at": "2000-01-01T00:00:00Z",
                    }
                },
            )
            before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            output = io.StringIO()
            with (
                patch.object(sync, "load_catalogue", return_value=catalogue),
                patch.object(sync, "check_upstream_releases", return_value=[]),
                patch.object(
                    sync, "update_public", side_effect=AssertionError("must not download")
                ),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(sync.main(["check", "--root", str(root)]), 2)
            self.assertEqual(json.loads(output.getvalue())["sources"][0]["status"], "due")
            self.assertEqual(
                before, {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            )

    def test_update_exit_and_action_plan_preserve_release_check_outcomes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, catalogue, args = self.fixture(root)
            self.update(args, catalogue)
            manifest = acquire.load_manifest(root)
            for status, code in [("action-required", 2), ("check-failed", 2), ("unchecked", 0)]:
                warning = sync.SourceStatus("iucn-release", status, "2099-2", "review provider")
                with (
                    self.subTest(status=status),
                    patch.object(sync, "load_catalogue", return_value=catalogue),
                    patch.object(sync, "check_upstream_releases", return_value=[warning]),
                    patch.object(
                        acquire,
                        "download",
                        side_effect=AssertionError("valid input must not download"),
                    ),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(sync.main(["update", "--root", str(root)]), code)
                plan = json.loads((root / "acquisition/action-required.json").read_text())
                self.assertEqual(plan["complete"], code == 0)
                self.assertEqual(plan["actions"][0]["status"], status)
                self.assertEqual(acquire.load_manifest(root), manifest)

    def test_isolated_staging_preserves_other_partial_downloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, catalogue, args = self.fixture(root)
            previous = root / "acquisition/.staging/fixture/partial"
            previous.parent.mkdir(parents=True)
            previous.write_text("previous run")
            args.staging_root = root / "isolated"
            self.update(args, catalogue)
            self.assertEqual(previous.read_text(), "previous run")
            self.assertFalse((args.staging_root / "fixture").exists())


if __name__ == "__main__":
    unittest.main()
