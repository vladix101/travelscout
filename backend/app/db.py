"""SQLite persistence (stdlib sqlite3, executed off the event loop).

Tables are deliberately simple JSON documents so authentication (a `user_id` column) and
price alerts can be added later without reshaping the domain models.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    request_json TEXT NOT NULL,
    progress_json TEXT NOT NULL DEFAULT '[]',
    result_json TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS saved_searches (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    name TEXT NOT NULL,
    constraints_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS favorites (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    job_id TEXT,
    package_id TEXT NOT NULL,
    package_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_offers (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('flight', 'accommodation')),
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    stored_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    # -- low level ----------------------------------------------------------------------
    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
            self._conn.commit()
            return rows

    async def run(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._exec, sql, params)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- jobs -----------------------------------------------------------------------------
    async def create_job(self, mode: str, request: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex[:12]
        ts = now_iso()
        await self.run(
            "INSERT INTO jobs (id, created_at, updated_at, status, mode, request_json) VALUES (?,?,?,?,?,?)",
            (job_id, ts, ts, "queued", mode, json.dumps(request)),
        )
        return job_id

    async def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        progress: list[Any] | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        sets, params = ["updated_at = ?"], [now_iso()]
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if progress is not None:
            sets.append("progress_json = ?")
            params.append(json.dumps(progress, default=str))
        if result is not None:
            sets.append("result_json = ?")
            params.append(json.dumps(result, default=str))
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        params.append(job_id)
        await self.run(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", tuple(params))  # noqa: S608

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        rows = await self.run("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if not rows:
            return None
        r = rows[0]
        return {
            "id": r["id"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "status": r["status"],
            "mode": r["mode"],
            "request": json.loads(r["request_json"]),
            "progress": json.loads(r["progress_json"]),
            "result": json.loads(r["result_json"]) if r["result_json"] else None,
            "error": r["error"],
        }

    async def mark_stale_jobs(self) -> None:
        await self.run("UPDATE jobs SET status='failed', error='server restarted' WHERE status IN ('queued','running')")

    # -- generic documents ------------------------------------------------------------------
    async def add_saved_search(self, name: str, constraints: dict[str, Any]) -> str:
        sid = uuid.uuid4().hex[:12]
        await self.run(
            "INSERT INTO saved_searches VALUES (?,?,?,?)", (sid, now_iso(), name, json.dumps(constraints, default=str))
        )
        return sid

    async def list_saved_searches(self) -> list[dict[str, Any]]:
        rows = await self.run("SELECT * FROM saved_searches ORDER BY created_at DESC")
        return [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "name": r["name"],
                "constraints": json.loads(r["constraints_json"]),
            }
            for r in rows
        ]

    async def delete_saved_search(self, sid: str) -> None:
        await self.run("DELETE FROM saved_searches WHERE id = ?", (sid,))

    async def add_favorite(self, job_id: str | None, package: dict[str, Any]) -> str:
        fid = uuid.uuid4().hex[:12]
        await self.run(
            "INSERT INTO favorites VALUES (?,?,?,?,?)",
            (fid, now_iso(), job_id, package["id"], json.dumps(package, default=str)),
        )
        return fid

    async def list_favorites(self) -> list[dict[str, Any]]:
        rows = await self.run("SELECT * FROM favorites ORDER BY created_at DESC")
        return [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "job_id": r["job_id"],
                "package": json.loads(r["package_json"]),
            }
            for r in rows
        ]

    async def delete_favorite(self, fid: str) -> None:
        await self.run("DELETE FROM favorites WHERE id = ?", (fid,))

    async def add_manual_offer(self, kind: str, payload: dict[str, Any]) -> str:
        mid = payload.get("id") or f"manual-{uuid.uuid4().hex[:10]}"
        payload = {**payload, "id": mid}
        await self.run(
            "INSERT OR REPLACE INTO manual_offers VALUES (?,?,?,?)",
            (mid, now_iso(), kind, json.dumps(payload, default=str)),
        )
        return mid

    async def list_manual_offers(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            rows = await self.run("SELECT * FROM manual_offers WHERE kind = ? ORDER BY created_at", (kind,))
        else:
            rows = await self.run("SELECT * FROM manual_offers ORDER BY created_at")
        return [
            {"id": r["id"], "kind": r["kind"], "created_at": r["created_at"], "payload": json.loads(r["payload_json"])}
            for r in rows
        ]

    async def delete_manual_offer(self, mid: str) -> None:
        await self.run("DELETE FROM manual_offers WHERE id = ?", (mid,))

    # -- cache ---------------------------------------------------------------------------
    async def cache_get(self, key: str) -> tuple[Any, float] | None:
        rows = await self.run("SELECT value_json, stored_at, expires_at FROM cache WHERE key = ?", (key,))
        if not rows or rows[0]["expires_at"] < time.time():
            return None
        return json.loads(rows[0]["value_json"]), rows[0]["stored_at"]

    async def cache_set(self, key: str, value: Any, ttl_seconds: float) -> None:
        now = time.time()
        await self.run(
            "INSERT OR REPLACE INTO cache VALUES (?,?,?,?)",
            (key, json.dumps(value, default=str), now, now + ttl_seconds),
        )
