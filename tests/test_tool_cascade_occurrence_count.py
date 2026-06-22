"""Regression test: tool_cascade occurrence_count must equal streak_len, not len(streaks).

Bug: each finding was reporting occurrence_count = len(streaks) (total distinct cascades
in the session) instead of streak_len (consecutive errors in this specific cascade).
For a session with two separate cascades of lengths 4 and 6, both findings incorrectly
reported occurrence_count = 2 instead of 4 and 6 respectively.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[1]
        / "skills"
        / "token-optimizer"
        / "scripts"
    ),
)

from detectors.tool_cascade import detect_tool_cascade


def _make_jsonl(records):
    return "\n".join(json.dumps(r) for r in records) + "\n"


def _error_result():
    return {"type": "tool_result", "is_error": True, "content": "error"}


def _ok_result():
    return {"type": "tool_result", "is_error": False, "content": "ok"}


class ToolCascadeOccurrenceCountTest(unittest.TestCase):
    def test_single_cascade_occurrence_count_equals_streak_length(self):
        """Trivial case: one cascade of 5 errors → occurrence_count == 5."""
        records = [_error_result()] * 5 + [_ok_result()]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(_make_jsonl(records))
            path = f.name

        findings = detect_tool_cascade({"jsonl_path": path})
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["occurrence_count"], 5,
                         "occurrence_count should equal streak_len (5), not len(streaks) (1)")

    def test_multi_cascade_each_finding_reports_its_own_streak_length(self):
        """Core regression: two cascades of length 4 and 6 must report their own lengths.

        Before the fix: both findings reported occurrence_count = 2 (len(streaks)).
        After the fix: findings report 4 and 6 respectively.
        """
        records = (
            [_error_result()] * 4  # first cascade: 4 errors
            + [_ok_result()]       # break
            + [_error_result()] * 6  # second cascade: 6 errors
            + [_ok_result()]
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(_make_jsonl(records))
            path = f.name

        findings = detect_tool_cascade({"jsonl_path": path})
        self.assertEqual(len(findings), 2)
        counts = sorted(f["occurrence_count"] for f in findings)
        self.assertEqual(
            counts, [4, 6],
            f"Expected occurrence_counts [4, 6], got {counts}. "
            "Before the fix this would be [2, 2] (len(streaks) for both).",
        )

    def test_occurrence_count_not_equal_to_total_cascade_count(self):
        """Explicit check: occurrence_count must NOT equal len(streaks) for multi-cascade."""
        records = (
            [_error_result()] * 5
            + [_ok_result()]
            + [_error_result()] * 7
            + [_ok_result()]
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(_make_jsonl(records))
            path = f.name

        findings = detect_tool_cascade({"jsonl_path": path})
        self.assertEqual(len(findings), 2)
        total_cascades = len(findings)  # = 2
        for finding in findings:
            self.assertNotEqual(
                finding["occurrence_count"],
                total_cascades,
                f"occurrence_count {finding['occurrence_count']} must not equal "
                f"total cascade count {total_cascades} (the pre-fix bug value).",
            )


if __name__ == "__main__":
    unittest.main()
