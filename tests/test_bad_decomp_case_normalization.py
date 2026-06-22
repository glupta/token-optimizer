"""Regression test: bad_decomposition detector must normalize verb case before counting.

Bug: _IMPERATIVE_PATTERN uses re.IGNORECASE, but findall() returns the original
matched text, not the lowercased form. Building a set() without .lower() treats
"Add", "add", and "ADD" as three distinct verbs instead of one. This causes false
positive "bad_decomposition" findings for perfectly reasonable prompts where the
same verb naturally appears in multiple cases (sentence-start capitalization,
all-caps headers, mid-sentence lowercase).

Fix: use {m.lower() for m in _IMPERATIVE_PATTERN.findall(text)} so that all case
variants of the same verb collapse to a single entry before the >= 5 threshold check.
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

from detectors.bad_decomposition import detect_bad_decomposition


def _make_session(user_text: str) -> dict:
    """Build a minimal session_data dict with one user message."""
    record = {
        "type": "user",
        "message": {"content": user_text},
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        f.write(json.dumps(record) + "\n")
        return {"jsonl_path": f.name}


def _long_text_with_verbs(verbs: list) -> str:
    """Return an 800+ word text embedding the given verbs."""
    filler = (
        "This is a detailed software engineering prompt that describes "
        "a real-world technical task with context, background, and rationale. " * 50
    )
    verb_sentences = " ".join(f"{v} something" for v in verbs)
    return filler + " " + verb_sentences


class BadDecompCaseNormalizationTest(unittest.TestCase):
    def test_same_verb_three_cases_not_five_distinct(self):
        """'Add', 'add', 'ADD' are ONE verb, not three — must not inflate count."""
        # 3 distinct verbs (add, create, update) but 'add' appears in 3 cases.
        # Pre-fix: set() gives {'Add', 'add', 'ADD', 'Create', 'Update'} = 5 → false positive.
        # Post-fix: {m.lower()} gives {'add', 'create', 'update'} = 3 → no finding.
        text = _long_text_with_verbs(["Add", "add", "ADD", "Create", "Update"])
        session = _make_session(text)
        findings = detect_bad_decomposition(session)
        self.assertEqual(
            findings,
            [],
            "3 distinct verbs with case variants must NOT trigger bad_decomposition",
        )

    def test_five_truly_distinct_verbs_still_reported(self):
        """Five different lowercased verbs MUST still trigger the finding."""
        text = _long_text_with_verbs(["add", "create", "update", "configure", "deploy"])
        session = _make_session(text)
        findings = detect_bad_decomposition(session)
        self.assertEqual(len(findings), 1, "5 distinct verbs must trigger bad_decomposition")
        self.assertEqual(findings[0]["name"], "bad_decomposition")

    def test_sentence_start_caps_plus_inline_lowercase(self):
        """Natural English: sentence-start 'Add' + mid-sentence 'add' = 1 verb, not 2."""
        # Pattern common in real prompts: 4 distinct verbs where 'add' appears twice
        text = _long_text_with_verbs(["Add", "add", "Create", "Update", "Configure"])
        session = _make_session(text)
        findings = detect_bad_decomposition(session)
        # 4 distinct verbs after lowercasing — below the threshold of 5
        self.assertEqual(
            findings,
            [],
            "4 distinct verbs (one in mixed case) must not trigger bad_decomposition",
        )


if __name__ == "__main__":
    unittest.main()
