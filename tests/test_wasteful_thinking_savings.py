"""Regression test: wasteful_thinking savings_tokens uses excess above 4x threshold.

Bug: total_wasted accumulated `thinking - output_tokens` (excess above 1x)
     instead of `thinking - (4 * output_tokens)` (excess above the 4x threshold).

Example: thinking=1000, output=100.
  Buggy:   savings = 1000 - 100     = 900
  Correct: savings = 1000 - 4*100   = 600
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "token-optimizer", "scripts"))

from detectors.wasteful_thinking import detect_wasteful_thinking


def _write_jsonl(records):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
    for r in records:
        f.write(json.dumps(r) + "\n")
    f.flush()
    f.close()
    return f.name


def _assistant(thinking, output):
    return {
        "type": "assistant",
        "message": {
            "content": "ok",
            "usage": {"thinking_tokens": thinking, "output_tokens": output},
        },
    }


class WastefulThinkingSavingsRegressionTests(unittest.TestCase):
    """savings_tokens must count only tokens above the 4x threshold."""

    def setUp(self):
        self._files = []

    def tearDown(self):
        for p in self._files:
            try:
                os.unlink(p)
            except OSError:
                pass

    def _make(self, records):
        path = _write_jsonl(records)
        self._files.append(path)
        return path

    def test_savings_is_excess_above_4x_not_above_1x(self):
        # thinking=1000, output=100 → 4x threshold = 400
        # correct excess = 1000 - 400 = 600
        # buggy   excess = 1000 - 100 = 900
        records = [_assistant(thinking=1000, output=100)] * 5
        path = self._make(records)
        findings = detect_wasteful_thinking({"jsonl_path": path})
        self.assertEqual(len(findings), 1)
        # Each turn contributes 1000 - 400 = 600; 5 turns = 3000 total
        self.assertEqual(findings[0]["savings_tokens"], 3000)

    def test_savings_for_10x_thinking(self):
        # thinking=5000, output=200 → 4x threshold = 800
        # excess = 5000 - 800 = 4200 per turn
        records = [_assistant(thinking=5000, output=200)] * 4
        path = self._make(records)
        findings = detect_wasteful_thinking({"jsonl_path": path})
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["savings_tokens"], 4 * (5000 - 4 * 200))

    def test_turn_just_above_4x_threshold_has_small_savings(self):
        # thinking=401, output=100 → threshold=400, excess=1
        records = [_assistant(thinking=401, output=100)] * 4
        path = self._make(records)
        findings = detect_wasteful_thinking({"jsonl_path": path})
        self.assertEqual(len(findings), 1)
        # 4 turns × (401 - 400) = 4
        self.assertEqual(findings[0]["savings_tokens"], 4)

    def test_turn_at_exactly_4x_is_not_flagged(self):
        # thinking=400, output=100 → exactly 4x, not > 4x, so not wasteful
        records = [_assistant(thinking=400, output=100)] * 5
        path = self._make(records)
        findings = detect_wasteful_thinking({"jsonl_path": path})
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
