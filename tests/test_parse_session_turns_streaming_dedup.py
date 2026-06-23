"""Regression test: parse_session_turns must deduplicate streaming records.

Bug: Claude Code writes multiple assistant records per requestId during streaming;
each record's usage.output_tokens is the cumulative count up to that chunk.
parse_session_turns had no requestId-based dedup, so a response streamed in N
chunks produced N turns with partial (not final) token counts, causing:
  - inflated turn counts (N turns instead of 1 per API call)
  - wrong per-turn output_tokens (partial vs final cumulative)
  - wrong per-turn cost_usd
  - skewed findings in the output_waste detector (which uses session_data["turns"])

_parse_session_jsonl received a fix for this in v5.4.9 (the requestId-MAX dedup).
parse_session_turns was not updated at the same time — this test covers the gap.

Fix: apply the same requestId-based MAX dedup in parse_session_turns so it
returns ONE turn per API call with the final (maximum) token counts.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "skills" / "token-optimizer" / "scripts"),
)

import measure


def _make_streaming_session(request_id="req-abc-123"):
    """Three assistant records for the same requestId (one streaming API call)."""
    return [
        # Chunk 1: tool_use appears in first chunk, partial output
        {
            "type": "assistant",
            "requestId": request_id,
            "timestamp": "2026-04-04T09:01:00Z",
            "message": {
                "model": "claude-sonnet-4-6",
                "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/a.py"}}],
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 500,
                    "cache_creation_input_tokens": 200,
                },
            },
        },
        # Chunk 2: text delta, more cumulative output
        {
            "type": "assistant",
            "requestId": request_id,
            "timestamp": "2026-04-04T09:01:01Z",
            "message": {
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "partial text"}],
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 250,
                    "cache_read_input_tokens": 500,
                    "cache_creation_input_tokens": 200,
                },
            },
        },
        # Chunk 3: final chunk — highest cumulative output_tokens
        {
            "type": "assistant",
            "requestId": request_id,
            "timestamp": "2026-04-04T09:01:02Z",
            "message": {
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "final complete response"}],
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 600,
                    "cache_read_input_tokens": 500,
                    "cache_creation_input_tokens": 200,
                },
            },
        },
    ]


class ParseSessionTurnsStreamingDedupTest(unittest.TestCase):
    def _write_session(self, records):
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        f.write("\n".join(json.dumps(r) for r in records) + "\n")
        f.close()
        return Path(f.name)

    def test_one_turn_per_requestid_not_per_streaming_chunk(self):
        """Three streaming chunks with same requestId must produce exactly one turn.

        Pre-fix: produced 3 turns (one per JSONL record).
        Post-fix: produces 1 turn (one per requestId / API call).
        """
        path = self._write_session(_make_streaming_session())
        try:
            turns = measure.parse_session_turns(str(path))
            self.assertEqual(
                len(turns), 1,
                f"Expected 1 turn for 1 requestId, got {len(turns)}. "
                "Pre-fix behaviour was 3 turns (one per streaming chunk).",
            )
        finally:
            path.unlink(missing_ok=True)

    def test_output_tokens_is_max_across_streaming_chunks(self):
        """The returned turn must carry the final (maximum) output_tokens.

        Pre-fix: turn used the first chunk's partial output_tokens (50).
        Post-fix: turn uses the maximum cumulative output_tokens (600).
        """
        path = self._write_session(_make_streaming_session())
        try:
            turns = measure.parse_session_turns(str(path))
            self.assertEqual(len(turns), 1)
            self.assertEqual(
                turns[0]["output_tokens"], 600,
                f"Expected output_tokens=600 (final cumulative), "
                f"got {turns[0]['output_tokens']}. "
                "Pre-fix would return 50 (first partial chunk).",
            )
        finally:
            path.unlink(missing_ok=True)

    def test_tools_used_collected_from_all_chunks(self):
        """tools_used must include tool_use blocks from ALL streaming chunks.

        The tool_use block appears in the first chunk; the final chunk has only
        text. Without union across chunks, tools_used would be empty if we only
        looked at the final chunk.
        """
        path = self._write_session(_make_streaming_session())
        try:
            turns = measure.parse_session_turns(str(path))
            self.assertEqual(len(turns), 1)
            self.assertIn(
                "Read", turns[0]["tools_used"],
                "tools_used must include 'Read' from the first streaming chunk.",
            )
        finally:
            path.unlink(missing_ok=True)

    def test_two_distinct_requestids_produce_two_turns(self):
        """Two separate API calls (different requestIds) must produce two turns."""
        records = _make_streaming_session("req-1") + _make_streaming_session("req-2")
        path = self._write_session(records)
        try:
            turns = measure.parse_session_turns(str(path))
            self.assertEqual(
                len(turns), 2,
                f"Expected 2 turns for 2 requestIds, got {len(turns)}.",
            )
        finally:
            path.unlink(missing_ok=True)

    def test_non_streaming_single_record_unchanged(self):
        """A single record per requestId (non-streaming) must still produce one turn."""
        records = [
            {
                "type": "assistant",
                "requestId": "req-solo",
                "timestamp": "2026-04-04T09:01:00Z",
                "message": {
                    "model": "claude-sonnet-4-6",
                    "content": [{"type": "tool_use", "name": "Edit", "input": {}}],
                    "usage": {
                        "input_tokens": 500,
                        "output_tokens": 100,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }
        ]
        path = self._write_session(records)
        try:
            turns = measure.parse_session_turns(str(path))
            self.assertEqual(len(turns), 1)
            self.assertEqual(turns[0]["output_tokens"], 100)
            self.assertEqual(turns[0]["tools_used"], ["Edit"])
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
