"""Tests for detectors/overpowered.py — detect_overpowered."""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "token-optimizer", "scripts"))

from detectors.overpowered import detect_overpowered


def _simple_session(model_name, model_tokens, avg_output_per_turn=500, simple_pct=0.9, api_calls=10):
    """Build a minimal session_data dict that should trigger an overpowered finding."""
    total_tool_count = 20
    simple_tool_count = int(total_tool_count * simple_pct)
    tool_calls = {"Read": simple_tool_count, "WebFetch": total_tool_count - simple_tool_count}
    return {
        "model_usage": {model_name: model_tokens},
        "total_output_tokens": avg_output_per_turn * api_calls,
        "api_calls": api_calls,
        "tool_calls": tool_calls,
    }


class OverpoweredFlagsTopTierTests(unittest.TestCase):

    def test_fable_dominant_simple_session_flagged(self):
        session = _simple_session("claude-fable-5", 100_000)
        findings = detect_overpowered(session)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["name"], "overpowered")

    def test_opus_dominant_simple_session_flagged(self):
        session = _simple_session("claude-opus-4-8", 100_000)
        findings = detect_overpowered(session)
        self.assertEqual(len(findings), 1)

    def test_claude_opus_prefix_is_recognised(self):
        session = _simple_session("claude-opus", 100_000)
        findings = detect_overpowered(session)
        self.assertEqual(len(findings), 1)


class OverpoweredSavingsCalculationTests(unittest.TestCase):
    """savings_tokens must reflect the model-aware rate delta vs Sonnet (3.0/Mtok)."""

    def test_fable_savings_ratio_is_70_percent(self):
        # Fable rate = 10.0; Sonnet = 3.0 → savings = 1 - 3/10 = 70%
        tokens = 100_000
        session = _simple_session("claude-fable-5", tokens)
        f = detect_overpowered(session)[0]
        expected = int(tokens * 0.70)
        self.assertAlmostEqual(f["savings_tokens"], expected, delta=1)

    def test_opus_savings_ratio_is_40_percent(self):
        # Opus rate = 5.0; Sonnet = 3.0 → savings = 1 - 3/5 = 40%
        tokens = 100_000
        session = _simple_session("claude-opus-4-8", tokens)
        f = detect_overpowered(session)[0]
        expected = int(tokens * 0.40)
        self.assertAlmostEqual(f["savings_tokens"], expected, delta=1)

    def test_finding_mentions_savings_in_suggestion(self):
        session = _simple_session("claude-fable-5", 100_000)
        f = detect_overpowered(session)[0]
        self.assertIn("save", f["suggestion"].lower())


class OverpoweredNoFindingTests(unittest.TestCase):

    def test_sonnet_only_session_not_flagged(self):
        session = _simple_session("claude-sonnet-4-6", 100_000)
        self.assertEqual(detect_overpowered(session), [])

    def test_haiku_only_session_not_flagged(self):
        session = _simple_session("claude-haiku-4-5", 100_000)
        self.assertEqual(detect_overpowered(session), [])

    def test_top_tier_under_50_pct_not_flagged(self):
        # 49% fable, 51% sonnet → should not flag
        session = {
            "model_usage": {"claude-fable-5": 49_000, "claude-sonnet-4-6": 51_000},
            "total_output_tokens": 5_000,
            "api_calls": 10,
            "tool_calls": {"Read": 18, "WebFetch": 2},
        }
        self.assertEqual(detect_overpowered(session), [])

    def test_high_output_per_turn_not_flagged(self):
        # avg output > 5000 → complex session, don't flag
        session = _simple_session("claude-fable-5", 100_000, avg_output_per_turn=6000)
        self.assertEqual(detect_overpowered(session), [])

    def test_complex_tool_mix_not_flagged(self):
        # Only 50% simple tools (< 0.7 threshold)
        session = _simple_session("claude-fable-5", 100_000, simple_pct=0.5)
        self.assertEqual(detect_overpowered(session), [])

    def test_empty_model_usage_returns_empty(self):
        self.assertEqual(detect_overpowered({}), [])
        self.assertEqual(detect_overpowered({"model_usage": {}}), [])

    def test_zero_total_tokens_returns_empty(self):
        session = {"model_usage": {"claude-fable-5": 0}}
        self.assertEqual(detect_overpowered(session), [])


class OverpoweredFindingMetadataTests(unittest.TestCase):

    def test_occurrence_count_is_one(self):
        session = _simple_session("claude-fable-5", 100_000)
        f = detect_overpowered(session)[0]
        self.assertEqual(f["occurrence_count"], 1)

    def test_confidence_is_0_6(self):
        session = _simple_session("claude-fable-5", 100_000)
        f = detect_overpowered(session)[0]
        self.assertAlmostEqual(f["confidence"], 0.6)

    def test_evidence_includes_model_name(self):
        session = _simple_session("claude-fable-5", 100_000)
        f = detect_overpowered(session)[0]
        self.assertIn("fable", f["evidence"].lower())


if __name__ == "__main__":
    unittest.main()
