"""Profile-local durable review jobs. SQLite serializes admission across processes.

Snapshots are immutable and loaded only after claiming. Credentials are resolved at execution,
never serialized here. Leases fence late workers out of native memory dispatch.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)
LEASE_SECONDS = 90
TERMINAL = ("completed", "permanently-failed")


def integer(config, key, default, minimum=0, maximum=86400):
    try:
        return max(minimum, min(maximum, int(config.get(key, default))))
    except (ValueError, TypeError, OverflowError):
        return default


class ReviewJobs:
    def __init__(self, home, *, now=time.time, jitter=random.uniform):
        self.home = Path(home).resolve()
        from hermes_constants import hermes_home_key
        self.key = hermes_home_key(self.home)
        self.path = self.home / "review_queue.sqlite3"
        self.now, self.jitter = now, jitter
        self.home.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS review_jobs (
                    id TEXT PRIMARY KEY, dedup_key TEXT UNIQUE NOT NULL,
                    session_id TEXT NOT NULL, runtime_scope TEXT NOT NULL,
                    review_memory INTEGER NOT NULL, review_skills INTEGER NOT NULL,
                    route TEXT NOT NULL, snapshot TEXT NOT NULL,
                    status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL,
                    owner TEXT, lease_until REAL, error TEXT, usage TEXT,
                    max_retries INTEGER NOT NULL, backoff REAL NOT NULL, backoff_max REAL NOT NULL,
                    mode TEXT NOT NULL, concurrency INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS review_jobs_due ON review_jobs(status, next_at);
                CREATE TABLE IF NOT EXISTS review_foreground (
                    owner TEXT NOT NULL, scope TEXT NOT NULL, lease_until REAL NOT NULL,
                    PRIMARY KEY(owner, scope)
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def enqueue(self, session_id, scope, messages, review_memory, review_skills, route, config, mode):
        snapshot = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        selectors = {k: route[k] for k in ("provider", "model", "review_provider", "review_model") if route.get(k)}
        digest = hashlib.sha256(json.dumps(
            [session_id, scope, bool(review_memory), bool(review_skills), json.dumps(selectors, sort_keys=True)]
        ).encode())
        digest.update(snapshot.encode())
        key = digest.hexdigest()
        # Process identity permits live-runtime reuse without confusing a resumed session
        # with a job queued in this process. It is not a credential or part of deduplication.
        route = json.dumps({**selectors, "runtime_owner": route.get("runtime_owner")}, sort_keys=True)
        now, job_id = self.now(), uuid.uuid4().hex
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO review_jobs
                (id,dedup_key,session_id,runtime_scope,review_memory,review_skills,route,snapshot,
                 status,created_at,updated_at,next_at,max_retries,backoff,backoff_max,mode,concurrency)
                VALUES (?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?,?)""",
                (job_id, key, session_id, scope, review_memory, review_skills, route, snapshot,
                 now, now, now, integer(config, "max_retries", 3),
                 integer(config, "retry_backoff_s", 5, 1), integer(config, "retry_backoff_max_s", 300, 1),
                 mode, integer(config, "max_concurrency", 1, 1, 8)))
            job = dict(db.execute("SELECT id,status FROM review_jobs WHERE dedup_key=?", (key,)).fetchone())
        self.event("queued", job["id"])
        return job["id"]

    def event(self, event, job_id, **fields):
        logger.info("memory_review %s", json.dumps({
            "event": event, "job_id": job_id, "profile": str(self.home),
            "queue_length": self.count(), **fields,
        }, sort_keys=True))

    def count(self):
        with self.connect() as db:
            return db.execute("SELECT count(*) FROM review_jobs WHERE status NOT IN (?,?)", TERMINAL).fetchone()[0]

    def get(self, job_id, *, snapshot=False):
        columns = "*" if snapshot else "id,session_id,status,attempts,error,next_at,lease_until,usage"
        with self.connect() as db:
            row = db.execute(f"SELECT {columns} FROM review_jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def _failed(self, db, row, error, transient):
        retry = transient and row["attempts"] <= row["max_retries"]
        delay = min(row["backoff_max"], row["backoff"] * 2 ** min(20, max(0, row["attempts"] - 1)))
        delay = self.jitter(delay / 2, delay)
        db.execute("""UPDATE review_jobs SET status=?,updated_at=?,next_at=?,error=?,owner=NULL,
                      lease_until=NULL WHERE id=?""",
                   ("retry-pending" if retry else "permanently-failed", self.now(),
                    self.now() + delay, error, row["id"]))

    def claim(self, owner):
        """Claim metadata only; never read snapshots in a queue scan."""
        now = self.now()
        recovered = []
        with self.connect() as db:
            db.execute("DELETE FROM review_foreground WHERE lease_until<=?", (now,))
            for row in db.execute("""SELECT id,attempts,max_retries,backoff,backoff_max FROM review_jobs
                                  WHERE status='running' AND lease_until<=?""", (now,)).fetchall():
                self._failed(db, row, "lease_expired", True)
                recovered.append(row["id"])
            row = db.execute("""SELECT j.id FROM review_jobs j
                WHERE j.status IN ('pending','retry-pending') AND j.next_at<=?
                AND (SELECT count(*) FROM review_jobs r WHERE r.status='running'
                     AND r.runtime_scope=j.runtime_scope)<min(j.concurrency,
                     coalesce((SELECT min(r.concurrency) FROM review_jobs r WHERE r.status='running'
                               AND r.runtime_scope=j.runtime_scope),j.concurrency))
                AND NOT EXISTS (SELECT 1 FROM review_jobs r WHERE r.status='running'
                                AND r.session_id=j.session_id)
                AND (j.mode='concurrent' OR NOT EXISTS (SELECT 1 FROM review_foreground))
                ORDER BY j.created_at LIMIT 1""", (now,)).fetchone()
            if row:
                db.execute("""UPDATE review_jobs SET status='running',owner=?,lease_until=?,
                    updated_at=?,attempts=attempts+1 WHERE id=?""", (owner, now + LEASE_SECONDS, now, row[0]))
        for job_id in recovered:
            self.event("recovered", job_id)
        return row[0] if row else None

    def foreground(self, owner, scopes):
        with self.connect() as db:
            db.execute("DELETE FROM review_foreground WHERE owner=?", (owner,))
            db.executemany("INSERT INTO review_foreground VALUES (?,?,?)",
                           [(owner, scope, self.now() + LEASE_SECONDS) for scope in scopes])

    def heartbeat(self, owner, job_ids):
        with self.connect() as db:
            db.executemany("""UPDATE review_jobs SET lease_until=?,updated_at=?
                WHERE owner=? AND id=? AND status='running'""",
                [(self.now() + LEASE_SECONDS, self.now(), owner, job_id) for job_id in job_ids])

    def defer(self, job_id, owner):
        """An occupied managed local server is admission deferral, not a failed attempt."""
        with self.connect() as db:
            db.execute("""UPDATE review_jobs SET status='pending',attempts=attempts-1,
                next_at=?,updated_at=?,owner=NULL,lease_until=NULL WHERE id=? AND owner=?""",
                (self.now() + 5, self.now(), job_id, owner))

    def owned(self, job_id, owner):
        with self.connect() as db:
            return db.execute("""SELECT 1 FROM review_jobs WHERE id=? AND owner=?
                AND status='running' AND lease_until>?""", (job_id, owner, self.now())).fetchone() is not None

    def finish(self, job_id, owner, *, error=None, transient=False, usage=None, duration=0):
        with self.connect() as db:
            row = db.execute("""SELECT id,attempts,max_retries,backoff,backoff_max FROM review_jobs
                             WHERE id=? AND owner=? AND status='running'""",
                             (job_id, owner)).fetchone()
            if row is None:
                return
            if error:
                self._failed(db, row, error, transient)
                event = "retried" if transient and row["attempts"] <= row["max_retries"] else "permanently_failed"
            else:
                db.execute("""UPDATE review_jobs SET status='completed',updated_at=?,owner=NULL,
                    lease_until=NULL,error=NULL WHERE id=?""", (self.now(), job_id))
                event = "completed"
            db.execute("UPDATE review_jobs SET usage=? WHERE id=?", (json.dumps(usage or {}), job_id))
        self.event(event, job_id, duration_s=duration, attempt=row["attempts"],
                   retry_count=max(0, row["attempts"] - 1), error=error, usage=usage or {})

    def next_wake(self):
        with self.connect() as db:
            row = db.execute("""SELECT min(CASE WHEN status='running' THEN lease_until ELSE next_at END)
                FROM review_jobs WHERE status NOT IN (?,?)""", TERMINAL).fetchone()
            return row[0]
