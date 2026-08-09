#!/usr/bin/env python3
"""Codex-specific install readiness checks for Token Optimizer."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_env import codex_home, detect_runtime, runtime_home
import codex_session
import codex_state
import codex_statusline
import codex_hook_bridge
import codex_token_contract

HOST_SESSION_START_GRACE_SECONDS = 1.0

SUPPORTED_HOOK_EVENTS = {
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "SessionStart",
    "UserPromptSubmit",
    "Stop",
    "SubagentStart",
    "SubagentStop",
}
BASH_ONLY_EVENTS = {"PreToolUse", "PermissionRequest", "PostToolUse"}
REQUIRED_FILES = (
    ".codex-plugin/plugin.json",
    "hooks/python-launcher.sh",
    "hooks/run.py",
    "skills/token-optimizer/scripts/codex_hook_bridge.py",
    "skills/token-optimizer/scripts/codex_session.py",
    "skills/token-optimizer/scripts/codex_token_contract.py",
    "skills/token-optimizer/scripts/codex_compact_prompt.py",
    "skills/token-optimizer/scripts/codex_statusline.py",
    "skills/token-optimizer/scripts/codex_install.py",
    "skills/token-optimizer/scripts/bash_hook.py",
    "skills/token-optimizer/scripts/bash_compress.py",
    "skills/token-optimizer/scripts/context_intel.py",
    "skills/token-optimizer/scripts/archive_result.py",
    "skills/token-optimizer/scripts/measure.py",
    "skills/token-optimizer/scripts/outline.py",
    "skills/token-optimizer/scripts/runtime_env.py",
)

SKILL_INSTALL_FILES = (
    "SKILL.md",
    "scripts/codex_hook_bridge.py",
    "scripts/codex_session.py",
    "scripts/codex_token_contract.py",
    "scripts/codex_compact_prompt.py",
    "scripts/codex_statusline.py",
    "scripts/codex_install.py",
    "scripts/measure.py",
    "scripts/runtime_env.py",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _is_plugin_repo(root: Path) -> bool:
    return (root / ".codex-plugin" / "plugin.json").exists()


def _check(status: str, name: str, detail: str) -> dict[str, str]:
    return {"status": status, "name": name, "detail": detail}


def _load_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)


def _codex_home_warnings() -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    home = codex_home()
    raw = os.environ.get("CODEX_HOME", "").strip()

    if raw:
        requested = Path(raw).expanduser()
        try:
            requested_resolved = requested.resolve(strict=False)
        except (OSError, ValueError):
            requested_resolved = requested
        if requested_resolved != home.resolve(strict=False):
            checks.append(_check("FAIL", "CODEX_HOME", f"ignored unsafe CODEX_HOME={raw!r}; using {home}"))
        else:
            checks.append(_check("OK", "CODEX_HOME", str(home)))
    else:
        checks.append(_check("OK", "CODEX_HOME", f"default {home}"))

    if home.exists():
        checks.append(_check("OK", "Codex home exists", str(home)))
    else:
        checks.append(_check("WARN", "Codex home exists", f"{home} does not exist yet"))

    parent = home if home.exists() else home.parent
    if os.access(parent, os.W_OK):
        checks.append(_check("OK", "Codex storage writable", str(parent)))
    else:
        checks.append(_check("FAIL", "Codex storage writable", f"{parent} is not writable"))

    return checks


def _manifest_checks(root: Path) -> list[dict[str, str]]:
    path = root / ".codex-plugin" / "plugin.json"
    data, error = _load_json(path)
    if error:
        return [_check("FAIL", "Plugin manifest", error)]
    if not isinstance(data, dict):
        return [_check("FAIL", "Plugin manifest", "manifest is not a JSON object")]

    checks = []
    name = data.get("name")
    version = data.get("version")
    if name == "token-optimizer":
        checks.append(_check("OK", "Plugin name", name))
    else:
        checks.append(_check("FAIL", "Plugin name", f"expected token-optimizer, got {name!r}"))
    if isinstance(version, str) and version.strip():
        checks.append(_check("OK", "Plugin version", version))
    else:
        checks.append(_check("FAIL", "Plugin version", "missing or blank version"))
    return checks


def _hook_config_checks(root: Path) -> list[dict[str, str]]:
    path = root / ".codex" / "hooks.json"
    data, error = _load_json(path)
    if error:
        return [_check("FAIL", "Codex hooks", error)]
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        return [_check("FAIL", "Codex hooks", "expected top-level hooks object")]

    checks = []
    hooks = data["hooks"]
    unsupported = sorted(set(hooks) - SUPPORTED_HOOK_EVENTS)
    if unsupported:
        checks.append(_check("FAIL", "Hook events", f"unsupported events: {', '.join(unsupported)}"))
    else:
        checks.append(_check("OK", "Hook events", ", ".join(sorted(hooks))))

    for event_name, groups in hooks.items():
        if not isinstance(groups, list):
            checks.append(_check("FAIL", f"{event_name} groups", "expected list"))
            continue
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict):
                checks.append(_check("FAIL", f"{event_name}[{group_index}]", "expected object"))
                continue
            matcher = group.get("matcher")
            if event_name in BASH_ONLY_EVENTS and matcher not in (None, "", "Bash", "^Bash$"):
                checks.append(
                    _check("FAIL", f"{event_name} matcher", f"Codex currently supports Bash hook payloads, got {matcher!r}")
                )
            for hook_index, hook in enumerate(group.get("hooks", [])):
                if not isinstance(hook, dict):
                    checks.append(_check("FAIL", f"{event_name} hook", "expected object"))
                    continue
                if hook.get("type") != "command":
                    checks.append(_check("FAIL", f"{event_name} hook", f"unsupported type {hook.get('type')!r}"))
                if hook.get("async"):
                    checks.append(_check("FAIL", f"{event_name} hook", "async hooks are skipped by current Codex"))
                timeout = hook.get("timeout")
                if isinstance(timeout, (int, float)) and timeout > 300:
                    checks.append(_check("WARN", f"{event_name} hook timeout", f"timeout={timeout} may be in milliseconds (Codex expects seconds)"))
                command = hook.get("command", "")
                if not isinstance(command, str) or not command.strip():
                    checks.append(_check("FAIL", f"{event_name} hook", "missing command"))
                elif _command_mentions_missing_path(root, command):
                    checks.append(_check("FAIL", f"{event_name} hook {hook_index}", f"missing file in command: {command}"))

    if not any(c["status"] == "FAIL" and c["name"].startswith("Hook") for c in checks):
        checks.append(_check("OK", "Hook commands", "all referenced repo files exist"))
    return checks


def _command_mentions_missing_path(root: Path, command: str) -> bool:
    for match in re.findall(r"(?:(?:\\.|/)?(?:hooks|skills)/[A-Za-z0-9_./-]+)", command):
        candidate = root / match.lstrip("./")
        if not candidate.exists():
            return True
    return False


def _required_file_checks(root: Path) -> list[dict[str, str]]:
    missing = [rel for rel in REQUIRED_FILES if not (root / rel).exists()]
    if missing:
        return [_check("FAIL", "Required files", ", ".join(missing))]
    return [_check("OK", "Required files", f"{len(REQUIRED_FILES)} present")]


def _skill_install_checks() -> list[dict[str, str]]:
    root = _skill_root()
    missing = [rel for rel in SKILL_INSTALL_FILES if not (root / rel).exists()]
    if missing:
        return [_check("FAIL", "Installed skill files", ", ".join(missing))]
    return [
        _check("OK", "Install shape", f"standalone Codex skill at {root}"),
        _check("OK", "Installed skill files", f"{len(SKILL_INSTALL_FILES)} present"),
    ]


def _compact_prompt_check() -> dict[str, str]:
    config_path = codex_home() / "config.toml"
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return _check("FAIL", "Compact prompt", f"{config_path} not found; run measure.py codex-compact-prompt --install")
    expected = codex_home() / "token-optimizer" / "codex-compact-prompt.md"
    if str(expected) in text and expected.exists():
        return _check("OK", "Compact prompt", str(expected))
    if "compact_prompt" in text or "experimental_compact_prompt_file" in text:
        return _check("WARN", "Compact prompt", "custom compact prompt configured")
    return _check("FAIL", "Compact prompt", "not configured yet; run measure.py codex-compact-prompt --install")


def _status_line_check() -> dict[str, str]:
    state = codex_statusline.status()
    if state.startswith("configured: Token Optimizer"):
        return _check("OK", "Codex CLI status line", state)
    if state.startswith("configured: custom"):
        return _check("WARN", "Codex CLI status line", state)
    return _check("WARN", "Codex CLI status line", "not configured; rerun codex-install with --enable-status-line")


def _global_hook_check() -> dict[str, str]:
    hooks_path = codex_home() / "hooks.json"
    data, error = _load_json(hooks_path)
    if error:
        return _check("FAIL", "Global hooks", f"{hooks_path} not found; run measure.py codex-install")
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        return _check("FAIL", "Global hooks", f"{hooks_path} has no hooks object")
    if "token-optimizer/scripts" in json.dumps(data, sort_keys=True):
        return _check("OK", "Global hooks", str(hooks_path))
    return _check("WARN", "Global hooks", f"no Token Optimizer hooks in {hooks_path}; run measure.py codex-install")


def _parse_epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError, OSError):
        return None


def _token_contract_smoke_check() -> dict[str, str]:
    output = codex_hook_bridge.build_session_start_context("startup", None)
    marker_count = output.count(codex_token_contract.CONTRACT_BEGIN)
    try:
        codex_token_contract.validate_contract_text(output)
    except ValueError as exc:
        return _check("FAIL", "Token contract adapter", str(exc))
    if marker_count != 1 or codex_token_contract.CONTRACT_VERSION not in output:
        return _check("FAIL", "Token contract adapter", "synthetic startup did not emit exactly one current contract")
    return _check(
        "OK",
        "Token contract adapter",
        f"{codex_token_contract.CONTRACT_VERSION} {codex_token_contract.CONTRACT_SHA256[:12]}; "
        "SessionStart plus stale-session UserPromptSubmit refresh",
    )


def _token_contract_runtime_check() -> dict[str, str]:
    state = codex_token_contract.load_install_state()
    installed_at = _parse_epoch(state.get("contract_installed_at"))
    if (
        state.get("contract_version") != codex_token_contract.CONTRACT_VERSION
        or state.get("contract_sha256") != codex_token_contract.CONTRACT_SHA256
        or installed_at is None
    ):
        return _check(
            "WARN",
            "Token contract host receipt",
            "global contract install state is missing or stale; rerun measure.py codex-install",
        )

    boundary = installed_at + HOST_SESSION_START_GRACE_SECONDS
    # CODEX_THREAD_ID is trusted host metadata. If a host deliberately shares
    # it with a child context, the exact version/hash-bound receipt represents
    # that same delivered contract context rather than user-controlled input.
    current_session_id = os.environ.get("CODEX_THREAD_ID", "").strip()
    if current_session_id:
        current_receipt = codex_token_contract.load_receipt(current_session_id)
        if not current_receipt:
            return _check(
                "FAIL",
                "Token contract host receipt",
                f"no receipt for current session {current_session_id}",
            )
        if current_receipt.get("session_id") != current_session_id:
            return _check("FAIL", "Token contract host receipt", "current-session receipt identity mismatch")
        if not codex_token_contract.receipt_is_current(current_receipt):
            return _check("FAIL", "Token contract host receipt", "current-session receipt version/hash mismatch")
        if current_receipt.get("simulated") is not False:
            return _check("FAIL", "Token contract host receipt", "current session has only a simulated receipt")
        # The receipt is content-bound to the exact contract version/hash.
        # Reinstalling identical content may advance install-state time, but it
        # does not invalidate this host-delivered receipt.
        return _check(
            "OK",
            "Token contract host receipt",
            f"host-invoked receipt for current session {current_session_id}",
        )

    current_refresh: dict[str, Any] | None = None
    for receipt in codex_token_contract.iter_receipts():
        last_seen_at = _parse_epoch(receipt.get("last_seen_at"))
        if (
            last_seen_at is not None
            and last_seen_at >= installed_at
            and receipt.get("simulated") is False
            and codex_token_contract.receipt_is_current(receipt)
            and "user-prompt-refresh" in receipt.get("sources", [])
        ):
            if current_refresh is None or last_seen_at > current_refresh["last_seen_epoch"]:
                current_refresh = {**receipt, "last_seen_epoch": last_seen_at}

    newest: dict[str, Any] | None = None
    saw_post_install_subagent = False
    for path, _mtime, _project in codex_session.find_all_jsonl_files(days=30, max_files=500):
        identity = codex_session.session_identity(path)
        if not identity or identity["started_at"] < boundary:
            continue
        # Internal collaboration subagents do not receive the host-level
        # SessionStart/UserPromptSubmit contract hooks. They therefore cannot
        # produce a legitimate host receipt and must not displace the newest
        # qualifying top-level session in this readiness check.
        if identity.get("thread_source") == "subagent":
            saw_post_install_subagent = True
            continue
        if newest is None or identity["started_at"] > newest["started_at"]:
            newest = identity

    if newest is None:
        if current_refresh is not None:
            return _check(
                "OK",
                "Token contract host receipt",
                f"host-invoked active-chat refresh for session {current_refresh.get('session_id')}",
            )
        if saw_post_install_subagent:
            return _check(
                "FAIL",
                "Token contract host receipt",
                "only unhooked subagent sessions were found after contract installation",
            )
        return _check(
            "WARN",
            "Token contract host receipt",
            "installed; pending the first post-install SessionStart or active-chat prompt refresh",
        )

    session_id = newest["session_id"]
    receipt = codex_token_contract.load_receipt(session_id)
    if not receipt:
        return _check(
            "FAIL",
            "Token contract host receipt",
            f"no receipt for qualifying session {session_id}; hook was not invoked or receipt writing failed",
        )
    if receipt.get("session_id") != session_id:
        return _check("FAIL", "Token contract host receipt", f"receipt identity mismatch for qualifying session {session_id}")
    if receipt.get("contract_version") != codex_token_contract.CONTRACT_VERSION:
        return _check("FAIL", "Token contract host receipt", f"stale contract receipt for qualifying session {session_id}")
    if receipt.get("contract_sha256") != codex_token_contract.CONTRACT_SHA256:
        return _check("FAIL", "Token contract host receipt", f"contract hash mismatch for qualifying session {session_id}")
    if receipt.get("simulated") is not False:
        return _check("FAIL", "Token contract host receipt", f"qualifying session {session_id} has only a simulated receipt")
    return _check("OK", "Token contract host receipt", f"host-invoked receipt for session {session_id}")


def _global_hook_config_checks() -> list[dict[str, str]]:
    hooks_path = codex_home() / "hooks.json"
    data, error = _load_json(hooks_path)
    if error:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        return []
    return _validate_hook_structure(data, hooks_path)


def _validate_hook_structure(data: dict[str, Any], source_path: Path) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    hooks = data["hooks"]
    unsupported = sorted(set(hooks) - SUPPORTED_HOOK_EVENTS)
    if unsupported:
        checks.append(_check("FAIL", f"Hook events ({source_path.name})", f"unsupported events: {', '.join(unsupported)}"))

    for event_name, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks", []):
                if not isinstance(hook, dict):
                    continue
                if hook.get("async"):
                    checks.append(_check("FAIL", f"{event_name} hook", "async hooks are skipped by current Codex"))
                timeout = hook.get("timeout")
                if isinstance(timeout, (int, float)) and timeout > 300:
                    checks.append(_check("WARN", f"{event_name} hook timeout", f"timeout={timeout} may be in milliseconds (Codex expects seconds)"))
    return checks


def _global_hooks_data() -> dict[str, Any]:
    """Global user hooks, or {} when absent/malformed (they are optional)."""
    data, error = _load_json(codex_home() / "hooks.json")
    if error or not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        return {}
    return data


def _global_has_token_optimizer_hooks() -> bool:
    return "token-optimizer/scripts" in json.dumps(_global_hooks_data(), sort_keys=True)


def _project_hook_check(project: Path) -> dict[str, str]:
    hooks_path = project / ".codex" / "hooks.json"
    global_installed = _global_has_token_optimizer_hooks()
    data, error = _load_json(hooks_path)
    if error:
        if global_installed:
            return _check("OK", "Project hooks", f"{hooks_path} not found; global user hook is the single authority")
        return _check("WARN", "Project hooks", f"{hooks_path} not found; per-project hooks are optional when global hooks are installed")
    if not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
        return _check("FAIL", "Project hooks", f"{hooks_path} has no hooks object")
    if "token-optimizer/scripts" in json.dumps(data, sort_keys=True):
        if global_installed:
            return _check(
                "FAIL",
                "Project hooks",
                f"duplicate Token Optimizer hooks in {hooks_path}; use the global user hook as the single authority",
            )
        return _check("OK", "Project hooks", f"Token Optimizer hooks installed in {hooks_path}")
    if global_installed:
        return _check("OK", "Project hooks", f"none in {hooks_path}; global user hook is the single authority")
    return _check("WARN", "Project hooks", f"no Token Optimizer hooks installed in {hooks_path}; manual refresh mode avoids visible Codex hook rows")


def _project_feature_checks(project: Path) -> list[dict[str, str]]:
    hooks_path = project / ".codex" / "hooks.json"
    data, error = _load_json(hooks_path)
    project_hooks: dict[str, Any] = {}
    if not error and isinstance(data, dict) and isinstance(data.get("hooks"), dict):
        project_hooks = data.get("hooks", {})
    global_hooks = _global_hooks_data().get("hooks", {})
    if not project_hooks and not global_hooks:
        return []

    def _feature_scope(event: str, matcher: str | None, needles: tuple[str, ...]) -> str | None:
        """Where the feature is wired: 'project', 'global', or None."""
        for needle in needles:
            if _has_project_hook(project_hooks, event, matcher, needle):
                return "project"
        for needle in needles:
            if _has_project_hook(global_hooks, event, matcher, needle):
                return "global"
        return None

    checks = []
    if _feature_scope("PreToolUse", "Bash", ("bash_hook.py",)):
        checks.append(_check("OK", "Feature: Bash compression", "enabled for PreToolUse(Bash)"))
    else:
        checks.append(_check("OK", "Feature: Bash compression", "off by default to avoid visible Codex PreToolUse hook spam"))

    required_features = (
        ("Session continuity and dashboard refresh", "Stop", None, ("session-end-flush",)),
    )
    optional_noisy_features = (
        ("Prompt quality nudges", "UserPromptSubmit", None, ("codex_hook_bridge.py",)),
        ("Subagent sprawl tracking", "SubagentStart", None, ("subagent-start",)),
        ("Tool output archive", "PostToolUse", "Bash", ("archive_result.py", "codex_post_tool.py")),
        ("Context intelligence", "PostToolUse", "Bash", ("context_intel.py", "codex_post_tool.py")),
    )
    for feature, event, matcher, needles in required_features:
        scope = _feature_scope(event, matcher, needles)
        if scope:
            checks.append(_check("OK", f"Feature: {feature}", f"available in current Codex adapter ({scope} hooks)"))
        else:
            checks.append(_check("WARN", f"Feature: {feature}", f"manual refresh mode; install low-noise Stop hook with measure.py codex-install --project {project}"))
    for feature, event, matcher, needles in optional_noisy_features:
        scope = _feature_scope(event, matcher, needles)
        if scope:
            checks.append(_check("OK", f"Optional feature: {feature}", f"enabled via {scope} hooks; Codex Desktop will show visible hook rows"))
        else:
            checks.append(_check("OK", f"Optional feature: {feature}", "off by default to avoid visible Codex hook rows"))

    repo_root = _repo_root()
    parser_path = (
        repo_root / "skills/token-optimizer/scripts/codex_session.py"
        if _is_plugin_repo(repo_root)
        else _skill_root() / "scripts/codex_session.py"
    )
    if parser_path.exists():
        checks.append(_check("OK", "Feature: Dashboard session parsing", "available in current Codex adapter"))
    else:
        checks.append(_check("FAIL", "Feature: Dashboard session parsing", f"missing {parser_path}"))

    return checks


def _has_project_hook(hooks: dict[str, Any], event: str, matcher: str | None, command_needle: str) -> bool:
    groups = hooks.get(event)
    if not isinstance(groups, list):
        return False
    for group in groups:
        if not isinstance(group, dict):
            continue
        if matcher is not None and group.get("matcher") not in (matcher, f"^{matcher}$"):
            continue
        for hook in group.get("hooks", []):
            if not isinstance(hook, dict) or hook.get("type") != "command":
                continue
            command = hook.get("command", "")
            if isinstance(command, str) and command_needle in command:
                return True
    return False


def _state_db_checks() -> list[dict[str, str]]:
    """Report readability of Codex's versioned state/goals SQLite databases.

    These power subagent/memory/goal measurement (codex-state). Absence is a
    WARN, not a FAIL: Codex creates them lazily once it records runtime state.
    """
    if detect_runtime() != "codex":
        return []
    status = codex_state.state_db_status()
    checks: list[dict[str, str]] = []
    if status["state_db"]:
        if status["state_readable"]:
            checks.append(_check("OK", "Codex state DB", status["state_db"]))
        else:
            checks.append(_check("WARN", "Codex state DB", f"{status['state_db']} present but no thread rows yet"))
    else:
        checks.append(_check("WARN", "Codex state DB", "no state_*.sqlite yet; subagent/memory metrics unavailable until Codex writes one"))
    if status["goals_present"]:
        checks.append(_check("OK", "Codex goals DB", status["goals_db"]))
    else:
        checks.append(_check("OK", "Codex goals DB", "no goals_*.sqlite yet (goals are opt-in)"))
    return checks


def run_checks(project: Path | None = None) -> list[dict[str, str]]:
    root = _repo_root()
    project = project or Path.cwd()
    checks = [
        _check("OK", "Repo root", str(root)),
        _check("OK", "Detected runtime", detect_runtime()),
        _check("OK", "Runtime home", str(runtime_home())),
    ]
    checks.extend(_codex_home_warnings())
    checks.extend(_state_db_checks())
    if _is_plugin_repo(root):
        checks.extend(_required_file_checks(root))
        checks.extend(_manifest_checks(root))
        checks.append(_check("OK", "Codex hook template", "generated by codex_install.py"))
    else:
        checks.extend(_skill_install_checks())
    checks.append(_compact_prompt_check())
    checks.append(_token_contract_smoke_check())
    checks.append(_status_line_check())
    checks.append(_global_hook_check())
    checks.extend(_global_hook_config_checks())
    checks.append(_token_contract_runtime_check())
    checks.append(_project_hook_check(project))
    checks.extend(_project_feature_checks(project))
    return checks


def _print_text(checks: list[dict[str, str]]) -> None:
    print("\nToken Optimizer Codex Doctor")
    print("=" * 28)
    for check in checks:
        print(f"[{check['status']}] {check['name']}: {check['detail']}")
    counts = {status: sum(1 for check in checks if check["status"] == status) for status in ("OK", "WARN", "FAIL")}
    print(f"\nSummary: {counts['OK']} OK, {counts['WARN']} WARN, {counts['FAIL']} FAIL")
    print("\nThis checks Token Optimizer's Codex integration (token/optimization focus).")
    print("For generic Codex runtime/auth/network health, run the native `codex doctor`.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check Token Optimizer Codex adapter readiness.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    parser.add_argument("--project", default=".", help="Project directory whose .codex/hooks.json should be checked")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project = Path(args.project).expanduser().resolve(strict=False)
    checks = run_checks(project=project)
    if args.json:
        print(json.dumps({"project": str(project), "checks": checks}, indent=2))
    else:
        _print_text(checks)
    return 1 if any(check["status"] == "FAIL" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
