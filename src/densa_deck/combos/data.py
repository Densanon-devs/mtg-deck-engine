"""Combo dataset persistence + fetch.

We pull the full /variants/ pagination from Commander Spellbook's backend
into a local SQLite database (~30k rows). The fetch is idempotent and
refreshable on demand — the desktop app's Settings panel exposes a
"Refresh combo data" button that re-walks the endpoint.

We deliberately do NOT bundle the snapshot in the PyInstaller package. The
combo set updates frequently as the community adds new variants; bundling
locks the binary to whatever the dataset looked like at build time and
forces a full re-release for each combo update. Fetch-on-first-use lets
v0.2.x ride combo updates without re-shipping the binary.

Table schema:
  combos(
    combo_id TEXT PRIMARY KEY,
    color_identity TEXT,
    bracket_tag TEXT,
    legal_commander INTEGER,
    popularity INTEGER,
    mana_value_needed REAL,
    description TEXT,
    notable_prerequisites TEXT,
    cards_json TEXT,        # ["Card A", "Card B", ...]
    templates_json TEXT,    # ["Permanent that can be cast using {C}", ...]
    produces_json TEXT      # ["Infinite colorless mana", ...]
  )
  combo_card_index(card_name COLLATE NOCASE, combo_id)  -- for fast deck-vs-combo lookup
  metadata(key, value)
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

import httpx

from densa_deck.combos.models import Combo

DEFAULT_COMBO_DB_PATH = Path.home() / ".densa-deck" / "combos.db"
SPELLBOOK_API_BASE = "https://backend.commanderspellbook.com"
PAGE_SIZE = 500
USER_AGENT_DEFAULT = "DensaDeck/0.4.1 (combo-fetch)"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS combos (
    combo_id TEXT PRIMARY KEY,
    color_identity TEXT DEFAULT '',
    bracket_tag TEXT DEFAULT '',
    legal_commander INTEGER DEFAULT 1,
    popularity INTEGER DEFAULT 0,
    mana_value_needed REAL DEFAULT 0,
    description TEXT DEFAULT '',
    notable_prerequisites TEXT DEFAULT '',
    cards_json TEXT NOT NULL,
    templates_json TEXT DEFAULT '[]',
    produces_json TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS combo_card_index (
    card_name TEXT NOT NULL COLLATE NOCASE,
    combo_id TEXT NOT NULL,
    PRIMARY KEY (card_name, combo_id)
);
CREATE INDEX IF NOT EXISTS idx_combo_card_index_name ON combo_card_index(card_name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS combo_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class ComboStore:
    """SQLite-backed combo cache with fast deck-vs-combo lookup.

    Thread-safe via thread-local connections (same pattern as CardDatabase
    in `data/database.py`) — the desktop app's pywebview dispatcher and
    background fetch thread can both touch the store concurrently.
    """

    def __init__(self, db_path: Path | str = DEFAULT_COMBO_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            with self._schema_lock:
                if not self._schema_ready:
                    conn.executescript(_SCHEMA)
                    self._schema_ready = True
            self._local.conn = conn
        return conn

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -------------------------------------------------------------- counts

    def combo_count(self) -> int:
        conn = self.connect()
        row = conn.execute("SELECT COUNT(*) FROM combos").fetchone()
        return row[0] if row else 0

    def get_metadata(self, key: str) -> str | None:
        conn = self.connect()
        row = conn.execute(
            "SELECT value FROM combo_metadata WHERE key = ?", (key,),
        ).fetchone()
        return row[0] if row else None

    def set_metadata(self, key: str, value: str):
        conn = self.connect()
        conn.execute(
            "INSERT OR REPLACE INTO combo_metadata (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()

    # -------------------------------------------------------------- writes

    def upsert_combos(self, combos: Iterable[Combo], batch_size: int = 500) -> int:
        """Insert or replace combos. Returns the count written.

        We rebuild the combo_card_index entries for each upserted combo so
        a card-rename in upstream gets reflected (delete-then-insert keeps
        the index honest without a full rebuild).
        """
        conn = self.connect()
        written = 0
        batch: list[Combo] = []
        for combo in combos:
            batch.append(combo)
            if len(batch) >= batch_size:
                self._flush_batch(conn, batch)
                written += len(batch)
                batch = []
        if batch:
            self._flush_batch(conn, batch)
            written += len(batch)
        return written

    def _flush_batch(self, conn: sqlite3.Connection, combos: list[Combo]) -> None:
        # Two-step transaction: delete index rows for the touched combos,
        # then upsert the combos table, then re-insert the index rows.
        # All inside a single conn.execute("BEGIN") so a crash mid-flush
        # doesn't leave the index out of sync with the combos table.
        ids = [c.combo_id for c in combos]
        placeholders = ",".join("?" * len(ids))
        conn.execute("BEGIN")
        try:
            if ids:
                conn.execute(
                    f"DELETE FROM combo_card_index WHERE combo_id IN ({placeholders})",
                    ids,
                )
            conn.executemany(
                """INSERT OR REPLACE INTO combos
                   (combo_id, color_identity, bracket_tag, legal_commander,
                    popularity, mana_value_needed, description,
                    notable_prerequisites, cards_json, templates_json, produces_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        c.combo_id, c.color_identity, c.bracket_tag,
                        1 if c.legal_in_commander else 0,
                        int(c.popularity or 0), float(c.mana_value_needed or 0.0),
                        c.description, c.notable_prerequisites,
                        json.dumps(c.cards), json.dumps(c.templates),
                        json.dumps(c.produces),
                    )
                    for c in combos
                ],
            )
            index_rows = [(name, c.combo_id) for c in combos for name in c.cards]
            conn.executemany(
                "INSERT OR IGNORE INTO combo_card_index (card_name, combo_id) VALUES (?, ?)",
                index_rows,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # -------------------------------------------------------------- reads

    def lookup_combos_for_card(self, card_name: str) -> list[str]:
        """Return combo IDs that include the given card."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT combo_id FROM combo_card_index WHERE card_name = ? COLLATE NOCASE",
            (card_name,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_combo(self, combo_id: str) -> Combo | None:
        conn = self.connect()
        row = conn.execute(
            """SELECT combo_id, color_identity, bracket_tag, legal_commander,
                      popularity, mana_value_needed, description,
                      notable_prerequisites, cards_json, templates_json,
                      produces_json
               FROM combos WHERE combo_id = ?""",
            (combo_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_combo(row)

    def iter_all_combos(self) -> Iterator[Combo]:
        conn = self.connect()
        for row in conn.execute(
            """SELECT combo_id, color_identity, bracket_tag, legal_commander,
                      popularity, mana_value_needed, description,
                      notable_prerequisites, cards_json, templates_json,
                      produces_json
               FROM combos"""
        ):
            yield _row_to_combo(row)


def _row_to_combo(row: tuple) -> Combo:
    (combo_id, identity, bracket, legal, pop, mvn, desc, notable,
     cards_json, templates_json, produces_json) = row
    return Combo(
        combo_id=str(combo_id),
        color_identity=str(identity or ""),
        bracket_tag=str(bracket or ""),
        legal_in_commander=bool(legal),
        popularity=int(pop or 0),
        mana_value_needed=float(mvn or 0.0),
        description=str(desc or ""),
        notable_prerequisites=str(notable or ""),
        cards=list(json.loads(cards_json or "[]")),
        templates=list(json.loads(templates_json or "[]")),
        produces=list(json.loads(produces_json or "[]")),
        spellbook_url=f"https://commanderspellbook.com/combo/{combo_id}/",
    )


# -------------------------------------------------------------- fetch


def _parse_variant(raw: dict) -> Combo | None:
    """Project an upstream variant JSON onto our Combo model.

    Skip variants with status != "OK" (they're spoiler / unverified /
    not-yet-implemented entries in upstream).
    """
    status = raw.get("status", "")
    if status != "OK":
        return None
    combo_id = str(raw.get("id") or "")
    if not combo_id:
        return None
    cards: list[str] = []
    for use in raw.get("uses") or []:
        card = (use or {}).get("card") or {}
        name = card.get("name")
        if name:
            cards.append(name)
    templates: list[str] = []
    for req in raw.get("requires") or []:
        tmpl = (req or {}).get("template") or {}
        n = tmpl.get("name")
        if n:
            templates.append(n)
    produces: list[str] = []
    for p in raw.get("produces") or []:
        feat = (p or {}).get("feature") or {}
        n = feat.get("name")
        if n:
            produces.append(n)
    legalities = raw.get("legalities") or {}
    return Combo(
        combo_id=combo_id,
        cards=cards,
        templates=templates,
        produces=produces,
        color_identity=str(raw.get("identity") or ""),
        bracket_tag=str(raw.get("bracketTag") or ""),
        description=str(raw.get("description") or ""),
        popularity=int(raw.get("popularity") or 0),
        legal_in_commander=bool(legalities.get("commander", True)),
        spellbook_url=f"https://commanderspellbook.com/combo/{combo_id}/",
        mana_value_needed=float(raw.get("manaValueNeeded") or 0.0),
        notable_prerequisites=str(raw.get("notablePrerequisites") or ""),
    )


async def _walk_variants(
    *,
    user_agent: str,
    progress_cb=None,
) -> list[Combo]:
    """Walk the paginated /variants/ endpoint, yielding parsed combos.

    Polite: 200ms inter-page sleep + custom User-Agent identifying
    Densa Deck per the agent's recommendation.
    """
    out: list[Combo] = []
    url: str | None = f"{SPELLBOOK_API_BASE}/variants/?limit={PAGE_SIZE}"
    pages = 0
    async with httpx.AsyncClient(
        timeout=60, headers={"User-Agent": user_agent, "Accept": "application/json"},
    ) as client:
        while url:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            for raw in data.get("results") or []:
                combo = _parse_variant(raw)
                if combo:
                    out.append(combo)
            pages += 1
            if progress_cb:
                progress_cb(pages, len(out))
            url = data.get("next")
            if url:
                # Polite spacing — Commander Spellbook doesn't publish a
                # rate limit but the research note recommended ~250ms
                # between pages.
                await asyncio.sleep(0.25)
    return out


def refresh_combo_snapshot(
    store: ComboStore | None = None,
    *,
    user_agent: str = USER_AGENT_DEFAULT,
    progress_cb=None,
) -> int:
    """Fetch a fresh combo snapshot and write it to the local store.

    Returns the number of combos written. Synchronous — wraps the async
    walker in a fresh event loop, mirroring how `_do_ingest` runs the
    Scryfall pipeline from a worker thread.

    `progress_cb(pages_done, combos_seen)` is called once per page so the
    desktop app's progress bar can advance during the walk (~60 pages at
    PAGE_SIZE=500 for a 30k dataset).
    """
    if store is None:
        store = ComboStore()
    loop = asyncio.new_event_loop()
    try:
        combos = loop.run_until_complete(
            _walk_variants(user_agent=user_agent, progress_cb=progress_cb),
        )
    finally:
        loop.close()
    written = store.upsert_combos(combos)
    store.set_metadata("last_refresh_at", datetime.now().isoformat(timespec="seconds"))
    store.set_metadata("source", SPELLBOOK_API_BASE)
    store.set_metadata("combo_count", str(written))
    return written
