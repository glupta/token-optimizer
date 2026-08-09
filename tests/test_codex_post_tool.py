"""Combined Codex PostToolUse hook: single-read fan-out regression tests."""

import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import codex_post_tool


class CodexPostToolTests(unittest.TestCase):
    def test_reads_stdin_once_and_feeds_both_handlers_same_payload(self):
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash"}
        seen = {}

        import archive_result
        import context_intel

        def intel_handler():
            seen["intel"] = context_intel.read_stdin_hook_input()
            print("intel-stdout-passthrough")

        def archive_handler(quiet=False):
            seen["archive"] = archive_result.read_stdin_hook_input()
            seen["archive_quiet"] = quiet
            print("archive-stdout-must-be-suppressed")

        captured = io.StringIO()
        with (
            patch("hook_io.read_stdin_hook_input", return_value=payload) as reader,
            patch.object(context_intel, "handle_post_tool_use", intel_handler),
            patch.object(archive_result, "archive_result", archive_handler),
            redirect_stdout(captured),
        ):
            self.assertEqual(codex_post_tool.main(), 0)

        self.assertEqual(reader.call_count, 1)
        self.assertIs(seen["intel"], payload)
        self.assertIs(seen["archive"], payload)
        self.assertTrue(seen["archive_quiet"])
        out = captured.getvalue()
        self.assertIn("intel-stdout-passthrough", out)
        self.assertNotIn("archive-stdout-must-be-suppressed", out)

    def test_handler_failure_never_blocks_the_other_or_exit_zero(self):
        import archive_result
        import context_intel

        called = {}

        def archive_handler(quiet=False):
            called["archive"] = True

        with (
            patch("hook_io.read_stdin_hook_input", return_value={}),
            patch.object(
                context_intel,
                "handle_post_tool_use",
                side_effect=RuntimeError("boom"),
            ),
            patch.object(archive_result, "archive_result", archive_handler),
        ):
            self.assertEqual(codex_post_tool.main(), 0)
        self.assertTrue(called.get("archive"))

    def test_stdin_read_failure_exits_zero_without_calling_handlers(self):
        import archive_result
        import context_intel

        with (
            patch("hook_io.read_stdin_hook_input", side_effect=OSError("no tty")),
            patch.object(context_intel, "handle_post_tool_use") as intel,
            patch.object(archive_result, "archive_result") as archive,
        ):
            self.assertEqual(codex_post_tool.main(), 0)
        intel.assert_not_called()
        archive.assert_not_called()

    def test_subprocess_garbage_stdin_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "codex_post_tool.py"), "--quiet"],
            input=b"not json {",
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
