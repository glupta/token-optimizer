"""Mechanical contract, receipt, and host-verification regression tests."""

import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import codex_doctor
import codex_hook_bridge
import codex_session
import codex_token_contract


class CodexTokenContractTests(unittest.TestCase):
    def test_v2_defaults_to_light_and_escalates_only_full_ceremony_signals(self):
        self.assertEqual(codex_token_contract.CONTRACT_VERSION, "studio-token-management-v2")
        self.assertEqual(codex_token_contract.classify_ceremony(), "light")
        self.assertEqual(
            codex_token_contract.classify_ceremony(signals={"read-only", "simple", "low-risk"}),
            "light",
        )
        for signal in ("substantive", "long-running", "risky", "external", "multi-repo", "durable"):
            with self.subTest(signal=signal):
                self.assertEqual(
                    codex_token_contract.classify_ceremony(
                        signals={signal}, requested_ceremony="light"
                    ),
                    "full",
                )

    def test_light_label_cannot_bypass_any_independent_action_gate(self):
        dangerous_signals = ("push", "deploy", "send", "spend", "sign", "trade", "destructive")
        for signal in dangerous_signals:
            with self.subTest(signal=signal):
                policy = codex_token_contract.policy_for_task(
                    signals={signal}, requested_ceremony="light"
                )
                self.assertEqual(policy.ceremony, "full")
                self.assertEqual(
                    policy.independent_gates,
                    codex_token_contract.INDEPENDENT_ACTION_GATES,
                )

        # Mutation proof: even a nominally light task is invalid if bypass logic
        # strips the independent gates. This test would pass under label-based
        # bypass logic and therefore guards the exact regression.
        light = codex_token_contract.policy_for_task(requested_ceremony="light")
        with self.assertRaises(ValueError):
            codex_token_contract.validate_policy(replace(light, independent_gates=()))

    def test_context_thresholds_warn_but_native_compaction_still_checkpoints(self):
        policy = codex_token_contract.policy_for_task()
        self.assertEqual(policy.early_threshold_behavior, "warning")
        self.assertTrue(policy.checkpoint_before_native_compaction)
        self.assertEqual(policy.handoff_condition, "safe-boundary-and-completion-not-credible")
        self.assertIn("task-aware warnings", codex_token_contract.CONTRACT_TEXT)
        self.assertIn("mandatory durable checkpoint", codex_token_contract.CONTRACT_TEXT)
        self.assertIn("completion in the current context is no longer credible", codex_token_contract.CONTRACT_TEXT)

    def test_contract_validator_rejects_gate_or_backstop_bypass(self):
        codex_token_contract.validate_contract_text(codex_token_contract.CONTRACT_TEXT)
        bypassed_gate = codex_token_contract.CONTRACT_TEXT.replace("money/spending", "ordinary budgeting")
        with self.assertRaises(ValueError):
            codex_token_contract.validate_contract_text(bypassed_gate)
        bypassed_compaction = codex_token_contract.CONTRACT_TEXT.replace(
            "mandatory durable checkpoint", "optional note"
        )
        with self.assertRaises(ValueError):
            codex_token_contract.validate_contract_text(bypassed_compaction)

    def test_composition_replaces_stale_block_and_keeps_continuity(self):
        restored = (
            f"{codex_token_contract.CONTRACT_BEGIN}\nold contract\n"
            f"{codex_token_contract.CONTRACT_END}\nrestored checkpoint"
        )
        output = codex_token_contract.compose_session_context(restored)
        self.assertEqual(output.count(codex_token_contract.CONTRACT_BEGIN), 1)
        self.assertIn(codex_token_contract.CONTRACT_VERSION, output)
        self.assertNotIn("old contract", output)
        self.assertIn("restored checkpoint", output)

    def test_receipt_filename_uses_hash_to_prevent_sanitized_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            first = codex_token_contract.receipt_path("same/id", home)
            second = codex_token_contract.receipt_path("same:id", home)
            self.assertNotEqual(first.name, second.name)
            self.assertLessEqual(len(first.name), 114)

    def test_receipt_is_atomic_bounded_and_marks_simulation(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            transcript = home / "sessions" / "2026" / "session.jsonl"
            transcript.parent.mkdir(parents=True)
            transcript.write_text("{}\n", encoding="utf-8")
            hook_input = {
                "session_id": "session-123456",
                "transcript_path": str(transcript),
                "source": "compact",
            }
            with patch.object(codex_session, "codex_home", return_value=home), patch.dict(
                os.environ, {codex_token_contract.SIMULATED_ENV: "1"}
            ):
                for index in range(codex_token_contract.MAX_RECEIPT_SOURCES + 3):
                    hook_input["source"] = f"source-{index}"
                    path = codex_token_contract.record_session_start_receipt(
                        hook_input, home=home, now=1000 + index
                    )

            self.assertIsNotNone(path)
            receipt = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(receipt["simulated"])
            self.assertEqual(receipt["contract_sha256"], codex_token_contract.CONTRACT_SHA256)
            self.assertEqual(len(receipt["sources"]), codex_token_contract.MAX_RECEIPT_SOURCES)
            self.assertEqual(receipt["transcript_name"], "session.jsonl")
            self.assertNotIn("content", receipt)

    def test_simulated_current_receipt_does_not_suppress_real_refresh(self):
        with patch.object(
            codex_token_contract,
            "load_receipt",
            return_value={
                "contract_version": codex_token_contract.CONTRACT_VERSION,
                "contract_sha256": codex_token_contract.CONTRACT_SHA256,
                "simulated": True,
            },
        ):
            self.assertTrue(codex_token_contract.needs_contract_refresh("session-123456"))

    def test_receipt_rejects_transcript_outside_codex_session_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            outside = home / "outside.jsonl"
            with patch.object(codex_session, "codex_home", return_value=home):
                result = codex_token_contract.record_session_start_receipt(
                    {"session_id": "session-123456", "transcript_path": str(outside)},
                    home=home,
                )
            self.assertIsNone(result)

    def test_install_state_records_contract_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = codex_token_contract.record_global_install(home=Path(tmp), now=1000)
            state = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(state["contract_version"], codex_token_contract.CONTRACT_VERSION)
            self.assertEqual(state["contract_sha256"], codex_token_contract.CONTRACT_SHA256)
            self.assertEqual(state["schema_version"], codex_token_contract.INSTALL_SCHEMA_VERSION)

    def test_session_identity_uses_session_meta_id_and_start_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            records = [
                {
                    "timestamp": "2026-08-08T12:00:00Z",
                    "type": "session_meta",
                    "payload": {"id": "session-immutable-123456"},
                },
                {
                    "timestamp": "2026-08-08T13:00:00Z",
                    "type": "event_msg",
                    "payload": {"type": "token_count"},
                },
            ]
            path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

            identity = codex_session.session_identity(path)

            self.assertEqual(identity["session_id"], "session-immutable-123456")
            self.assertEqual(identity["started_at"], 1786190400.0)

    def test_session_start_injects_contract_for_every_source(self):
        for source in ("startup", "resume", "clear", "compact"):
            emitted = []
            hook_input = {
                "session_id": "session-123456",
                "transcript_path": "/tmp/not-used-in-test.jsonl",
                "source": source,
            }

            def capture(func, *args, **kwargs):
                if func is codex_hook_bridge.measure.compact_restore:
                    return "restored continuity"
                return ""

            with self.subTest(source=source), patch.object(
                codex_hook_bridge, "read_stdin_hook_input", return_value=hook_input
            ), patch.object(codex_hook_bridge, "_capture_stdout", side_effect=capture), patch.object(
                codex_hook_bridge, "_has_matching_checkpoint", return_value=True
            ), patch.object(
                codex_hook_bridge.codex_token_contract, "record_session_start_receipt"
            ), patch.object(
                codex_hook_bridge,
                "_emit_additional_context",
                side_effect=lambda event, text: emitted.append((event, text)),
            ):
                codex_hook_bridge.handle_session_start()

            self.assertEqual(len(emitted), 1)
            self.assertEqual(emitted[0][0], "SessionStart")
            self.assertEqual(emitted[0][1].count(codex_token_contract.CONTRACT_BEGIN), 1)
            if source in {"resume", "clear"}:
                self.assertIn("restored continuity", emitted[0][1])

    def test_user_prompt_refreshes_an_already_active_stale_session_once(self):
        hook_input = {
            "session_id": "active-session-123456",
            "transcript_path": "/tmp/not-used-in-test.jsonl",
            "source": "user-prompt-submit",
            "prompt": "continue",
        }
        emitted = []
        with patch.object(
            codex_hook_bridge, "read_stdin_hook_input", return_value=hook_input
        ), patch.object(
            codex_hook_bridge.codex_session, "is_codex_session_path", return_value=True
        ), patch.object(
            codex_hook_bridge.measure, "quality_cache", return_value=None
        ), patch.object(
            codex_hook_bridge.measure, "codex_prompt_hints", return_value=""
        ), patch.object(
            codex_hook_bridge.measure, "run_verbosity_steer", return_value=None
        ), patch.object(
            codex_hook_bridge.codex_token_contract, "needs_contract_refresh", side_effect=[True, False]
        ), patch.object(
            codex_hook_bridge.codex_token_contract, "record_contract_receipt"
        ) as record, patch.object(
            codex_hook_bridge,
            "_emit_additional_context",
            side_effect=lambda event, text: emitted.append((event, text)),
        ):
            codex_hook_bridge.handle_user_prompt_submit()
            codex_hook_bridge.handle_user_prompt_submit()

        self.assertIn(codex_token_contract.CONTRACT_TEXT, emitted[0][1])
        self.assertNotIn(codex_token_contract.CONTRACT_TEXT, emitted[1][1])
        record.assert_called_once_with(hook_input, source="user-prompt-refresh")


class CodexTokenContractDoctorTests(unittest.TestCase):
    def _install_state(self):
        return {
            "contract_version": codex_token_contract.CONTRACT_VERSION,
            "contract_sha256": codex_token_contract.CONTRACT_SHA256,
            "contract_installed_at": "1970-01-01T00:16:40+00:00",
        }

    def test_startup_grace_boundary_excludes_earlier_session(self):
        boundary = 1000 + codex_doctor.HOST_SESSION_START_GRACE_SECONDS
        with patch.object(codex_token_contract, "load_install_state", side_effect=self._install_state), patch.object(
            codex_token_contract, "iter_receipts", return_value=iter([])
        ), patch.object(
            codex_session, "find_all_jsonl_files", return_value=[(Path("before.jsonl"), 0, "test")]
        ), patch.object(
            codex_session,
            "session_identity",
            return_value={"session_id": "session-before", "started_at": boundary - 0.001, "path": "before.jsonl"},
        ):
            check = codex_doctor._token_contract_runtime_check()
        self.assertEqual(check["status"], "WARN")
        self.assertIn("pending", check["detail"])

    def test_boundary_session_without_receipt_fails(self):
        boundary = 1000 + codex_doctor.HOST_SESSION_START_GRACE_SECONDS
        with patch.object(codex_token_contract, "load_install_state", side_effect=self._install_state), patch.object(
            codex_token_contract, "iter_receipts", return_value=iter([])
        ), patch.object(
            codex_session, "find_all_jsonl_files", return_value=[(Path("boundary.jsonl"), 0, "test")]
        ), patch.object(
            codex_session,
            "session_identity",
            return_value={"session_id": "session-boundary", "started_at": boundary, "path": "boundary.jsonl"},
        ), patch.object(codex_token_contract, "load_receipt", return_value={}):
            check = codex_doctor._token_contract_runtime_check()
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("not invoked", check["detail"])

    def test_matching_non_simulated_receipt_passes(self):
        boundary = 1000 + codex_doctor.HOST_SESSION_START_GRACE_SECONDS
        session_id = "session-real"
        receipt = {
            "session_id": session_id,
            "contract_version": codex_token_contract.CONTRACT_VERSION,
            "contract_sha256": codex_token_contract.CONTRACT_SHA256,
            "simulated": False,
        }
        with patch.object(codex_token_contract, "load_install_state", side_effect=self._install_state), patch.object(
            codex_token_contract, "iter_receipts", return_value=iter([])
        ), patch.object(
            codex_session, "find_all_jsonl_files", return_value=[(Path("real.jsonl"), 0, "test")]
        ), patch.object(
            codex_session,
            "session_identity",
            return_value={"session_id": session_id, "started_at": boundary + 1, "path": "real.jsonl"},
        ), patch.object(codex_token_contract, "load_receipt", return_value=receipt):
            check = codex_doctor._token_contract_runtime_check()
        self.assertEqual(check["status"], "OK")

    def test_post_install_active_chat_refresh_passes_without_new_session(self):
        receipt = {
            "session_id": "active-session",
            "contract_version": codex_token_contract.CONTRACT_VERSION,
            "contract_sha256": codex_token_contract.CONTRACT_SHA256,
            "last_seen_at": "1970-01-01T00:16:41+00:00",
            "sources": ["user-prompt-refresh"],
            "simulated": False,
        }
        with patch.object(
            codex_token_contract, "load_install_state", side_effect=self._install_state
        ), patch.object(
            codex_token_contract, "iter_receipts", return_value=iter([receipt])
        ), patch.object(
            codex_session, "find_all_jsonl_files", return_value=[]
        ):
            check = codex_doctor._token_contract_runtime_check()
        self.assertEqual(check["status"], "OK")
        self.assertIn("active-chat refresh", check["detail"])


if __name__ == "__main__":
    unittest.main()
