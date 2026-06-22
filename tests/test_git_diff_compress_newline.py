"""Regression test: _compress_git_diff must not insert an extra blank line
before the truncation summary.

Bug: line 294 of bash_compress.py used f"\\n... ({N} more lines...)" as the
appended element. When joined with "\\n".join(result_lines), that leading \\n
produced a double newline — a blank line — between the last diff line and the
summary. Every compressed git diff (>50 input lines) was affected.

Fix: remove the leading \\n from the format string so the join produces a
single \\n between the last content line and the summary.
"""

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "token-optimizer"
    / "scripts"
    / "bash_compress.py"
)

spec = importlib.util.spec_from_file_location("bash_compress", _SCRIPT_PATH)
bash_compress = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bash_compress)

import unittest


def _make_large_diff(n_lines: int) -> str:
    """Build a synthetic git diff with the given number of body lines."""
    header = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,40 +1,40 @@\n"
    )
    body = "\n".join(f"+line{i}" for i in range(n_lines))
    return header + body


class GitDiffCompressNewlineTest(unittest.TestCase):
    def test_no_blank_line_before_summary(self):
        """Regression: no blank line between last diff line and ... summary."""
        diff = _make_large_diff(55)  # 4 header + 55 body = 59 lines (>50 threshold)
        result = bash_compress._compress_git_diff(diff)
        lines = result.split("\n")

        # Find the truncation summary line
        summary_idx = next(
            (i for i, ln in enumerate(lines) if ln.startswith("... (")),
            None,
        )
        self.assertIsNotNone(summary_idx, "Truncation summary line not found in output")

        # The line immediately before the summary must not be blank.
        # Pre-fix: lines[summary_idx - 1] == "" (double newline from leading \\n).
        # Post-fix: lines[summary_idx - 1] is the last content line, not blank.
        prev_line = lines[summary_idx - 1]
        self.assertNotEqual(
            prev_line,
            "",
            "Blank line found before '...' summary — the leading \\n is still present.",
        )

    def test_summary_line_contains_counts(self):
        """The summary line must include addition and deletion counts."""
        diff = _make_large_diff(60)
        result = bash_compress._compress_git_diff(diff)
        self.assertIn("more lines", result)
        self.assertIn("+60/-0", result)

    def test_small_diff_returned_unchanged(self):
        """Diffs with <=50 lines bypass _compress_git_diff entirely."""
        diff = _make_large_diff(40)
        result = bash_compress._compress_git_diff(diff)
        # Small diff: function returns early before appending the summary.
        self.assertNotIn("more lines", result)
        self.assertEqual(result, diff)

    def test_large_diff_keeps_first_30_lines(self):
        """Only the first 30 lines of a large diff are kept."""
        diff = _make_large_diff(55)
        result = bash_compress._compress_git_diff(diff)
        content_lines = [ln for ln in result.split("\n") if not ln.startswith("...")]
        self.assertLessEqual(len(content_lines), 30)


if __name__ == "__main__":
    unittest.main()
