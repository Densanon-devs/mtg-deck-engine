"""SQLite storage layer for card data."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from mtg_deck_engine.models import Card, CardFace, CardLayout, CardTag, Color, Legality

DEFAULT_DB_PATH = Path.home() / ".mtg-deck-engine" / "cards.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    scryfall_id TEXT PRIMARY KEY,
    oracle_id TEXT NOT NULL,
    name TEXT NOT NULL,
    layout TEXT NOT NULL,
    cmc REAL DEFAULT 0,
    mana_cost TEXT DEFAULT '',
    type_line TEXT DEFAULT '',
    oracle_text TEXT DEFAULT '',
    colors TEXT DEFAULT '[]',
    color_identity TEXT DEFAULT '[]',
    produced_mana TEXT DEFAULT '[]',
    keywords TEXT DEFAULT '[]',
    legalities TEXT DEFAULT '{}',
    faces TEXT DEFAULT '[]',
    power TEXT,
    toughness TEXT,
    loyalty TEXT,
    rarity TEXT DEFAULT '',
    set_code TEXT DEFAULT '',
    data_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name);
CREATE INDEX IF NOT EXISTS idx_cards_oracle_id ON cards(oracle_id);
CREATE INDEX IF NOT EXISTS idx_cards_name_lower ON cards(name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class CardDatabase:
    """SQLite-backed card storage with fast name lookups."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def get_metadata(self, key: str) -> str | None:
        conn = self.connect()
        row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_metadata(self, key: str, value: str):
        conn = self.connect()
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()

    def card_count(self) -> int:
        conn = self.connect()
        row = conn.execute("SELECT COUNT(*) FROM cards").fetchone()
        return row[0] if row else 0

    def upsert_cards(self, cards: list[Card], batch_size: int = 5000):
        conn = self.connect()
        for i in range(0, len(cards), batch_size):
            batch = cards[i : i + batch_size]
            conn.executemany(
                """INSERT OR REPLACE INTO cards
                   (scryfall_id, oracle_id, name, layout, cmc, mana_cost,
                    type_line, oracle_text, colors, color_identity, produced_mana,
                    keywords, legalities, faces, power, toughness, loyalty,
                    rarity, set_code, data_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [_card_to_row(c) for c in batch],
            )
            conn.commit()

    def lookup_by_name(self, name: str) -> Card | None:
        conn = self.connect()
        row = conn.execute(
            "SELECT data_json FROM cards WHERE name = ? COLLATE NOCASE LIMIT 1",
            (name,),
        ).fetchone()
        if row:
            return _card_from_json(row[0])
        # Try partial match for split/DFC names like "Fire // Ice"
        row = conn.execute(
            "SELECT data_json FROM cards WHERE name LIKE ? COLLATE NOCASE LIMIT 1",
            (f"{name} //%",),
        ).fetchone()
        if row:
            return _card_from_json(row[0])
        # Try as a face name
        row = conn.execute(
            "SELECT data_json FROM cards WHERE name LIKE ? COLLATE NOCASE LIMIT 1",
            (f"% // {name}",),
        ).fetchone()
        if row:
            return _card_from_json(row[0])
        return None

    def lookup_many(self, names: list[str]) -> dict[str, Card | None]:
        results: dict[str, Card | None] = {}
        for name in names:
            results[name] = self.lookup_by_name(name)
        return results

    def search(self, query: str, limit: int = 50) -> list[Card]:
        conn = self.connect()
        rows = conn.execute(
            "SELECT data_json FROM cards WHERE name LIKE ? COLLATE NOCASE LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [_card_from_json(r[0]) for r in rows]


def _card_to_row(card: Card) -> tuple:
    data = card.model_dump(mode="json")
    return (
        card.scryfall_id,
        card.oracle_id,
        card.name,
        card.layout.value,
        card.cmc,
        card.mana_cost,
        card.type_line,
        card.oracle_text,
        json.dumps([c.value for c in card.colors]),
        json.dumps([c.value for c in card.color_identity]),
        json.dumps(card.produced_mana),
        json.dumps(card.keywords),
        json.dumps({k: v.value for k, v in card.legalities.items()}),
        json.dumps([f.model_dump(mode="json") for f in card.faces]),
        card.power,
        card.toughness,
        card.loyalty,
        card.rarity,
        card.set_code,
        json.dumps(data),
    )


def _card_from_json(data_json: str) -> Card:
    data = json.loads(data_json)
    # Reconstruct enums
    data["layout"] = CardLayout(data["layout"])
    data["colors"] = [Color(c) for c in data.get("colors", [])]
    data["color_identity"] = [Color(c) for c in data.get("color_identity", [])]
    data["legalities"] = {k: Legality(v) for k, v in data.get("legalities", {}).items()}
    data["tags"] = [CardTag(t) for t in data.get("tags", [])]
    faces = []
    for f in data.get("faces", []):
        f["colors"] = [Color(c) for c in f.get("colors", [])]
        f["color_indicator"] = [Color(c) for c in f.get("color_indicator", [])]
        faces.append(CardFace(**f))
    data["faces"] = faces
    return Card(**data)
