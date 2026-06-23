"""Regression test: wasteful_thinking excess calculation.

Before fix: total_wasted += thinking - output_tokens
After fix:  total_wasted += thinking - (4 * output_tokens)

When thinking=1000 and output=100 (ratio=10x, threshold=4x), the excess
above the 4x threshold is 1000 - 400 = 600, not 1000 - 100 = 900.
"""
import json
import os
import tempfile

import pytest

# Adjust import path so tests can import from the skills directory
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "token-optimizer", "scripts"))

from detectors.wasteful_thinking import detect_wasteful_thinking


def _make_session(records, tmp_path):
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records))
    return {"jsonl_path": str(p)}


def _assistant(thinking, output):
    return {
        "type": "assistant",
        "message": {
            "usage": {
                "thinking_tokens": thinking,
                "output_tokens": output,
            }
        },
    }


def test_excess_is_above_4x_threshold(tmp_path):
    """savings_tokens must equal sum of (thinking - 4*output) per wasteful turn."""
    # 4 turns each with thinking=1000, output=100 → excess = 600 per turn → total 2400
    records = [_assistant(1000, 100)] * 4
    findings = detect_wasteful_thinking(_make_session(records, tmp_path))
    assert len(findings) == 1
    assert findings[0]["savings_tokens"] == 2400  # 4 * (1000 - 4*100)


def test_pre_fix_formula_would_have_returned_wrong_value(tmp_path):
    """Show that the old formula (thinking - output) gives a different (wrong) result."""
    records = [_assistant(1000, 100)] * 4
    findings = detect_wasteful_thinking(_make_session(records, tmp_path))
    # Old formula would have given 4 * (1000 - 100) = 3600 — assert it's NOT that
    assert findings[0]["savings_tokens"] != 3600


def test_turn_below_4x_threshold_not_counted(tmp_path):
    """Turns at exactly 4x threshold must not contribute to wasted count."""
    # thinking = 400, output = 100 → exactly 4x, should NOT be flagged
    records = [_assistant(400, 100)] * 4
    findings = detect_wasteful_thinking(_make_session(records, tmp_path))
    assert findings == []


def test_mixed_turns(tmp_path):
    """Only wasteful turns contribute to excess; non-wasteful are skipped."""
    # 4 wasteful turns (thinking=800, output=100, excess=400 each) + 2 fine turns
    records = [_assistant(800, 100)] * 4 + [_assistant(200, 100)] * 2
    findings = detect_wasteful_thinking(_make_session(records, tmp_path))
    assert len(findings) == 1
    assert findings[0]["savings_tokens"] == 4 * (800 - 4 * 100)  # 1600
