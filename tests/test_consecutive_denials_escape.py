"""consecutive_denials must persist across processes so the escape hatch can fire."""
import importlib.util
import sqlite3
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "token-optimizer" / "scripts"


def _load(name):
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(f"_{name}_cd", SCRIPTS / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SCRIPTS))


def _entry():
    return {"mtime_ns": 1, "size_bytes": 1, "tokens_est": 10, "read_count": 2,
            "last_access": time.time(), "ranges_seen": []}


def test_column_persists_across_store_reopen(tmp_path):
    ss = _load("session_store")
    a = ss.SessionStore("s1", snapshot_dir=tmp_path)
    e = _entry()
    e["consecutive_denials"] = 2
    a.upsert_file_entry("/f", e)
    a.close()
    b = ss.SessionStore("s1", snapshot_dir=tmp_path)
    assert b.get_file_entry("/f")["consecutive_denials"] == 2
    b.close()


def test_legacy_db_is_migrated(tmp_path):
    ss = _load("session_store")
    d = tmp_path / "session-store"
    d.mkdir()
    conn = sqlite3.connect(str(d / "old.db"))
    conn.execute(
        "CREATE TABLE file_reads (file_path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL,"
        " size_bytes INTEGER NOT NULL, ranges_seen TEXT NOT NULL DEFAULT '[]',"
        " tokens_est INTEGER NOT NULL DEFAULT 0, read_count INTEGER NOT NULL DEFAULT 1,"
        " content_hash TEXT, last_access REAL NOT NULL,"
        " last_replacement_fingerprint TEXT DEFAULT '', last_replacement_type TEXT DEFAULT '',"
        " repeat_replacement_count INTEGER DEFAULT 0, last_structure_reason TEXT DEFAULT '',"
        " last_structure_confidence REAL DEFAULT 0.0)"
    )
    conn.commit()
    conn.close()
    s = ss.SessionStore("old", snapshot_dir=tmp_path)
    e = _entry()
    e["consecutive_denials"] = 1
    s.upsert_file_entry("/f", e)
    assert s.get_file_entry("/f")["consecutive_denials"] == 1
    s.close()


def test_escape_hatch_fires_after_persisted_denials(tmp_path):
    ss = _load("session_store")
    rc = _load("read_cache")
    rc._log_decision = lambda *a, **k: None
    fired = []
    for _ in range(rc.MAX_CONSECUTIVE_DENIALS):
        # each iteration simulates a fresh hook process
        store = ss.SessionStore("s2", snapshot_dir=tmp_path)
        entry = store.get_file_entry("/f") or _entry()
        fired.append(rc._check_escape_hatch(
            entry, store, "/f", "s2", "block", 0, 0, 10, "py", False, None, False, True))
        if not fired[-1]:
            store.upsert_file_entry("/f", entry)
        store.close()
    assert fired == [False] * (rc.MAX_CONSECUTIVE_DENIALS - 1) + [True]
