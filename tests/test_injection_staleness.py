"""Regression tests for injection.check_staleness timestamp parsing.

Bug: check_staleness used datetime.fromisoformat() to parse a "%Y-%m-%dT%H:%M"
timestamp (no seconds). On Python < 3.11, fromisoformat rejects that format and
raises ValueError, which the except clause silently converts to stale=True.
This caused every managed block to be treated as stale on Python 3.9/3.10,
triggering endless re-injection on every hook call.

Fix: use datetime.strptime(ts, "%Y-%m-%dT%H:%M") which works on all Python
versions and exactly matches the format written by inject_managed_block.
"""

import datetime as _dt_module
import importlib.util
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "token-optimizer"
    / "scripts"
    / "injection.py"
)

spec = importlib.util.spec_from_file_location("token_optimizer_injection", MODULE_PATH)
injection = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(injection)


class InjectionStalenessTests(unittest.TestCase):
    def test_check_staleness_fresh_block_not_stale(self):
        """A block written moments ago must be reported as not stale."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            fname = f.name

        try:
            injection.inject_managed_block(fname, "TEST", "# advice")
            result = injection.check_staleness(fname, "TEST", max_age_hours=48)
            self.assertTrue(result["exists"])
            self.assertFalse(result["stale"], "A freshly-written block must not be stale")
            self.assertIsNotNone(result["age_hours"])
            self.assertLess(result["age_hours"], 1.0)
        finally:
            Path(fname).unlink(missing_ok=True)

    def test_check_staleness_survives_fromisoformat_failure(self):
        """Regression: on Python < 3.11 fromisoformat rejects HH:MM timestamps.

        Simulate that environment by replacing the injection module's `datetime`
        reference with a subclass whose fromisoformat always raises ValueError.
        Before the fix the fallback returned stale=True; after the fix strptime
        is used (inherited from the real datetime) so the result is stale=False.
        """
        # Subclass that simulates Python < 3.11 fromisoformat behavior.
        class _OldPythonDatetime(_dt_module.datetime):
            @classmethod
            def fromisoformat(cls, date_string):
                raise ValueError(
                    f"fromisoformat: unsupported format (Python < 3.11 sim): {date_string!r}"
                )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            fname = f.name

        try:
            injection.inject_managed_block(fname, "COACH", "# tip")

            # Patch the `datetime` name inside the injection module.
            with patch.object(injection, "datetime", _OldPythonDatetime):
                result = injection.check_staleness(fname, "COACH", max_age_hours=48)

            self.assertTrue(result["exists"])
            self.assertFalse(
                result["stale"],
                "A freshly-written block must not be stale even when "
                "datetime.fromisoformat is unavailable (Python < 3.11)",
            )
        finally:
            Path(fname).unlink(missing_ok=True)

    def test_check_staleness_old_block_is_stale(self):
        """A block whose timestamp is older than max_age_hours must be stale."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            fname = f.name
            # Write a block with a timestamp 72 hours in the past
            old_ts = "2020-01-01T00:00"
            f.write(
                "<!-- TOKEN_OPTIMIZER:OLDBLOCK -->\n"
                "# old advice\n"
                f"<!-- updated {old_ts} -->\n"
                "<!-- /TOKEN_OPTIMIZER:OLDBLOCK -->\n"
            )

        try:
            result = injection.check_staleness(fname, "OLDBLOCK", max_age_hours=48)
            self.assertTrue(result["exists"])
            self.assertTrue(result["stale"], "A block from years ago must be stale")
        finally:
            Path(fname).unlink(missing_ok=True)

    def test_check_staleness_missing_file_returns_not_exists(self):
        """A missing file must return exists=False, stale=False."""
        result = injection.check_staleness("/nonexistent/path/file.md", "ANY")
        self.assertFalse(result["exists"])
        self.assertFalse(result["stale"])

    def test_check_staleness_missing_section_returns_not_exists(self):
        """A file that doesn't contain the section must return exists=False."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write("# no managed blocks here\n")
            fname = f.name
        try:
            result = injection.check_staleness(fname, "MISSING")
            self.assertFalse(result["exists"])
            self.assertFalse(result["stale"])
        finally:
            Path(fname).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
