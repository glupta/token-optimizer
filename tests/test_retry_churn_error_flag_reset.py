"""Regression test: retry_churn last_had_error not reset on successful tool_result.

Before fix: a successful tool_result left last_had_error=True, causing the next
tool_use of the same tool to be falsely counted as an error-driven retry.

After fix: a successful tool_result resets last_had_error=False.
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "token-optimizer", "scripts"))

from detectors.retry_churn import detect_retry_churn


def _make_session(records, tmp_path):
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records))
    return {"jsonl_path": str(p)}


def _tool_use(name, inp="{}"):
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": name, "input": inp}]
        },
    }


def _tool_result(error=False):
    text = "error: command failed" if error else "ok"
    return {"type": "tool_result", "content": text}


def test_no_false_positive_after_success(tmp_path):
    """A success result followed by the same tool should NOT be counted as churn."""
    records = [
        _tool_use("Bash", "cmd1"),
        _tool_result(error=True),   # first call errored
        _tool_result(error=False),  # unrelated success result clears the flag
        _tool_use("Bash", "cmd1"), # retry — but success already reset the flag
        _tool_result(error=False),
        _tool_use("Bash", "cmd1"),
        _tool_result(error=False),
    ]
    findings = detect_retry_churn(_make_session(records, tmp_path))
    assert findings == [], f"Expected no findings, got: {findings}"


def test_genuine_retry_churn_still_detected(tmp_path):
    """Error → same-tool retry repeated 3+ times must still be flagged."""
    records = []
    for _ in range(4):
        records.append(_tool_use("Bash", "bad_cmd"))
        records.append(_tool_result(error=True))

    findings = detect_retry_churn(_make_session(records, tmp_path))
    assert len(findings) == 1
    assert findings[0]["name"] == "retry_churn"
    assert findings[0]["occurrence_count"] >= 3


def test_success_between_errors_skips_one_retry_count(tmp_path):
    """A success result between two error chains removes one retry from the tally.

    Without the fix (last_had_error never reset on success), all 3 retries of
    cmd_a would be error-preceded, giving count=4 (initial + 3 retries).
    With the fix, the retry immediately after the success is not counted, giving
    count=3 (initial + 2 genuine error-retries). Both cross the churn threshold,
    but the occurrence_count is lower after the fix.
    """
    records = [
        _tool_use("Bash", "cmd_a"),
        _tool_result(error=True),   # cmd_a errors → last_had_error = True
        _tool_result(error=False),  # success clears flag → last_had_error = False (fix)
        _tool_use("Bash", "cmd_a"), # NOT counted as error-retry after fix
        _tool_result(error=True),   # errors again
        _tool_use("Bash", "cmd_a"), # counted (error-preceded)
        _tool_result(error=True),
        _tool_use("Bash", "cmd_a"), # counted (error-preceded)
        _tool_result(error=False),
    ]
    findings = detect_retry_churn(_make_session(records, tmp_path))
    # After fix: 2 error-preceded retries counted → tool_attempts = 1+2 = 3
    assert len(findings) == 1
    # With the fix the count is 3 (not 4 as it would be without reset-on-success)
    assert findings[0]["occurrence_count"] == 3
