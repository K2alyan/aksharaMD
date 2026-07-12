from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Job:
    path: str
    content_hash: str
    status: str
    readiness_score: int | None
    added_at: float
    processed_at: float | None
    error: str | None
    chunk_count: int | None


class IndexQueue:
    """SQLite-backed job queue with content-hash deduplication.

    Thread-safe: a single lock serialises all reads and writes so the watcher
    (enqueue) and worker (dequeue / mark_*) can operate from different threads.
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_tables()

    def _create_tables(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    path            TEXT PRIMARY KEY,
                    content_hash    TEXT NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'pending',
                    readiness_score INTEGER,
                    added_at        REAL NOT NULL,
                    processed_at    REAL,
                    error           TEXT,
                    chunk_count     INTEGER
                )
            """)
            self._conn.commit()

    @staticmethod
    def _hash_file(path: str) -> str:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def enqueue(self, path: str) -> bool:
        """Queue path for indexing.

        Returns True if the job was queued (new file or changed content).
        Returns False if the file is already indexed with identical content.
        Raises FileNotFoundError if path does not exist.
        """
        if not Path(path).exists():
            raise FileNotFoundError(path)

        content_hash = self._hash_file(path)

        with self._lock:
            row = self._conn.execute(
                "SELECT content_hash, status FROM jobs WHERE path = ?", (path,)
            ).fetchone()

            if row and row["content_hash"] == content_hash and row["status"] == "done":
                return False  # unchanged and already indexed

            if row:
                self._conn.execute(
                    "UPDATE jobs SET content_hash=?, status='pending', added_at=?,"
                    " processed_at=NULL, error=NULL, chunk_count=NULL, readiness_score=NULL"
                    " WHERE path=?",
                    (content_hash, time.time(), path),
                )
            else:
                self._conn.execute(
                    "INSERT INTO jobs (path, content_hash, status, added_at)"
                    " VALUES (?, ?, 'pending', ?)",
                    (path, content_hash, time.time()),
                )
            self._conn.commit()
            return True

    def dequeue(self) -> str | None:
        """Return the oldest pending job path and mark it as processing.

        Returns None if the queue is empty.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT path FROM jobs WHERE status='pending' ORDER BY added_at LIMIT 1"
            ).fetchone()
            if not row:
                return None
            path = row["path"]
            self._conn.execute(
                "UPDATE jobs SET status='processing' WHERE path=?", (path,)
            )
            self._conn.commit()
            return path

    def mark_done(self, path: str, chunk_count: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status='done', chunk_count=?, processed_at=? WHERE path=?",
                (chunk_count, time.time(), path),
            )
            self._conn.commit()

    def mark_low_quality(self, path: str, readiness_score: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status='low_quality', readiness_score=?, processed_at=? WHERE path=?",
                (readiness_score, time.time(), path),
            )
            self._conn.commit()

    def mark_error(self, path: str, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status='error', error=?, processed_at=? WHERE path=?",
                (str(error)[:2000], time.time(), path),
            )
            self._conn.commit()

    def remove(self, path: str) -> None:
        """Remove a job entry entirely (e.g. the file was deleted from the inbox)."""
        with self._lock:
            self._conn.execute("DELETE FROM jobs WHERE path=?", (path,))
            self._conn.commit()

    def stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    def list_all(self, status: str | None = None) -> list[Job]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM jobs WHERE status=? ORDER BY added_at DESC", (status,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM jobs ORDER BY added_at DESC"
                ).fetchall()
        return [Job(**dict(r)) for r in rows]

    def pending_count(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='pending'"
            ).fetchone()[0]

    def reset_all_jobs(self) -> None:
        """Delete all job records. Used by index clear."""
        with self._lock:
            self._conn.execute("DELETE FROM jobs")
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()
