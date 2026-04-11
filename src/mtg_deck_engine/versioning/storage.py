"""SQLite storage for deck version snapshots.

Each saved deck gets a unique deck_id. Each save creates a new version
with a snapshot of the full decklist, analysis scores, and metadata.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path

from mtg_deck_engine.data.database import DEFAULT_DB_PATH

_VERSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS decks (
    deck_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    format TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deck_versions (
    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    saved_at TEXT NOT NULL,
    notes TEXT DEFAULT '',
    decklist_json TEXT NOT NULL,
    scores_json TEXT DEFAULT '{}',
    metrics_json TEXT DEFAULT '{}',
    FOREIGN KEY (deck_id) REFERENCES decks(deck_id)
);

CREATE INDEX IF NOT EXISTS idx_versions_deck ON deck_versions(deck_id, version_number);
"""


@dataclass
class DeckSnapshot:
    """A saved snapshot of a deck at a point in time."""

    version_id: int = 0
    deck_id: str = ""
    version_number: int = 0
    saved_at: str = ""
    notes: str = ""
    decklist: dict[str, int] = field(default_factory=dict)  # card_name -> quantity
    zones: dict[str, list[str]] = field(default_factory=dict)  # zone -> [card_names]
    scores: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class DeckDiff:
    """Difference between two deck versions."""

    deck_name: str = ""
    version_a: int = 0
    version_b: int = 0
    added: dict[str, int] = field(default_factory=dict)     # card_name -> qty added
    removed: dict[str, int] = field(default_factory=dict)    # card_name -> qty removed
    changed_qty: dict[str, tuple[int, int]] = field(default_factory=dict)  # card -> (old, new)
    total_added: int = 0
    total_removed: int = 0
    score_deltas: dict[str, float] = field(default_factory=dict)  # score_name -> delta
    metric_deltas: dict[str, float] = field(default_factory=dict)


class VersionStore:
    """SQLite-backed deck version storage."""

    def __init__(self, db_path: Path | str | None = None):
        if db_path is None:
            db_path = DEFAULT_DB_PATH.parent / "versions.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_VERSION_SCHEMA)
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def save_version(
        self,
        deck_id: str,
        name: str,
        format: str | None,
        decklist: dict[str, int],
        zones: dict[str, list[str]],
        scores: dict[str, float] | None = None,
        metrics: dict[str, float] | None = None,
        notes: str = "",
    ) -> DeckSnapshot:
        """Save a new version of a deck."""
        conn = self.connect()
        now = datetime.now().isoformat()

        # Ensure deck exists
        existing = conn.execute(
            "SELECT deck_id FROM decks WHERE deck_id = ?", (deck_id,)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO decks (deck_id, name, format, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (deck_id, name, format, now, now),
            )
        else:
            conn.execute(
                "UPDATE decks SET name = ?, format = ?, updated_at = ? WHERE deck_id = ?",
                (name, format, now, deck_id),
            )

        # Get next version number
        row = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) FROM deck_versions WHERE deck_id = ?",
            (deck_id,),
        ).fetchone()
        version_number = row[0] + 1

        # Insert version
        conn.execute(
            """INSERT INTO deck_versions
               (deck_id, version_number, saved_at, notes, decklist_json, scores_json, metrics_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                deck_id,
                version_number,
                now,
                notes,
                json.dumps({"cards": decklist, "zones": zones}),
                json.dumps(scores or {}),
                json.dumps(metrics or {}),
            ),
        )
        conn.commit()

        version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        return DeckSnapshot(
            version_id=version_id,
            deck_id=deck_id,
            version_number=version_number,
            saved_at=now,
            notes=notes,
            decklist=decklist,
            zones=zones,
            scores=scores or {},
            metrics=metrics or {},
        )

    def get_version(self, deck_id: str, version_number: int) -> DeckSnapshot | None:
        """Load a specific version of a deck."""
        conn = self.connect()
        row = conn.execute(
            """SELECT version_id, version_number, saved_at, notes, decklist_json, scores_json, metrics_json
               FROM deck_versions WHERE deck_id = ? AND version_number = ?""",
            (deck_id, version_number),
        ).fetchone()
        if not row:
            return None
        return _row_to_snapshot(deck_id, row)

    def get_latest(self, deck_id: str) -> DeckSnapshot | None:
        """Load the most recent version of a deck."""
        conn = self.connect()
        row = conn.execute(
            """SELECT version_id, version_number, saved_at, notes, decklist_json, scores_json, metrics_json
               FROM deck_versions WHERE deck_id = ?
               ORDER BY version_number DESC LIMIT 1""",
            (deck_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_snapshot(deck_id, row)

    def get_all_versions(self, deck_id: str) -> list[DeckSnapshot]:
        """Load all versions of a deck."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT version_id, version_number, saved_at, notes, decklist_json, scores_json, metrics_json
               FROM deck_versions WHERE deck_id = ?
               ORDER BY version_number ASC""",
            (deck_id,),
        ).fetchall()
        return [_row_to_snapshot(deck_id, r) for r in rows]

    def list_decks(self) -> list[dict]:
        """List all saved decks with their latest version info."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT d.deck_id, d.name, d.format, d.created_at, d.updated_at,
                      (SELECT COUNT(*) FROM deck_versions v WHERE v.deck_id = d.deck_id) as versions
               FROM decks d ORDER BY d.updated_at DESC""",
        ).fetchall()
        return [
            {
                "deck_id": r[0],
                "name": r[1],
                "format": r[2],
                "created_at": r[3],
                "updated_at": r[4],
                "versions": r[5],
            }
            for r in rows
        ]

    def delete_deck(self, deck_id: str):
        """Delete a deck and all its versions."""
        conn = self.connect()
        conn.execute("DELETE FROM deck_versions WHERE deck_id = ?", (deck_id,))
        conn.execute("DELETE FROM decks WHERE deck_id = ?", (deck_id,))
        conn.commit()


def diff_versions(a: DeckSnapshot, b: DeckSnapshot) -> DeckDiff:
    """Compute the difference between two deck snapshots."""
    d = DeckDiff(
        deck_name=a.deck_id,
        version_a=a.version_number,
        version_b=b.version_number,
    )

    all_cards = set(a.decklist.keys()) | set(b.decklist.keys())
    for card in all_cards:
        qty_a = a.decklist.get(card, 0)
        qty_b = b.decklist.get(card, 0)

        if qty_a == 0 and qty_b > 0:
            d.added[card] = qty_b
            d.total_added += qty_b
        elif qty_b == 0 and qty_a > 0:
            d.removed[card] = qty_a
            d.total_removed += qty_a
        elif qty_a != qty_b:
            d.changed_qty[card] = (qty_a, qty_b)
            if qty_b > qty_a:
                d.total_added += qty_b - qty_a
            else:
                d.total_removed += qty_a - qty_b

    # Score deltas
    all_scores = set(a.scores.keys()) | set(b.scores.keys())
    for key in all_scores:
        sa = a.scores.get(key, 0.0)
        sb = b.scores.get(key, 0.0)
        d.score_deltas[key] = round(sb - sa, 2)

    # Metric deltas
    all_metrics = set(a.metrics.keys()) | set(b.metrics.keys())
    for key in all_metrics:
        ma = a.metrics.get(key, 0.0)
        mb = b.metrics.get(key, 0.0)
        d.metric_deltas[key] = round(mb - ma, 2)

    return d


def _row_to_snapshot(deck_id: str, row: tuple) -> DeckSnapshot:
    dl = json.loads(row[4])
    return DeckSnapshot(
        version_id=row[0],
        deck_id=deck_id,
        version_number=row[1],
        saved_at=row[2],
        notes=row[3],
        decklist=dl.get("cards", {}),
        zones=dl.get("zones", {}),
        scores=json.loads(row[5]),
        metrics=json.loads(row[6]),
    )
