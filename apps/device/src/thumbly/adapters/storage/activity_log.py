"""Durable outcomes, keyed request fingerprints; never payloads or typed text."""

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from pathlib import Path
from uuid import UUID

from thumbly.domain.errors import ThumblyError

KEEP_SECONDS = 30 * 86_400  # Operations are kept 30 days.
# Upper bound on one operations page as sent to the dashboard; rows past the budget are left
# for the next page.
PAGE_BYTES = 24 * 1024
PAGE_LIMIT = 200
# Lease bookkeeping around every action; the dashboard hides it unless asked.
BOOKKEEPING = ("acquire", "release")
ACTIVE_STATES = ("QUEUED", "DISPATCHED")


class ActivityLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                fingerprint TEXT NOT NULL, state TEXT NOT NULL, result TEXT,
                created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS profiles (device TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id TEXT PRIMARY KEY, value TEXT NOT NULL, approved INTEGER NOT NULL,
                created REAL NOT NULL, last_used REAL, issuer TEXT);
            CREATE TABLE IF NOT EXISTS oauth_grants (
                digest TEXT PRIMARY KEY, kind TEXT NOT NULL, client_id TEXT NOT NULL,
                family TEXT NOT NULL, value TEXT NOT NULL, expires REAL NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS owner_labels (owner TEXT PRIMARY KEY, label TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS operations_created ON operations(created DESC, id DESC);
        """)
        # Changes on every write and never repeats, across restarts too: seeded from the clock but
        # always past the last persisted value. The dashboard watches it in the status digest to
        # refetch history.
        persisted = self.db.execute("SELECT value FROM secrets WHERE name='revision'").fetchone()
        self.revision = max(int(time.time() * 1000), int(persisted[0]) + 1 if persisted else 0)
        self._persist_revision()
        self.db.execute("INSERT OR IGNORE INTO secrets VALUES ('fingerprint', ?)", (secrets.token_hex(32),))
        self.secret = bytes.fromhex(
            self.db.execute("SELECT value FROM secrets WHERE name='fingerprint'").fetchone()[0]
        )
        self.db.commit()

    def _persist_revision(self) -> None:
        self.db.execute("INSERT OR REPLACE INTO secrets VALUES ('revision', ?)", (str(self.revision),))

    def _bump(self) -> None:
        """Call before the write's commit so the new revision is persisted with it."""
        self.revision += 1
        self._persist_revision()

    def journal_revision(self) -> int:
        return self.revision

    def recover_interrupted(self):
        self.db.execute("UPDATE operations SET state='OUTCOME_UNKNOWN' WHERE state='DISPATCHED'")
        self.db.execute("UPDATE operations SET state='CANCELLED' WHERE state='QUEUED'")
        # Activity older than KEEP_SECONDS is dropped at start, so the log never grows forever.
        self.db.execute("DELETE FROM operations WHERE created<?", (time.time() - KEEP_SECONDS,))
        self._bump()
        self.db.commit()

    def existing(self, operation_id, owner, kind, payload):
        row = self.db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
        if row is None:
            return None
        fingerprint = hmac.new(
            self.secret, json.dumps([kind, payload], sort_keys=True).encode(), hashlib.sha256
        ).hexdigest()
        if row["owner"] != owner or not hmac.compare_digest(row["fingerprint"], fingerprint):
            raise ThumblyError("OPERATION_CONFLICT")
        return self._public(row)

    def reserve(self, operation_id, owner, kind, payload):
        try:
            UUID(operation_id)
        except (ValueError, TypeError):
            raise ThumblyError("INVALID_OPERATION_ID", "Use a UUID operation ID", 400) from None
        fingerprint = hmac.new(
            self.secret, json.dumps([kind, payload], sort_keys=True).encode(), hashlib.sha256
        ).hexdigest()
        previous = self.db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
        if previous:
            if previous["owner"] != owner or not hmac.compare_digest(previous["fingerprint"], fingerprint):
                raise ThumblyError("OPERATION_CONFLICT")
            return self._public(previous), False
        now = time.time()
        self.db.execute(
            "INSERT INTO operations VALUES (?,?,?,?,?,?,?,?)",
            (operation_id, owner, kind, fingerprint, "QUEUED", None, now, now),
        )
        self._bump()
        self.db.commit()
        return self.get(operation_id, owner), True

    @staticmethod
    def _public(row):
        return {
            k: (json.loads(row[k]) if row[k] else None) if k == "result" else row[k]
            for k in ("id", "kind", "state", "result", "created", "updated")
        }

    def get(self, operation_id, owner):
        row = self.db.execute(
            "SELECT * FROM operations WHERE id=? AND owner=?", (operation_id, owner)
        ).fetchone()
        if row is None:
            raise ThumblyError("OPERATION_NOT_FOUND", status=404)
        return self._public(row)

    def list_operations(self, limit=50, before=None):
        """Newest first, across owners, each labelled with the client that owns the session."""
        limit = max(1, min(int(limit), 200))
        query = (
            "SELECT o.*, COALESCE(l.label, 'unknown') AS client FROM operations o "
            "LEFT JOIN owner_labels l ON l.owner = o.owner"
        )
        params: tuple = ()
        if before is not None:
            query += " WHERE o.created < ?"
            params = (float(before),)
        query += " ORDER BY o.created DESC, o.id DESC LIMIT ?"
        rows = self.db.execute(query, (*params, limit)).fetchall()
        return [{**self._public(row), "client": row["client"]} for row in rows]

    @staticmethod
    def _summary(row) -> dict:
        """One journal row as the dashboard lists it: no result payload, just an error code when it
        failed and the app or link an action opened."""
        summary = {k: row[k] for k in ("id", "kind", "state", "created", "updated")}
        summary["client"] = row["client"]
        result = json.loads(row["result"]) if row["result"] else None
        if isinstance(result, dict):
            if isinstance(result.get("code"), str):
                summary["error_code"] = result["code"]
            process = result.get("process")
            if isinstance(process, dict):
                for key, name in (("bundle_id", "app"), ("url", "url")):
                    if isinstance(process.get(key), str):
                        summary[name] = process[key]
        return summary

    @staticmethod
    def cursor_after(row: dict) -> str:
        return f"{row['created']!r}:{row['id']}"

    @staticmethod
    def _parse_cursor(cursor: str) -> tuple[float, str]:
        created, _, operation_id = cursor.partition(":")
        try:
            return float(created), operation_id
        except ValueError:
            raise ThumblyError("INVALID_REQUEST", "cursor is not a journal cursor", 400) from None

    def page_operations(self, limit=20, cursor=None, budget=PAGE_BYTES, actions_only=False) -> dict:
        """Newest first, stable under identical timestamps: the cursor is ``(created, id)`` of the
        last row served, and ``has_more`` is set when rows remain or the byte budget stopped the
        page early. Rows are summaries; ``get`` serves the full result of one operation.
        ``actions_only`` leaves out lease bookkeeping; ``total`` counts every matching row."""
        limit = max(1, min(int(limit), PAGE_LIMIT))
        query = (
            "SELECT o.*, COALESCE(l.label, 'unknown') AS client FROM operations o "
            "LEFT JOIN owner_labels l ON l.owner = o.owner"
        )
        where: list[str] = []
        params: tuple = ()
        if actions_only:
            where.append(f"o.kind NOT IN ({','.join('?' * len(BOOKKEEPING))})")
            params = BOOKKEEPING
        total = self.db.execute(
            "SELECT COUNT(*) FROM operations o" + (" WHERE " + where[0] if where else ""), params
        ).fetchone()[0]
        if cursor:
            created, operation_id = self._parse_cursor(cursor)
            where.append("(o.created < ? OR (o.created = ? AND o.id < ?))")
            params = (*params, created, created, operation_id)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY o.created DESC, o.id DESC LIMIT ?"
        rows = self.db.execute(query, (*params, limit + 1)).fetchall()
        has_more = len(rows) > limit
        page: list[dict] = []
        size = 0
        for row in rows[:limit]:
            summary = self._summary(row)
            size += len(json.dumps(summary, separators=(",", ":")))
            if page and size > budget:
                has_more = True
                break
            page.append(summary)
        return {
            "operations": page,
            "next_cursor": self.cursor_after(page[-1]) if has_more and page else None,
            "has_more": has_more,
            "total": total,
        }

    def clear_operations(self) -> int:
        """Forget finished activity. Queued and running operations stay so their outcome can
        still be looked up."""
        cursor = self.db.execute(
            f"DELETE FROM operations WHERE state NOT IN ({','.join('?' * len(ACTIVE_STATES))})", ACTIVE_STATES
        )
        self._bump()
        self.db.commit()
        return cursor.rowcount

    def label_owner(self, owner, label):
        self.db.execute("INSERT OR REPLACE INTO owner_labels VALUES (?,?)", (owner, label))
        self._bump()
        self.db.commit()

    def update(self, operation_id, state, result=None):
        self.db.execute(
            "UPDATE operations SET state=?,result=?,updated=? WHERE id=?",
            (state, json.dumps(result) if result is not None else None, time.time(), operation_id),
        )
        self._bump()
        self.db.commit()
        return self._public(
            self.db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
        )

    def profile(self, device):
        row = self.db.execute("SELECT value FROM profiles WHERE device=?", (device,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_profile(self, device, value):
        self.db.execute("INSERT OR REPLACE INTO profiles VALUES (?,?)", (device, json.dumps(value)))
        self.db.commit()

    def close(self):
        self.db.close()
