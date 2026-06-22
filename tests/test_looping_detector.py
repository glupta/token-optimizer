"""Tests for detectors/looping.py — detect_looping."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "token-optimizer", "scripts"))

from detectors.looping import detect_looping, _similarity, _word_set


def _write_jsonl(records):
    """Write records to a temp JSONL file; return the file path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
    for r in records:
        f.write(json.dumps(r) + "\n")
    f.flush()
    f.close()
    return f.name


def _user_msg(text):
    return {"type": "user", "message": {"content": text}}


def _user_block_msg(text):
    """User message using content-block format."""
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def _assistant_msg(text="ok"):
    return {"type": "assistant", "message": {"content": text}}


class LoopingWordSimilarityTests(unittest.TestCase):
    """Unit tests for the Jaccard helpers."""

    def test_identical_texts_similarity_one(self):
        a = _word_set("hello world foo bar")
        self.assertAlmostEqual(_similarity(a, a), 1.0)

    def test_disjoint_texts_similarity_zero(self):
        a = _word_set("apple banana cherry")
        b = _word_set("dog elephant frog")
        self.assertAlmostEqual(_similarity(a, b), 0.0)

    def test_partial_overlap(self):
        a = _word_set("the cat sat on the mat")
        b = _word_set("the cat sat on the chair")
        sim = _similarity(a, b)
        self.assertGreater(sim, 0.5)
        self.assertLess(sim, 1.0)

    def test_empty_sets_return_zero(self):
        self.assertEqual(_similarity(set(), set()), 0.0)
        self.assertEqual(_similarity(_word_set("word"), set()), 0.0)


class LoopingDetectTests(unittest.TestCase):

    def setUp(self):
        self._tmp_files = []

    def tearDown(self):
        for p in self._tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass

    def _make_jsonl(self, records):
        path = _write_jsonl(records)
        self._tmp_files.append(path)
        return path

    # ------------------------------------------------------------------ triggers

    def test_four_near_identical_messages_triggers_finding(self):
        msg = "please fix the failing test in test_foo.py using pytest"
        records = [_user_msg(msg)] * 4 + [_assistant_msg()]
        path = self._make_jsonl(records)
        findings = detect_looping({"jsonl_path": path})
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["name"], "looping")

    def test_five_similar_messages_reports_streak_length(self):
        msg = "can you check the logs and find the error that happened yesterday"
        records = [_user_msg(msg)] * 5
        path = self._make_jsonl(records)
        findings = detect_looping({"jsonl_path": path})
        self.assertEqual(findings[0]["occurrence_count"], 5)

    def test_content_block_format_also_detected(self):
        msg = "please refactor the helper function in utils.py to be more readable"
        records = [_user_block_msg(msg)] * 4
        path = self._make_jsonl(records)
        findings = detect_looping({"jsonl_path": path})
        self.assertEqual(len(findings), 1)

    def test_savings_tokens_proportional_to_streak(self):
        msg = "run the full test suite and show me the output please thank you"
        records = [_user_msg(msg)] * 6
        path = self._make_jsonl(records)
        findings = detect_looping({"jsonl_path": path})
        # 6 * 5000 = 30000
        self.assertEqual(findings[0]["savings_tokens"], 30_000)

    # ------------------------------------------------------------------ no-finding cases

    def test_fewer_than_four_messages_no_finding(self):
        msg = "can you help me fix this bug in the authentication code"
        records = [_user_msg(msg)] * 3
        path = self._make_jsonl(records)
        self.assertEqual(detect_looping({"jsonl_path": path}), [])

    def test_diverse_messages_no_finding(self):
        records = [
            _user_msg("check the authentication module"),
            _user_msg("now look at the database schema"),
            _user_msg("run the test suite please"),
            _user_msg("deploy to staging environment"),
        ]
        path = self._make_jsonl(records)
        self.assertEqual(detect_looping({"jsonl_path": path}), [])

    def test_short_messages_under_10_chars_skipped(self):
        records = [_user_msg("ok")] * 10  # < 10 chars each
        path = self._make_jsonl(records)
        self.assertEqual(detect_looping({"jsonl_path": path}), [])

    def test_broken_streak_not_four_consecutive_no_finding(self):
        similar = "can you check the failing tests in the authentication module please"
        different = "completely different request about deployment and infrastructure"
        records = [
            _user_msg(similar),
            _user_msg(similar),
            _user_msg(similar),
            _user_msg(different),  # breaks the streak
            _user_msg(similar),
        ]
        path = self._make_jsonl(records)
        self.assertEqual(detect_looping({"jsonl_path": path}), [])

    # ------------------------------------------------------------------ edge / error

    def test_missing_jsonl_path_returns_empty(self):
        self.assertEqual(detect_looping({}), [])

    def test_nonexistent_file_returns_empty(self):
        self.assertEqual(detect_looping({"jsonl_path": "/nonexistent/file.jsonl"}), [])

    def test_malformed_json_lines_skipped_gracefully(self):
        path = tempfile.mktemp(suffix=".jsonl")
        self._tmp_files.append(path)
        msg = "please check the failing tests in the authentication module for errors"
        with open(path, "w") as f:
            f.write("not json at all\n")
            for _ in range(4):
                f.write(json.dumps(_user_msg(msg)) + "\n")
        findings = detect_looping({"jsonl_path": path})
        self.assertEqual(len(findings), 1)

    def test_finding_confidence_is_0_6(self):
        msg = "please run the failing tests in test_suite and show me what breaks"
        records = [_user_msg(msg)] * 4
        path = self._make_jsonl(records)
        findings = detect_looping({"jsonl_path": path})
        self.assertAlmostEqual(findings[0]["confidence"], 0.6)


if __name__ == "__main__":
    unittest.main()
