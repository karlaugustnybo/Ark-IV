"""Exercise the release guard against synthetic Git indexes, never source data."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_release as guard

# Deliberately incomplete marker: no private key material or usable credentials.
SYNTHETIC_SECRET = b"-----BEGIN " + b"PRIVATE KEY-----\nSYNTHETIC NOT A KEY\n"


class IndexedReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.patch = patch.object(guard, "ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.git("init", "--quiet")
        for name in guard.REQUIRED_FILES:
            self.write(name, b"safe synthetic release document\n")
        self.git("add", "--", *guard.REQUIRED_FILES)

    def git(self, *arguments):
        return subprocess.check_output(("git", *arguments), cwd=self.root)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def errors(self):
        return guard.index_errors()[0]

    def test_indexed_secret_is_detected_after_safe_unstaged_replacement(self):
        self.write("notes.md", SYNTHETIC_SECRET)
        self.git("add", "--", "notes.md")
        self.write("notes.md", b"safe unstaged replacement")
        self.assertTrue(any("possible credential" in error for error in self.errors()))

    def test_safe_index_ignores_unsafe_unstaged_version(self):
        self.write("notes.md", b"safe staged version")
        self.git("add", "--", "notes.md")
        self.write("notes.md", SYNTHETIC_SECRET)
        self.assertEqual(self.errors(), [])
        self.assertEqual(guard.candidate_files(), [])

    def test_missing_worktree_file_still_scans_index(self):
        path = self.write("notes.md", SYNTHETIC_SECRET)
        self.git("add", "--", "notes.md")
        path.unlink()
        (self.root / "LICENSE").unlink()
        errors = self.errors()
        self.assertTrue(any("possible credential" in error for error in errors))
        self.assertFalse(any("missing required" in error for error in errors))

    def test_untracked_required_document_cannot_satisfy_index(self):
        self.git("rm", "--cached", "--", "LICENSE")
        self.assertTrue(any("missing required indexed" in error for error in self.errors()))

    def test_indexed_symlinks_rejected_for_existing_and_missing_targets(self):
        for target in ("LICENSE", "absent"):
            with self.subTest(target=target):
                name = "link-" + target
                (self.root / name).symlink_to(target)
                self.git("add", "--", name)
                self.assertTrue(any(name in error and "120000" in error for error in self.errors()))

    def test_unresolved_index_fails_closed(self):
        oid = self.git("hash-object", "--", "LICENSE").decode().strip()
        subprocess.run(
            ("git", "update-index", "--index-info"), cwd=self.root,
            input=f"100644 {oid} 1\tconflicted.md\n".encode(), check=True,
        )
        self.assertTrue(any("unresolved index" in error for error in self.errors()))

    def test_attribution_uses_index_in_both_directions(self):
        name = guard.ATTRIBUTION_MANIFEST
        safe = json.dumps({"frameworks": [{"id": "synthetic", "status": "ready",
                                           "license": "CC0", "source_url": "https://example.org"}]}).encode()
        unsafe = json.dumps({"frameworks": [{"id": "synthetic", "status": "ready"}]}).encode()
        self.write(name, unsafe)
        self.git("add", "--", name)
        self.write(name, safe)
        self.assertTrue(any("lacks license" in error for error in self.errors()))
        self.git("add", "--", name)
        self.write(name, unsafe)
        self.assertEqual(self.errors(), [])

    def test_untracked_candidates_are_assessed_separately(self):
        self.write("untracked.md", SYNTHETIC_SECRET)
        (self.root / "untracked-link").symlink_to("absent")
        self.assertEqual(self.errors(), [])
        errors = guard.worktree_errors(guard.candidate_files())
        self.assertTrue(any("possible credential" in error for error in errors))
        self.assertTrue(any("symlinks" in error for error in errors))

    def test_large_allowed_blob_is_scanned(self):
        self.write("large.md", b"x" * (5 * 1024 * 1024 + 1) + SYNTHETIC_SECRET)
        self.git("add", "--", "large.md")
        self.assertTrue(any("possible credential" in error for error in self.errors()))

    def test_restricted_reviewed_evidence_is_blocked_even_if_force_added(self):
        self.write(guard.PRIVATE_EVIDENCE_PATH, b'{"synthetic": true}')
        self.git("add", "--", guard.PRIVATE_EVIDENCE_PATH)
        self.assertTrue(any("restricted reviewed evidence" in error for error in self.errors()))


class BoundedCredentialTests(unittest.TestCase):
    def test_patterns_across_chunk_boundary(self):
        tokens = [SYNTHETIC_SECRET, b"AKIA" + b"A" * 16,
                  b"ghp_" + b"a" * 36, b"sk-" + b"a" * 32]
        for token in tokens:
            with self.subTest(prefix=token[:4]):
                stream = io.BytesIO(b"x" * (guard.SCAN_CHUNK_BYTES - 2) + token)
                self.assertTrue(guard.contains_secret(stream))

    def test_large_safe_asset_is_allowed(self):
        stream = io.BytesIO(b"x" * (5 * 1024 * 1024 + 1))
        self.assertEqual(guard.content_errors("synthetic.svg", stream.getbuffer().nbytes, stream), [])


if __name__ == "__main__":
    unittest.main()
