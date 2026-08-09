"""Mechanical token-management contract and Codex SessionStart receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import codex_io
from runtime_env import codex_home

CONTRACT_VERSION = "studio-token-management-v2"
CONTRACT_BEGIN = "<!-- STUDIO_TOKEN_MANAGEMENT:BEGIN -->"
CONTRACT_END = "<!-- STUDIO_TOKEN_MANAGEMENT:END -->"
CONTRACT_TEXT = f"""{CONTRACT_BEGIN}
[Studio token management: {CONTRACT_VERSION}]
- Default to the light ceremony for simple, read-only, and low-risk work: use bounded reads and the smallest useful response; token management alone does not require a checkpoint, worktree, lifecycle receipt, advisor review, or wiki writeback.
- Full ceremony is reserved for substantive, multi-step, long-running, risky, external-action, multi-repo, or otherwise durable work. Collapse overlapping checkpoints, receipts, and lifecycle updates when one artifact satisfies the same requirement.
- Ceremony classification never bypasses independent gates for secrets, money/spending, signing, trading, outbound sends, deployment, destructive actions, repository integrity, or deterministic verification. Run those gates before or regardless of any light label.
- Early call-count and context-percentage thresholds are task-aware warnings, not automatic routing or handoff triggers. Re-check scope and checkpoint when the task needs it.
- Keep a mandatory durable checkpoint before actual or native compaction.
- Handoff only at a safe boundary when completion in the current context is no longer credible; otherwise continue the current task.
- A stale chat without this v2 contract remains under its prior stricter policy. Missing refresh evidence never grants permission.
Injection, refresh, and host receipts are mechanical; task and safety decisions remain agent-directed.
{CONTRACT_END}"""

CONTRACT_SHA256 = hashlib.sha256(CONTRACT_TEXT.encode("utf-8")).hexdigest()

LIGHT_CEREMONY = "light"
FULL_CEREMONY = "full"
FULL_CEREMONY_SIGNALS = frozenset(
    {
        "substantive",
        "multi-step",
        "long",
        "long-running",
        "risky",
        "external",
        "external-action",
        "multi-repo",
        "durable",
        "push",
        "deploy",
        "send",
        "spend",
        "money",
        "sign",
        "trade",
        "destructive",
        "secret",
        "repository-write",
    }
)
INDEPENDENT_ACTION_GATES = (
    "secrets",
    "money/spending",
    "signing",
    "trading",
    "outbound sends",
    "deployment",
    "destructive actions",
    "repository integrity",
    "deterministic verification",
)
REQUIRED_CONTRACT_PHRASES = (
    CONTRACT_VERSION,
    "Default to the light ceremony",
    "Full ceremony is reserved",
    "Ceremony classification never bypasses independent gates",
    *INDEPENDENT_ACTION_GATES,
    "task-aware warnings, not automatic routing or handoff triggers",
    "mandatory durable checkpoint before actual or native compaction",
    "completion in the current context is no longer credible",
    "prior stricter policy",
)


@dataclass(frozen=True)
class TaskPolicy:
    ceremony: str
    independent_gates: tuple[str, ...] = INDEPENDENT_ACTION_GATES
    early_threshold_behavior: str = "warning"
    checkpoint_before_native_compaction: bool = True
    handoff_condition: str = "safe-boundary-and-completion-not-credible"


def classify_ceremony(
    *,
    signals: Iterable[str] = (),
    requested_ceremony: str | None = None,
) -> str:
    """Classify ceremony without allowing a requested light label to hide risk."""
    normalized = {str(signal).strip().lower() for signal in signals if str(signal).strip()}
    requested = str(requested_ceremony or "").strip().lower()
    if requested == FULL_CEREMONY or normalized.intersection(FULL_CEREMONY_SIGNALS):
        return FULL_CEREMONY
    return LIGHT_CEREMONY


def policy_for_task(
    *,
    signals: Iterable[str] = (),
    requested_ceremony: str | None = None,
) -> TaskPolicy:
    policy = TaskPolicy(
        ceremony=classify_ceremony(
            signals=signals,
            requested_ceremony=requested_ceremony,
        )
    )
    validate_policy(policy)
    return policy


def validate_policy(policy: TaskPolicy) -> None:
    """Fail closed if ceremony logic ever strips an independent gate/backstop."""
    if policy.ceremony not in {LIGHT_CEREMONY, FULL_CEREMONY}:
        raise ValueError(f"unknown ceremony: {policy.ceremony}")
    if policy.independent_gates != INDEPENDENT_ACTION_GATES:
        raise ValueError("ceremony must preserve every independent action gate")
    if policy.early_threshold_behavior != "warning":
        raise ValueError("early token thresholds must remain warnings")
    if not policy.checkpoint_before_native_compaction:
        raise ValueError("native compaction requires a durable checkpoint")
    if policy.handoff_condition != "safe-boundary-and-completion-not-credible":
        raise ValueError("handoff must require both a safe boundary and lost completion credibility")


def validate_contract_text(text: str) -> None:
    """Validate the injected text against the safety and compaction invariants."""
    missing = [phrase for phrase in REQUIRED_CONTRACT_PHRASES if phrase not in text]
    if missing:
        raise ValueError(f"token-management contract missing invariants: {', '.join(missing)}")
    validate_policy(policy_for_task())


validate_contract_text(CONTRACT_TEXT)

RECEIPT_SCHEMA_VERSION = 1
INSTALL_SCHEMA_VERSION = 1
RECEIPT_RETENTION_DAYS = 14
MAX_RECEIPT_SOURCES = 8
SIMULATED_ENV = "TOKEN_OPTIMIZER_SIMULATED"
STATE_DIR_NAME = "token-optimizer"
RECEIPT_DIR_NAME = "session-start-receipts"
INSTALL_STATE_NAME = "codex-install-state.json"

_CONTRACT_RE = re.compile(
    rf"{re.escape(CONTRACT_BEGIN)}.*?{re.escape(CONTRACT_END)}",
    re.DOTALL,
)
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")


def strip_contract_blocks(text: str) -> str:
    """Remove exact sentinel-delimited contract blocks, including stale versions."""
    return _CONTRACT_RE.sub("", text or "").strip()


def compose_session_context(restored_context: str = "") -> str:
    """Return exactly one current contract followed by any continuity context."""
    continuity = strip_contract_blocks(restored_context)
    return "\n\n".join(part for part in (CONTRACT_TEXT, continuity) if part)


def state_dir(home: Path | None = None) -> Path:
    return (home or codex_home()) / STATE_DIR_NAME


def receipt_dir(home: Path | None = None) -> Path:
    return state_dir(home) / RECEIPT_DIR_NAME


def install_state_path(home: Path | None = None) -> Path:
    return state_dir(home) / INSTALL_STATE_NAME


def receipt_path(session_id: str, home: Path | None = None) -> Path:
    raw = str(session_id).strip()
    safe = _SAFE_ID_RE.sub("-", raw).strip("-")[:96] or "unknown"
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:12]
    return receipt_dir(home) / f"{safe}-{digest}.json"


def _utc_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _is_truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def prune_receipts(*, home: Path | None = None, now: float | None = None) -> None:
    """Delete only expired receipt JSON files from Token Optimizer's state dir."""
    current = time.time() if now is None else now
    cutoff = current - RECEIPT_RETENTION_DAYS * 86400
    directory = receipt_dir(home)
    if not directory.exists():
        return
    for path in directory.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def record_contract_receipt(
    hook_input: dict[str, Any],
    *,
    source: str | None = None,
    home: Path | None = None,
    now: float | None = None,
) -> Path | None:
    """Atomically record a bounded, content-free contract-delivery receipt."""
    session_id = hook_input.get("session_id")
    transcript_path = hook_input.get("transcript_path")
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    if not isinstance(transcript_path, str) or not transcript_path.strip():
        return None

    # Import lazily so this pure contract module stays cheap for the installer.
    import codex_session

    if not codex_session.is_codex_session_path(transcript_path):
        return None

    timestamp = time.time() if now is None else now
    path = receipt_path(session_id, home)
    existing = _load_object(path)
    sources = existing.get("sources")
    if not isinstance(sources, list):
        sources = []
    receipt_source = str(source or hook_input.get("source") or "startup").strip().lower() or "startup"
    sources = [str(item) for item in sources[-(MAX_RECEIPT_SOURCES - 1):]] + [receipt_source]
    first_seen_at = existing.get("first_seen_at")
    if not isinstance(first_seen_at, str):
        first_seen_at = _utc_iso(timestamp)

    payload = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "contract_sha256": CONTRACT_SHA256,
        "session_id": session_id,
        "first_seen_at": first_seen_at,
        "last_seen_at": _utc_iso(timestamp),
        "sources": sources,
        "transcript_name": Path(transcript_path).name,
        "simulated": _is_truthy_env(SIMULATED_ENV),
    }
    codex_io.atomic_write_json(path, payload)
    prune_receipts(home=home, now=timestamp)
    return path


def record_session_start_receipt(
    hook_input: dict[str, Any],
    *,
    home: Path | None = None,
    now: float | None = None,
) -> Path | None:
    return record_contract_receipt(hook_input, home=home, now=now)


def record_global_install(*, home: Path | None = None, now: float | None = None) -> Path:
    """Record when the global contract became authoritative for new sessions."""
    timestamp = time.time() if now is None else now
    path = install_state_path(home)
    codex_io.atomic_write_json(
        path,
        {
            "schema_version": INSTALL_SCHEMA_VERSION,
            "contract_version": CONTRACT_VERSION,
            "contract_sha256": CONTRACT_SHA256,
            "contract_installed_at": _utc_iso(timestamp),
        },
    )
    return path


def load_install_state(home: Path | None = None) -> dict[str, Any]:
    return _load_object(install_state_path(home))


def load_receipt(session_id: str, home: Path | None = None) -> dict[str, Any]:
    return _load_object(receipt_path(session_id, home))


def iter_receipts(home: Path | None = None) -> Iterable[dict[str, Any]]:
    """Yield bounded receipt objects without exposing transcript contents."""
    directory = receipt_dir(home)
    if not directory.exists():
        return
    for path in sorted(directory.glob("*.json")):
        receipt = _load_object(path)
        if receipt:
            yield receipt


def receipt_is_current(receipt: dict[str, Any]) -> bool:
    return (
        receipt.get("contract_version") == CONTRACT_VERSION
        and receipt.get("contract_sha256") == CONTRACT_SHA256
    )


def needs_contract_refresh(session_id: Any, home: Path | None = None) -> bool:
    if not isinstance(session_id, str) or not session_id.strip():
        return False
    receipt = load_receipt(session_id, home)
    return receipt.get("simulated") is True or not receipt_is_current(receipt)
