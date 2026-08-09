"""Doctor must credit globally installed hooks, not just project hooks."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import codex_doctor


GLOBAL_HOOKS = {
    "hooks": {
        "Stop": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "bash launcher token-optimizer/scripts run.py skills/token-optimizer/scripts/measure.py session-end-flush --defer",
                    }
                ]
            }
        ],
        "PostToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": "/opt/homebrew/bin/python3 token-optimizer/skills/token-optimizer/scripts/codex_post_tool.py --quiet",
                    }
                ]
            }
        ],
    }
}


class DoctorGlobalHooksTests(unittest.TestCase):
    def _with_global(self, global_payload, project_payload=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name) / "codex-home"
        home.mkdir()
        if global_payload is not None:
            (home / "hooks.json").write_text(json.dumps(global_payload))
        project = Path(tmp.name) / "project"
        project.mkdir()
        if project_payload is not None:
            (project / ".codex").mkdir()
            (project / ".codex" / "hooks.json").write_text(json.dumps(project_payload))
        return home, project

    def test_global_install_makes_project_hook_check_ok(self):
        home, project = self._with_global(GLOBAL_HOOKS)
        with patch.object(codex_doctor, "codex_home", return_value=home):
            check = codex_doctor._project_hook_check(project)
        self.assertEqual(check["status"], "OK")
        self.assertIn("single authority", check["detail"])

    def test_global_features_report_enabled_not_manual_mode(self):
        home, project = self._with_global(GLOBAL_HOOKS)
        with patch.object(codex_doctor, "codex_home", return_value=home):
            checks = codex_doctor._project_feature_checks(project)
        by_name = {c["name"]: c for c in checks}
        continuity = by_name["Feature: Session continuity and dashboard refresh"]
        self.assertEqual(continuity["status"], "OK")
        self.assertIn("global hooks", continuity["detail"])
        for feature in ("Optional feature: Tool output archive", "Optional feature: Context intelligence"):
            self.assertIn("enabled via global hooks", by_name[feature]["detail"])

    def test_project_duplicate_of_global_still_fails(self):
        project_payload = {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3 token-optimizer/scripts/x.py",
                            }
                        ]
                    }
                ]
            }
        }
        home, project = self._with_global(GLOBAL_HOOKS, project_payload)
        with patch.object(codex_doctor, "codex_home", return_value=home):
            check = codex_doctor._project_hook_check(project)
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("duplicate", check["detail"])

    def test_no_hooks_anywhere_keeps_manual_mode_warn(self):
        home, project = self._with_global(None)
        with patch.object(codex_doctor, "codex_home", return_value=home):
            check = codex_doctor._project_hook_check(project)
            checks = codex_doctor._project_feature_checks(project)
        self.assertEqual(check["status"], "WARN")
        self.assertEqual(checks, [])


if __name__ == "__main__":
    unittest.main()
