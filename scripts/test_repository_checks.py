"""Exercise repository safeguards with synthetic strings and no filesystem writes."""

import unittest

from check_repository import inspect_text, path_is_private


class RepositoryChecks(unittest.TestCase):
    """Ensure forced private files, secrets and broken navigation are rejected."""

    def test_private_paths(self) -> None:
        for path in (".env", "x/.env.local", "data/events.sqlite3", ".cache/a.txt"):
            with self.subTest(path=path):
                self.assertTrue(path_is_private(path))
        self.assertFalse(path_is_private(".env.example"))
        self.assertFalse(path_is_private("tests/fake_upstream.py"))

    def test_credential_detection_is_redacted(self) -> None:
        synthetic = "ghp_" + "a" * 36
        result = inspect_text("config.txt", synthetic, set())
        self.assertEqual(result, ["credential_pattern"])
        self.assertNotIn(synthetic, str(result))

    def test_local_links(self) -> None:
        self.assertEqual(
            inspect_text("docs/a.md", "[b](../README.md)", {"README.md"}), []
        )
        self.assertEqual(
            inspect_text("docs/a.md", "[b](missing.md)", {"README.md"}),
            ["missing_local_link"],
        )


if __name__ == "__main__":
    unittest.main()
