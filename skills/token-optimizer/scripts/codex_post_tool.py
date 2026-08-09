#!/usr/bin/env python3
"""Combined Codex PostToolUse hook: context intel + tool-result archiving.

Replaces two separate hooks.json entries (context_intel.py and
archive_result.py) with one interpreter spawn invoked DIRECTLY
(no bash launcher, no run.py dispatcher):

  "command": "TOKEN_OPTIMIZER_RUNTIME=codex /opt/homebrew/bin/python3 \
      .../scripts/codex_post_tool.py --quiet"

Contract preserved from the two-entry wiring:
  - stdin is read exactly once (1MB cap) and fed to both handlers;
  - context_intel keeps stdout passthrough (its hook entry had none
    redirected);
  - archive_result runs with stdout suppressed, matching the previous
    `>/dev/null 2>&1` redirect on its entry;
  - always exits 0; a failure in one handler never blocks the other or
    the user's tool call.

Because this file is the hook process itself (no dispatcher child),
there is no orphaned-grandchild window holding the SQLite lock when
Codex kills a timed-out hook.
"""
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if sys.version_info < (3, 9):
    sys.exit(0)


def main() -> int:
    try:
        import hook_io

        payload = hook_io.read_stdin_hook_input(1_048_576)
    except Exception:
        return 0

    def _cached_read(*_args, **_kwargs):
        return payload

    try:
        import context_intel

        context_intel.read_stdin_hook_input = _cached_read
        context_intel.handle_post_tool_use()
    except Exception:
        pass

    try:
        import archive_result

        archive_result.read_stdin_hook_input = _cached_read
        with contextlib.redirect_stdout(io.StringIO()):
            archive_result.archive_result(quiet=True)
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
