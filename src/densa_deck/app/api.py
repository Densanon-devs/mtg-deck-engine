"""Python API exposed to the JS frontend via pywebview.

Every method returns a plain dict/list (JSON-serializable) so the frontend
can consume it without glue. Methods that touch the engine wrap existing
modules — no duplicate logic.

Tier gating: the `get_tier()` method reports the current tier. Individual
endpoints check `require_pro()` internally for features that should be
locked to Pro, but the GUI is the authoritative gate — free users never
see the button in the first place. The server-side checks are defense in
depth, not primary enforcement.

Deck lab: `save_deck_version` / `list_saved_decks` / `get_deck_latest` /
`diff_versions` wrap `versioning/storage.py`. The stored snapshot is a
structured `{cards: {name: qty}, zones: {zone: [names]}}`; the GUI
reconstructs an editable text form via `_snapshot_to_text` so the user
can come back, tweak, re-save as a new version.
"""

from __future__ import annotations

import json
import threading
import traceback
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from densa_deck.analysis.advanced import run_advanced_analysis
from densa_deck.analysis.castability import analyze_castability
from densa_deck.analysis.power_level import estimate_power_level
from densa_deck.analysis.staples import check_staples
from densa_deck.analysis.static import analyze_deck as run_static_analysis
from densa_deck.data.database import CardDatabase
from densa_deck.deck.parser import parse_decklist as parse_auto
from densa_deck.deck.resolver import resolve_deck
from densa_deck.deck.validator import validate_deck
from densa_deck.formats.profiles import detect_archetype
from densa_deck.models import Format, Zone
from densa_deck.tiers import Tier, get_user_tier
from densa_deck.versioning.storage import DeckSnapshot, VersionStore, diff_versions


def _safe(method):
    """Wrap an API method so uncaught exceptions become error dicts instead
    of crashing the pywebview bridge. The frontend checks `.ok` to branch.

    Failures still log the traceback to stderr so a user running with a
    console window open (dev mode) sees what broke; the end user sees a
    clean error message in the GUI.
    """
    def wrapper(self, *args, **kwargs):
        try:
            result = method(self, *args, **kwargs)
            if isinstance(result, dict) and "ok" in result:
                return result
            return {"ok": True, "data": result}
        except Exception as e:
            traceback.print_exc()
            return {"ok": False, "error": str(e), "error_type": type(e).__name__}
    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


class AppApi:
    """Methods exposed to the JS frontend.

    Construct with an optional `db_path` override (mainly for tests). The
    default matches the CLI — `~/.densa-deck/cards.db` for the card
    database and `~/.densa-deck/versions.db` for deck snapshots.
    """

    def __init__(self, db_path: Path | str | None = None, version_db_path: Path | str | None = None,
                 session_path: Path | str | None = None, state_path: Path | str | None = None):
        self._db_path = db_path
        self._version_db_path = version_db_path
        # Small-scale app-wide state (first-run flag, tour progress, etc.)
        # separate from the card/version DBs so it's cheap to write on every
        # launch without touching SQLite. Defaults to the same directory as
        # the card database so everything lives under ~/.densa-deck/.
        self._state_path = (
            Path(state_path) if state_path
            else Path.home() / ".densa-deck" / "app_state.json"
        )
        self._db: CardDatabase | None = None
        self._vstore: VersionStore | None = None
        # Progress state for long-running background operations (ingest + model
        # pull). The frontend polls `*_progress()` on an interval. `done=True`
        # means the operation finished (success or error) and polling can stop.
        self._progress: dict[str, dict] = {
            "ingest": {"pct": 0, "message": "idle", "done": True, "error": None, "running": False},
            "analyst_pull": {"pct": 0, "message": "idle", "done": True, "error": None, "running": False},
        }
        self._threads: dict[str, threading.Thread] = {}
        # Active coach REPL sessions, keyed by opaque uuid tokens so the
        # frontend can track them without exposing the CoachSession object
        # shape across the pywebview bridge. Lives for the app's lifetime
        # unless persisted via _session_path.
        self._coach_sessions: dict[str, dict] = {}
        self._coach_backend = None  # Lazily resolved on first coach call
        # Locks guard state mutated from the pywebview dispatcher thread and
        # our background ingest/pull threads. Without them, rapid double-
        # clicks can race check-then-set on `_progress["ingest"]["running"]`
        # and spawn duplicate download threads, and concurrent coach calls
        # can step on the sessions dict. Python's GIL makes single dict ops
        # atomic but compound sequences (get-modify-store) are not.
        self._progress_lock = threading.Lock()
        self._coach_lock = threading.Lock()
        self._backend_lock = threading.Lock()
        # Signalled by close() so background workers (ingest, analyst pull)
        # can notice an app shutdown and bail out early instead of leaving
        # cards.db or the analyst.gguf in a half-written state.
        self._shutdown_event = threading.Event()
        # Warnings accumulated during __init__ so the frontend can surface
        # "we couldn't read your coach_sessions.json last time — a fresh
        # start was created" etc. on the Settings tab. Read via
        # get_load_warnings() or as part of get_system_status().
        self._load_warnings: list[str] = []
        # Persistence path for coach sessions — load on init so a user who
        # restarts the app finds their prior conversations intact.
        self._session_path = (
            Path(session_path) if session_path
            else Path.home() / ".densa-deck" / "coach_sessions.json"
        )
        self._load_coach_sessions()
        # If a prior launch (or this one's license loader) quarantined a
        # corrupt license.key, surface that so the user knows why they might
        # need to re-activate — otherwise the Free tier just silently
        # reappears and they're left wondering what happened.
        self._check_license_quarantine()

    # ------------------------------------------------------------------ lifecycle

    def _get_db(self) -> CardDatabase:
        if self._db is None:
            self._db = CardDatabase(db_path=self._db_path) if self._db_path else CardDatabase()
        return self._db

    def _get_vstore(self) -> VersionStore:
        if self._vstore is None:
            if self._version_db_path:
                self._vstore = VersionStore(db_path=self._version_db_path)
            else:
                self._vstore = VersionStore()
        return self._vstore

    # ------------------------------------------------------------------ background-op plumbing

    def _update_progress(self, op: str, **fields) -> None:
        """Apply a partial update to self._progress[op] under the lock.

        Every background worker mutation MUST go through this helper (instead
        of touching self._progress[op] directly) so concurrent poll reads from
        the UI thread never see a torn dict or raise
        RuntimeError("dictionary changed size during iteration").
        """
        with self._progress_lock:
            self._progress[op].update(**fields)

    def _read_progress(self, op: str) -> dict:
        """Return a shallow copy of the progress dict under the lock.

        Pairs with _update_progress — readers copy under the same lock that
        writers hold when mutating, so the snapshot is always internally
        consistent.
        """
        with self._progress_lock:
            return dict(self._progress[op])

    def _check_license_quarantine(self) -> None:
        """Look for license.key.corrupt-*.bak files in the config dir and,
        if any are present, add a one-time warning so the Settings panel
        can tell the user their license needed re-activation and explain
        how to recover (paste the DD-... key from the Stripe receipt).

        The warning is self-clearing — once the user pastes a fresh key
        and activates, the warning stays in _load_warnings until
        dismiss_load_warnings() is called; the UI is expected to clear
        it when the user acknowledges.
        """
        from densa_deck.licensing import LICENSE_PATH
        try:
            stale = list(LICENSE_PATH.parent.glob("license.key.corrupt-*.bak"))
        except OSError:
            return
        if not stale:
            return
        self._load_warnings.append(
            "A previous license.key was unreadable and was moved aside — "
            "you may need to re-activate Pro by pasting your DD-XXXX-XXXX-XXXX "
            "key from the Stripe receipt page. Old copies are preserved as "
            "license.key.corrupt-*.bak in ~/.densa-deck/ for support."
        )

    def _quarantine_bad_file(self, path: Path, reason: str) -> None:
        """Move a corrupt persistence file aside so future launches succeed
        while still preserving the damaged original for support / forensics.

        Best-effort: if the rename itself fails (permission denied, the file
        just vanished, ...), we simply swallow the error — the loader calling
        us has already decided to start fresh, and we shouldn't cascade a
        failure here into blocking app launch.
        """
        from datetime import datetime as _dt
        stamp = _dt.now().strftime("%Y%m%d-%H%M%S")
        quarantine = path.with_suffix(path.suffix + f".corrupt-{stamp}.bak")
        try:
            path.rename(quarantine)
        except OSError:
            try:
                path.unlink()
            except OSError:
                pass
        # Append to a durable load-errors log so support can ask the user
        # to send it if something weird keeps happening across launches.
        try:
            log_path = path.parent / "load-errors.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"[{stamp}] quarantined {path.name}: {reason}\n")
        except OSError:
            pass

    def close(self):
        # Signal background workers (ingest + analyst pull) to bail at the
        # next checkpoint so we don't yank the DB / model file out from
        # under an in-flight write.
        self._shutdown_event.set()
        # Best-effort join of any live background threads. Bounded to a
        # generous-but-finite timeout so a stuck thread can't prevent the
        # app from closing — the user's Alt-F4 still wins, we just get one
        # honest chance to flush cleanly.
        for name, t in list(self._threads.items()):
            if t is not None and t.is_alive():
                try:
                    t.join(timeout=5.0)
                except Exception:
                    pass  # a hung thread isn't worth blocking close on
        # Persist coach sessions before tearing down the DB handles — the
        # versioning db path resolves to the same directory we save into, so
        # losing the vstore first would break `_session_path.parent` (the
        # dir is shared, not gated by the db object, but keep the order
        # conservative for future safety).
        self._save_coach_sessions()
        if self._db is not None:
            self._db.close()
            self._db = None
        if self._vstore is not None:
            self._vstore.close()
            self._vstore = None

    # ------------------------------------------------------------------ coach persistence

    def _save_coach_sessions(self):
        """Serialize coach sessions to JSON so a restart can restore them.

        Stores only primitives — token, deck_name, deck_id, created_at, and
        the list of turns. The CoachSession's `deck_sheet` and `allowed_cards`
        are rebuilt from the saved deck snapshot at restore time.
        """
        with self._coach_lock:
            # Snapshot the dict under the lock so we don't iterate a mutating
            # collection. The payload build happens outside the lock to keep
            # serialize time out of contention.
            snapshot = list(self._coach_sessions.items())
        if not snapshot:
            try:
                if self._session_path.exists():
                    self._session_path.unlink()
            except OSError:
                pass
            return
        try:
            payload = []
            for token, entry in snapshot:
                session = entry["session"]
                payload.append({
                    "token": token,
                    "deck_name": entry.get("deck_name"),
                    "deck_id": entry.get("deck_id"),
                    "created_at": entry.get("created_at"),
                    "deck_sheet": session.deck_sheet,
                    "allowed_cards": sorted(session.allowed_cards),
                    "history": [
                        {
                            "user_question": t.user_question,
                            "assistant_response": t.assistant_response,
                            "verified": t.verified,
                            "confidence": t.confidence,
                        }
                        for t in session.history
                    ],
                })
            _atomic_write_json(self._session_path, payload)
        except OSError as e:
            # Non-fatal — a failure here doesn't block app close, just logs.
            print(f"Warning: couldn't persist coach sessions: {e}")

    def _load_coach_sessions(self):
        """Restore sessions from the persistence file.

        On a missing file: stay silent. On a corrupt or malformed file:
        rename it to coach_sessions.json.corrupt-<timestamp>.bak so the
        user (or support) can forensics it later, record a warning the
        UI can surface, and start with a fresh sessions dict. A partial
        restore is worse than a fresh start but silently erasing history
        with no breadcrumb is worse still.
        """
        if not self._session_path.exists():
            return
        try:
            raw = self._session_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            self._quarantine_bad_file(self._session_path, reason=str(exc))
            self._load_warnings.append(
                "Coach session history was unreadable last launch and a "
                "fresh start was created. The old file is preserved as "
                f"{self._session_path.name}.corrupt-*.bak if you need it."
            )
            return
        if not isinstance(payload, list):
            self._quarantine_bad_file(self._session_path, reason="payload is not a list")
            self._load_warnings.append(
                "Coach session file had an unexpected shape; fresh start created. "
                f"Old copy preserved as {self._session_path.name}.corrupt-*.bak."
            )
            return

        from densa_deck.analyst.coach import CoachSession, CoachTurn
        for item in payload:
            token = item.get("token")
            if not token:
                continue
            history = [
                CoachTurn(
                    user_question=t.get("user_question", ""),
                    assistant_response=t.get("assistant_response", ""),
                    verified=bool(t.get("verified", False)),
                    confidence=float(t.get("confidence", 0.0)),
                )
                for t in item.get("history", [])
            ]
            session = CoachSession(
                deck_sheet=item.get("deck_sheet", ""),
                allowed_cards=set(item.get("allowed_cards", [])),
                history=history,
            )
            self._coach_sessions[token] = {
                "session": session,
                "deck_name": item.get("deck_name"),
                "deck_id": item.get("deck_id"),
                "created_at": item.get("created_at"),
                # Per-session lock so coach_ask's append and coach_reset's
                # clear / coach_close's pop can't race on session.history.
                # See the long comment in coach_ask for the invariant.
                "turn_lock": threading.Lock(),
            }

    # ------------------------------------------------------------------ status

    @_safe
    def get_tier(self) -> dict:
        """Return the current tier + whether Pro features should be shown."""
        tier = get_user_tier()
        return {
            "tier": tier.value,
            "is_pro": tier == Tier.PRO,
        }

    @_safe
    def get_system_status(self) -> dict:
        """Summarize whether setup is complete — for the first-run banner.

        Reports: card DB present + count, analyst model path + present,
        version DB path. Frontend uses this to decide what setup prompts
        to show on first launch.
        """
        db = self._get_db()
        card_count = db.card_count()
        # Analyst model availability is two separate questions:
        #   1. Is the GGUF file downloaded to disk?
        #   2. Is llama-cpp-python importable so we can actually use it?
        # The UI cares about both — "file present but library missing"
        # is a real state that earlier versions rendered as a misleading
        # "Not installed" message after the user had just downloaded it.
        analyst_path = ""
        analyst_file_present = False
        analyst_library_ok = False
        analyst_reason = ""
        try:
            from densa_deck.analyst.backends.llama_cpp import DEFAULT_MODEL_PATH
            analyst_path = str(DEFAULT_MODEL_PATH)
            analyst_file_present = DEFAULT_MODEL_PATH.exists()
        except Exception as e:
            analyst_reason = f"Could not resolve analyst model path: {e}"
        if analyst_file_present:
            try:
                import llama_cpp  # noqa: F401
                analyst_library_ok = True
            except Exception as e:
                analyst_reason = (
                    f"Model file is present but llama-cpp-python failed to load "
                    f"({e}). The analyst won't run until this is resolved — "
                    f"reinstalling the app usually fixes it."
                )
        elif not analyst_reason:
            analyst_reason = "Not installed. Click Download analyst model below."
        return {
            "card_database": {
                "count": card_count,
                "ready": card_count > 0,
            },
            "analyst_model": {
                "path": analyst_path,
                "ready": analyst_file_present and analyst_library_ok,
                "file_present": analyst_file_present,
                "library_ok": analyst_library_ok,
                "reason": analyst_reason,
            },
            "version_db_path": str(self._get_vstore().db_path),
            # Snapshot of warnings the frontend should surface as a
            # dismissible banner in Settings — empty list is the happy path.
            "load_warnings": list(self._load_warnings),
        }

    @_safe
    def dismiss_load_warnings(self) -> dict:
        """Clear the pending load_warnings list. Called after the UI has
        shown the banner and the user has acknowledged it."""
        self._load_warnings.clear()
        return {"cleared": True}

    # ------------------------------------------------------------------ analysis

    @_safe
    def analyze_deck(self, decklist_text: str, format_: str | None = None, name: str = "Unnamed Deck") -> dict:
        """Parse + resolve + analyze a decklist. Returns a dict the frontend renders.

        `format_` is an optional format string ("commander" / "modern" / ...).
        Defaults to Commander when omitted. All Pro-gated deeper analyses
        (power breakdown, castability, advanced, staples) run here because
        the GUI is already the Pro-gating layer — the CLI-level checks
        are defense in depth but we don't re-check per-field.
        """
        if not decklist_text.strip():
            return {"ok": False, "error": "Decklist is empty."}
        db = self._get_db()
        if db.card_count() == 0:
            return {
                "ok": False,
                "error": "Card database not ingested. Open Settings and run Setup.",
                "error_type": "IngestRequired",
            }

        entries = parse_auto(decklist_text)
        if not entries:
            return {"ok": False, "error": "No cards parsed from the decklist."}
        fmt = Format(format_) if format_ else Format.COMMANDER
        deck = resolve_deck(entries, db, name=name, format=fmt)

        static_result = run_static_analysis(deck)
        issues = validate_deck(deck)
        static_result.issues.extend(issues)

        archetype = detect_archetype(deck)
        power = estimate_power_level(deck)
        advanced = run_advanced_analysis(deck, static_result.color_sources)
        castability = analyze_castability(deck, static_result.color_sources)
        staples = check_staples(deck)

        unresolved = [e.card_name for e in deck.entries if e.card is None]

        return _analysis_to_dict(
            deck=deck, static_result=static_result,
            archetype=archetype, power=power,
            advanced=advanced, castability=castability, staples=staples,
            unresolved=unresolved,
        )

    # ------------------------------------------------------------------ deck lab

    @_safe
    def list_saved_decks(self) -> list[dict]:
        """Return all saved decks with version counts + timestamps for the sidebar."""
        return self._get_vstore().list_decks()

    @_safe
    def get_deck_latest(self, deck_id: str) -> dict:
        """Return the most recent version of a saved deck + reconstructed editable text."""
        snap = self._get_vstore().get_latest(deck_id)
        if snap is None:
            return {"ok": False, "error": f"No saved versions for deck '{deck_id}'."}
        return _snapshot_to_dict(snap)

    @_safe
    def get_deck_history(self, deck_id: str) -> list[dict]:
        """Return all version snapshots (newest first) for the history view."""
        versions = self._get_vstore().get_all_versions(deck_id)
        # Sort newest-first so the history panel leads with the current version
        versions.sort(key=lambda s: s.version_number, reverse=True)
        return [_snapshot_summary(v) for v in versions]

    @_safe
    def save_deck_version(
        self,
        deck_id: str,
        name: str,
        decklist_text: str,
        format_: str | None = None,
        notes: str = "",
    ) -> dict:
        """Parse + resolve + save a new version of a deck.

        Reuses the same ingest as the CLI `save` subcommand: run static
        analysis, stash scores with the snapshot, persist via VersionStore.
        Returns the saved snapshot dict so the frontend can render the
        new version immediately.
        """
        if not deck_id.strip():
            return {"ok": False, "error": "Deck ID is required (letters + digits only is safest)."}
        db = self._get_db()
        if db.card_count() == 0:
            return {
                "ok": False,
                "error": "Card database not ingested. Open Settings and run Setup.",
                "error_type": "IngestRequired",
            }
        if not decklist_text.strip():
            return {"ok": False, "error": "Decklist is empty."}

        entries = parse_auto(decklist_text)
        if not entries:
            return {"ok": False, "error": "No cards parsed from the decklist."}
        fmt = Format(format_) if format_ else Format.COMMANDER
        deck = resolve_deck(entries, db, name=name, format=fmt)

        result = run_static_analysis(deck)

        decklist: dict[str, int] = {}
        zones: dict[str, list[str]] = {}
        for entry in deck.entries:
            decklist[entry.card_name] = decklist.get(entry.card_name, 0) + entry.quantity
            zone_name = entry.zone.value if hasattr(entry.zone, "value") else str(entry.zone)
            zones.setdefault(zone_name, []).append(entry.card_name)

        metrics = {
            "land_count": float(result.land_count),
            "ramp_count": float(result.ramp_count),
            "draw_count": float(result.draw_engine_count),
            "interaction_count": float(result.interaction_count),
            "threat_count": float(result.threat_count),
            "average_cmc": float(result.average_cmc),
            "total_cards": float(result.total_cards),
        }
        snap = self._get_vstore().save_version(
            deck_id=deck_id, name=name,
            format=deck.format.value if deck.format else None,
            decklist=decklist, zones=zones,
            scores=dict(result.scores or {}), metrics=metrics, notes=notes,
        )
        return _snapshot_to_dict(snap)

    @_safe
    def delete_deck(self, deck_id: str) -> dict:
        """Delete a saved deck + all its versions. Destructive — the frontend
        should confirm before calling."""
        self._get_vstore().delete_deck(deck_id)
        return {"deleted": deck_id}

    @_safe
    def diff_deck_versions(self, deck_id: str, version_a: int, version_b: int) -> dict:
        """Diff two versions of a deck. Returns added/removed/score-delta."""
        store = self._get_vstore()
        a = store.get_version(deck_id, version_a)
        b = store.get_version(deck_id, version_b)
        if a is None or b is None:
            return {"ok": False, "error": "One or both versions not found."}
        d = diff_versions(a, b)
        return {
            "version_a": a.version_number,
            "version_b": b.version_number,
            "added": dict(d.added),
            "removed": dict(d.removed),
            "changed_qty": {k: list(v) for k, v in d.changed_qty.items()},
            "total_added": d.total_added,
            "total_removed": d.total_removed,
            "score_deltas": dict(d.score_deltas),
        }

    # ------------------------------------------------------------------ license

    # ------------------------------------------------------------------ fuzzy resolve

    @_safe
    def resolve_suggestions(self, names: list[str], limit: int = 3) -> dict:
        """For each unresolved card name, return up to `limit` close matches
        from the card DB. Powers the "did you mean?" chips on the unresolved
        cards panel after Analyze.

        Uses a two-pass strategy so common typos resolve without a full
        string-distance scan over 30k cards:
          1. SQLite LIKE with the first word of the bad name — cheap,
             catches 80% of typos (e.g. "Cultvate" -> Cultivate via "Cult%").
          2. If the LIKE returns nothing, fall back to difflib.get_close_matches
             over a prefix-filtered slice of names to keep latency acceptable.

        Returns `{name: [suggestion, suggestion, ...], ...}`.
        """
        import difflib
        db = self._get_db()
        if db.card_count() == 0:
            return {}

        conn = db.connect()
        out: dict[str, list[str]] = {}
        for bad in names:
            if not bad or len(bad.strip()) < 2:
                out[bad] = []
                continue
            first_word = bad.strip().split()[0][:6]
            like_pattern = f"{first_word}%"
            rows = conn.execute(
                "SELECT name FROM cards WHERE name LIKE ? COLLATE NOCASE LIMIT 50",
                (like_pattern,),
            ).fetchall()
            candidates = [r[0] for r in rows]
            if not candidates:
                # Wider prefix scope — first 3 letters
                prefix3 = bad.strip()[:3]
                rows = conn.execute(
                    "SELECT name FROM cards WHERE name LIKE ? COLLATE NOCASE LIMIT 200",
                    (f"{prefix3}%",),
                ).fetchall()
                candidates = [r[0] for r in rows]
            # Rank by difflib ratio — cheap when the candidate pool is small
            matches = difflib.get_close_matches(bad, candidates, n=limit, cutoff=0.5)
            out[bad] = matches
        return out

    # ------------------------------------------------------------------ first-run / app state

    def _load_state(self) -> dict:
        """Read the app_state JSON. Missing/malformed file returns {} — we
        never crash on a bad state file because it would block app launch."""
        if not self._state_path.exists():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self, data: dict):
        """Overwrite the app_state JSON atomically. Failure is logged but not raised."""
        try:
            _atomic_write_json(self._state_path, data)
        except OSError as e:
            print(f"Warning: couldn't save app state: {e}")

    @_safe
    def get_first_run_state(self) -> dict:
        """Return whether the first-run tour has completed.

        `completed` is True once the user finishes OR dismisses the tour —
        we don't distinguish because either way they don't want it again
        unless they explicitly ask via reset_first_run.
        """
        state = self._load_state()
        return {
            "completed": bool(state.get("first_run_completed", False)),
            "completed_at": state.get("first_run_completed_at"),
        }

    @_safe
    def mark_first_run_complete(self) -> dict:
        """Persist the first-run-done flag. Called at the end of the tour
        (either Complete or Skip) so subsequent launches skip the overlay."""
        state = self._load_state()
        state["first_run_completed"] = True
        state["first_run_completed_at"] = _now_iso()
        self._save_state(state)
        return {"completed": True}

    @_safe
    def reset_first_run(self) -> dict:
        """Clear the first-run flag so the tour shows again on next launch.
        Wired to a 'Show tour again' button in Settings."""
        state = self._load_state()
        state.pop("first_run_completed", None)
        state.pop("first_run_completed_at", None)
        self._save_state(state)
        return {"reset": True}

    # ------------------------------------------------------------------ misc helpers

    @_safe
    def open_external(self, url: str) -> dict:
        """Open a URL in the user's default browser. Called by the frontend
        to route external links out of the webview (so clicking a link
        doesn't navigate the app's own document away from the SPA).
        """
        import webbrowser
        # Basic scheme whitelist — pywebview's webview hands us whatever the
        # page passes in, and a malicious page shouldn't be able to launch
        # arbitrary protocols on the user's machine. https/http/mailto only.
        safe = any(url.startswith(p) for p in ("https://", "http://", "mailto:"))
        if not safe:
            return {"ok": False, "error": "Only https/http/mailto links are allowed."}
        try:
            webbrowser.open(url, new=2)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"opened": True}

    # ------------------------------------------------------------------ URL import

    @_safe
    def import_deck_from_url(self, url: str) -> dict:
        """Import a decklist from a Moxfield or Archidekt URL. Returns the
        decklist in our pasteable text format so the frontend can drop it
        into the textarea."""
        import asyncio
        from densa_deck.deck.url_import import detect_url, fetch_from_url
        if not url or not url.strip():
            return {"ok": False, "error": "URL is empty."}
        detected = detect_url(url)
        if detected is None:
            return {
                "ok": False,
                "error": "Unsupported URL. Currently supports Moxfield and Archidekt public deck URLs.",
            }
        loop = asyncio.new_event_loop()
        try:
            entries = loop.run_until_complete(fetch_from_url(url))
        except Exception as e:
            return {"ok": False, "error": f"Fetch failed: {e}"}
        finally:
            loop.close()
        if not entries:
            return {"ok": False, "error": "URL loaded but no cards found."}

        # Group entries by zone, emit pasteable format
        from collections import defaultdict
        by_zone: dict[str, list] = defaultdict(list)
        for e in entries:
            zone_name = e.zone.value if hasattr(e.zone, "value") else str(e.zone)
            by_zone[zone_name].append(e)
        zone_order = ["commander", "companion", "mainboard", "sideboard", "maybeboard"]
        lines: list[str] = []
        for zone in zone_order + [z for z in by_zone if z not in zone_order]:
            if zone not in by_zone:
                continue
            lines.append(f"{zone.capitalize()}:")
            for e in by_zone[zone]:
                lines.append(f"{e.quantity} {e.card_name}")
            lines.append("")
        text = "\n".join(lines).strip() + "\n"
        return {
            "service": detected[0],
            "deck_id": detected[1],
            "card_count": sum(e.quantity for e in entries),
            "decklist_text": text,
        }

    # ------------------------------------------------------------------ auto-update

    @_safe
    def get_current_version(self) -> dict:
        """Return the version baked into this build."""
        from densa_deck import __version__ as v
        return {"version": v}

    @_safe
    def check_for_updates(self, url: str = "https://toolkit.densanon.com/densa-deck-version.json") -> dict:
        """Check the version JSON on the toolkit site. Mirrors the D-Brief pattern.

        Returns `{current, latest, update_available, changelog, download_url}`.
        Network errors return `update_available=False` with an `error` field
        so the frontend can show "Couldn't check for updates" without failing
        the whole launch.
        """
        import urllib.request
        from densa_deck import __version__ as current
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {
                "current": current, "latest": None,
                "update_available": False, "error": str(e),
            }
        latest = str(data.get("version", ""))
        update_available = _is_newer(latest, current)
        return {
            "current": current,
            "latest": latest,
            "update_available": update_available,
            "release_date": data.get("releaseDate") or data.get("release_date"),
            "changelog": data.get("changelog", []),
            "download_url": data.get("downloadUrl") or data.get("download_url"),
        }

    # ------------------------------------------------------------------ coach (Pro chat)

    @_safe
    def coach_start(self, deck_id: str | None = None, decklist_text: str | None = None,
                    name: str = "Coach Deck", format_: str | None = None) -> dict:
        """Open a new coach session bound to a specific deck.

        Two modes:
          - `deck_id` provided: load the latest saved snapshot and use its
            reconstructed text as the deck state.
          - `decklist_text` provided: use a fresh paste, no version history.

        The returned token is the handle the frontend uses for subsequent
        ask/reset/close calls. Sessions live for the app's lifetime in memory;
        closing the app loses them (acceptable for phase 1 — persistence is a
        future enhancement).
        """
        from densa_deck.analysis.power_level import estimate_power_level
        from densa_deck.analysis.static import analyze_deck as _analyze
        from densa_deck.analyst.coach import CoachSession, build_deck_sheet
        from densa_deck.formats.profiles import detect_archetype

        db = self._get_db()
        if db.card_count() == 0:
            return {
                "ok": False,
                "error": "Card database not ingested. Open Settings and run Setup.",
                "error_type": "IngestRequired",
            }

        if deck_id:
            snap = self._get_vstore().get_latest(deck_id)
            if snap is None:
                return {"ok": False, "error": f"No saved versions for deck '{deck_id}'."}
            decklist_text = _snapshot_to_text(snap)
            # Prefer the human-readable name from the decks table (set on save)
            # over the URL-safe deck_id so the coach header reads naturally.
            try:
                entry = next((d for d in self._get_vstore().list_decks()
                              if d["deck_id"] == deck_id), None)
                name = entry["name"] if entry and entry.get("name") else deck_id
            except Exception:
                name = deck_id
        if not decklist_text or not decklist_text.strip():
            return {"ok": False, "error": "Deck text required (pass deck_id or decklist_text)."}

        entries = parse_auto(decklist_text)
        if not entries:
            return {"ok": False, "error": "No cards parsed from the decklist."}
        fmt = Format(format_) if format_ else Format.COMMANDER
        deck = resolve_deck(entries, db, name=name, format=fmt)

        result = _analyze(deck)
        power = estimate_power_level(deck)
        archetype = detect_archetype(deck)

        color_identity = sorted({
            c.value for e in deck.entries if e.card for c in e.card.color_identity
        })
        deck_cards = [e.card.name for e in deck.entries if e.card]

        sheet = build_deck_sheet(
            deck_name=deck.name,
            archetype=archetype.value if hasattr(archetype, "value") else str(archetype),
            color_identity=color_identity,
            power_overall=power.overall, power_tier=power.tier,
            land_count=result.land_count, ramp_count=result.ramp_count,
            draw_count=result.draw_engine_count,
            interaction_count=result.interaction_count,
            avg_mana_value=result.average_cmc,
            deck_cards=deck_cards,
            reasons_up=list(power.reasons_up),
            reasons_down=list(power.reasons_down),
        )
        session = CoachSession(deck_sheet=sheet, allowed_cards=set(deck_cards))

        token = uuid.uuid4().hex[:12]
        with self._coach_lock:
            self._coach_sessions[token] = {
                "session": session, "deck_name": deck.name, "deck_id": deck_id,
                "created_at": _now_iso(),
                # Per-session turn_lock prevents coach_ask's history.append
                # from racing with coach_reset's history.clear() or
                # coach_close's pop. See comment in coach_ask.
                "turn_lock": threading.Lock(),
            }
        return {
            "token": token,
            "deck_name": deck.name,
            "deck_id": deck_id,
            "card_count": len(deck_cards),
            "power": f"{power.overall:.1f}/10 ({power.tier})",
            "archetype": archetype.value if hasattr(archetype, "value") else str(archetype),
        }

    @_safe
    def coach_ask(self, token: str, question: str) -> dict:
        """Send a question to an active coach session. Returns the turn
        (user_question, assistant_response, verified, confidence).

        Two-layer locking avoids both "block all coach endpoints for the
        seconds of LLM generation" (bad UX) and "history.append races
        history.clear() from coach_reset" (data corruption):

          1. _coach_lock — briefly held just to look up the entry.
          2. entry["turn_lock"] — held across coach_step(), so this one
             session serializes its turns while other sessions stay free.
             coach_reset and coach_close also take this lock, so they
             wait for an in-flight turn to finish instead of clobbering
             session.history mid-append.
        """
        from densa_deck.analyst.coach import coach_step
        with self._coach_lock:
            entry = self._coach_sessions.get(token)
        if entry is None:
            return {"ok": False, "error": f"Unknown or closed coach session: {token}"}
        if not question.strip():
            return {"ok": False, "error": "Question is empty."}

        backend = self._get_coach_backend()
        with entry["turn_lock"]:
            # Re-check under the turn_lock so a concurrent coach_close that
            # already popped the session doesn't see us appending to a
            # detached session.history after the dict entry is gone.
            with self._coach_lock:
                if self._coach_sessions.get(token) is not entry:
                    return {"ok": False, "error": f"Session was closed during generation: {token}"}
            turn = coach_step(entry["session"], backend, question, max_retries=1)
        return {
            "user_question": turn.user_question,
            "assistant_response": turn.assistant_response,
            "verified": turn.verified,
            "confidence": turn.confidence,
        }

    @_safe
    def coach_reset(self, token: str) -> dict:
        """Clear conversation history for a session but keep the deck sheet.

        Holds the session's turn_lock across the clear so an in-flight
        coach_ask can't append a turn mid-clear. _coach_lock is only held
        briefly to safely fetch the entry.
        """
        with self._coach_lock:
            entry = self._coach_sessions.get(token)
        if entry is None:
            return {"ok": False, "error": "Unknown session."}
        with entry["turn_lock"]:
            entry["session"].history.clear()
        return {"reset": True}

    @_safe
    def coach_close(self, token: str) -> dict:
        """Terminate a session and free its memory.

        Acquires the session's turn_lock before popping so any in-flight
        coach_ask for this token finishes cleanly (or returns the
        "closed during generation" error after re-checking). Without this,
        close + in-flight ask could race on session.history.
        """
        with self._coach_lock:
            entry = self._coach_sessions.get(token)
        if entry is None:
            return {"closed": False}
        with entry["turn_lock"]:
            with self._coach_lock:
                removed = self._coach_sessions.pop(token, None)
        return {"closed": removed is not None}

    @_safe
    def coach_get_history(self, token: str) -> list[dict]:
        """Return the full turn history for a session. Used by the frontend
        when resuming a session (e.g. after app restart) so the chat panel
        can re-render prior user/assistant messages without re-running them."""
        with self._coach_lock:
            entry = self._coach_sessions.get(token)
        if entry is None:
            return {"ok": False, "error": "Unknown session."}
        return [
            {
                "user_question": t.user_question,
                "assistant_response": t.assistant_response,
                "verified": t.verified,
                "confidence": t.confidence,
            }
            for t in entry["session"].history
        ]

    @_safe
    def coach_list_sessions(self) -> list[dict]:
        """List active session metadata — for the Coach tab's left column."""
        with self._coach_lock:
            # Snapshot the session data under the lock so the list isn't
            # mutated mid-iteration by a concurrent close/start.
            items = [
                {
                    "token": token,
                    "deck_name": entry["deck_name"],
                    "deck_id": entry.get("deck_id"),
                    "created_at": entry["created_at"],
                    "turn_count": len(entry["session"].history),
                }
                for token, entry in self._coach_sessions.items()
            ]
        return items

    def _get_coach_backend(self):
        """Pick the LLM backend once, cache it. Falls back to the mock
        analyst backend when the GGUF model isn't available so the UI stays
        functional for users who haven't downloaded a model yet.

        Double-checked locking: the first check avoids the lock overhead in
        the hot path (after first initialization), the second check inside
        the lock prevents a race where two threads both pass the first check
        and both initialize the backend.
        """
        if self._coach_backend is not None:
            return self._coach_backend
        with self._backend_lock:
            if self._coach_backend is not None:
                return self._coach_backend
            import os
            backend_name = os.environ.get("MTG_ANALYST_BACKEND", "mock").lower().strip()
            if backend_name in ("llama", "llama_cpp", "llamacpp"):
                try:
                    from densa_deck.analyst.backends.llama_cpp import LlamaCppBackend
                    backend = LlamaCppBackend()
                    if backend.is_available():
                        self._coach_backend = backend
                        return backend
                except Exception:
                    pass
            from densa_deck.analyst import MockBackend
            self._coach_backend = MockBackend(
                default="(Coach placeholder — install an analyst model from Settings for real responses.)",
            )
            return self._coach_backend

    @_safe
    def activate_license(self, key: str) -> dict:
        """Activate a license key. Wraps licensing.save_license.

        Returns the granular `error` string from verify_license_key on
        failure so the UI can display "wrong prefix", "wrong length",
        "checksum mismatch", etc. instead of a generic "invalid" message.
        """
        from densa_deck.licensing import save_license
        result = save_license(key)
        return {
            "valid": result.valid,
            "is_master": result.is_master,
            "key": result.key if result.valid else None,
            "activated_at": result.activated_at,
            "error": result.error if not result.valid else "",
        }

    # ------------------------------------------------------------------ setup (threaded)

    @_safe
    def ingest_start(self, force: bool = False) -> dict:
        """Kick off a background Scryfall ingest. Returns immediately; the
        frontend polls `ingest_progress()` to drive its progress bar.

        Uses `force=True` to re-download even if the DB already has cards —
        needed for version refreshes when Scryfall ships new sets.
        """
        # Lock the whole check-then-set so two rapid clicks can't spawn
        # two ingest threads both racing on the same download + SQLite handle.
        with self._progress_lock:
            current = self._progress["ingest"]
            existing_thread = self._threads.get("ingest")
            if current.get("running") or (existing_thread and existing_thread.is_alive()):
                return {"ok": False, "error": "Ingest already running"}
            self._progress["ingest"] = {
                "pct": 0, "message": "Starting...", "done": False, "error": None, "running": True,
            }
            t = threading.Thread(target=self._do_ingest, args=(force,), daemon=True)
            self._threads["ingest"] = t
            t.start()
        return {"ok": True, "started": True}

    @_safe
    def ingest_progress(self) -> dict:
        """Poll target for the ingest progress bar. Fields: pct (0-100),
        message (human-readable phase), done (bool), error (nullable),
        running (bool)."""
        return self._read_progress("ingest")

    def _do_ingest(self, force: bool):
        """Background ingest. Runs the existing async pipeline via asyncio,
        updates `self._progress["ingest"]` at phase boundaries so the
        frontend's progress bar animates between phases (download → parse
        → store) rather than freezing on a single long bar.

        All progress mutations go through self._update_progress so the UI
        poller can't see a torn dict. At every phase boundary we check
        self._shutdown_event so close()-during-ingest aborts cleanly
        instead of trampling SQLite mid-write.
        """
        import asyncio
        try:
            # Phase 0: preamble
            self._update_progress("ingest", pct=1, message="Resolving Scryfall bulk URL...")
            if self._shutdown_event.is_set():
                self._update_progress("ingest", message="Ingest cancelled (app closing)", done=True, running=False)
                return
            db = self._get_db()
            existing = db.card_count()
            if existing > 0 and not force:
                self._update_progress(
                    "ingest",
                    pct=100,
                    message=f"Already ingested ({existing} cards). Pass force to re-download.",
                    done=True, running=False,
                )
                return

            from densa_deck.data.scryfall import (
                download_bulk_file, fetch_bulk_data_url, load_bulk_file,
            )

            cache_dir = db.db_path.parent / "bulk"
            cache_dir.mkdir(parents=True, exist_ok=True)
            dest = cache_dir / "oracle_cards.json"

            loop = asyncio.new_event_loop()
            try:
                # Phase 1: resolve the bulk-data URL (tiny HTTP call)
                url = loop.run_until_complete(fetch_bulk_data_url())
                if self._shutdown_event.is_set():
                    self._update_progress("ingest", message="Ingest cancelled (app closing)", done=True, running=False)
                    return
                self._update_progress("ingest", pct=5, message="Downloading bulk card data (~250 MB)...")

                # Phase 2: download (this is the big wait — hundreds of MB)
                loop.run_until_complete(download_bulk_file(url, dest))
                if self._shutdown_event.is_set():
                    self._update_progress("ingest", message="Ingest cancelled (app closing)", done=True, running=False)
                    return
                self._update_progress("ingest", pct=60, message="Parsing cards...")

                # Phase 3: parse
                cards = load_bulk_file(dest)
                self._update_progress("ingest", pct=85, message=f"Storing {len(cards)} cards...")

                # Phase 4: write to SQLite
                db.upsert_cards(cards)
                db.set_metadata("last_ingest", str(len(cards)))

                self._update_progress(
                    "ingest",
                    pct=100, message=f"Done — {len(cards)} cards stored.",
                    done=True, running=False,
                )
            finally:
                loop.close()
                dest.unlink(missing_ok=True)
        except Exception as e:
            self._update_progress(
                "ingest",
                error=str(e), message=f"Ingest failed: {e}",
                done=True, running=False,
            )

    @_safe
    def analyst_pull_start(self, model_key: str = "qwen2.5-3b") -> dict:
        """Background download of the analyst GGUF model."""
        with self._progress_lock:
            current = self._progress["analyst_pull"]
            existing_thread = self._threads.get("analyst_pull")
            if current.get("running") or (existing_thread and existing_thread.is_alive()):
                return {"ok": False, "error": "Pull already running"}
            self._progress["analyst_pull"] = {
                "pct": 0, "message": "Starting...", "done": False, "error": None, "running": True,
                "model": model_key,
            }
            t = threading.Thread(target=self._do_analyst_pull, args=(model_key,), daemon=True)
            self._threads["analyst_pull"] = t
            t.start()
        return {"ok": True, "started": True}

    @_safe
    def analyst_pull_progress(self) -> dict:
        return self._read_progress("analyst_pull")

    def _do_analyst_pull(self, model_key: str):
        """Background model download via urllib.request.urlretrieve with a
        reporthook callback that streams byte-count progress into the
        progress dict.

        Progress mutations go through self._update_progress (locked); the
        reporthook also checks self._shutdown_event and raises to abort the
        download when the app is closing.
        """
        import shutil
        import urllib.request

        try:
            # Pull the model catalog from the CLI module so the single source
            # of truth for URLs + sizes lives there.
            from densa_deck.cli import _ANALYST_MODELS
            from densa_deck.analyst.backends.llama_cpp import DEFAULT_MODEL_PATH

            spec = _ANALYST_MODELS.get(model_key)
            if spec is None:
                self._update_progress(
                    "analyst_pull",
                    error=f"Unknown model: {model_key}", done=True, running=False,
                )
                return

            dest_dir = DEFAULT_MODEL_PATH.parent
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / spec["filename"]

            if dest_file.exists():
                self._update_progress(
                    "analyst_pull",
                    pct=95, message="Already downloaded; wiring as default...",
                )
            else:
                total_bytes = [0]  # use list so nested fn can mutate

                def _report(chunk_num: int, chunk_size: int, total_size: int):
                    if self._shutdown_event.is_set():
                        # urlretrieve's internal loop propagates exceptions
                        # out of the reporthook, which aborts the download.
                        raise RuntimeError("Pull cancelled (app closing)")
                    if total_size > 0:
                        downloaded = chunk_num * chunk_size
                        pct = min(95, int(downloaded / total_size * 95))
                        mb = downloaded // (1024 * 1024)
                        total_mb = total_size // (1024 * 1024)
                        self._update_progress(
                            "analyst_pull",
                            pct=pct,
                            message=f"Downloading {model_key}: {mb}/{total_mb} MB",
                        )
                    total_bytes[0] = chunk_num * chunk_size

                self._update_progress(
                    "analyst_pull",
                    pct=1, message=f"Downloading {model_key} (~{spec['size_mb']} MB)...",
                )
                urllib.request.urlretrieve(spec["url"], dest_file, reporthook=_report)

            # Symlink / copy as the default-path target
            if DEFAULT_MODEL_PATH != dest_file:
                if DEFAULT_MODEL_PATH.exists() or DEFAULT_MODEL_PATH.is_symlink():
                    DEFAULT_MODEL_PATH.unlink()
                try:
                    DEFAULT_MODEL_PATH.symlink_to(dest_file)
                except (OSError, NotImplementedError):
                    shutil.copy2(dest_file, DEFAULT_MODEL_PATH)

            self._update_progress(
                "analyst_pull",
                pct=100, message=f"Analyst model ready at {DEFAULT_MODEL_PATH}.",
                done=True, running=False,
            )
        except Exception as e:
            self._update_progress(
                "analyst_pull",
                error=str(e), message=f"Pull failed: {e}",
                done=True, running=False,
            )

    # ------------------------------------------------------------------ simulations (Pro)

    @_safe
    def run_goldfish(
        self, decklist_text: str, format_: str | None = None,
        name: str = "Deck", sims: int = 1000, seed: int | None = None,
    ) -> dict:
        """Run goldfish simulation. Sync — typically 3-15s for 1000 sims.

        Pro-gated at the UI layer; the API runs if called. Lower default sim
        count than CLI (1000 vs. 10000) because the GUI cares about latency
        over tight error bars — users can re-run with a higher count via the
        sims param if they want.
        """
        from densa_deck.goldfish.runner import run_goldfish_batch
        deck = self._build_deck(decklist_text, format_, name)
        if isinstance(deck, dict):  # error envelope
            return deck
        report = run_goldfish_batch(deck, simulations=sims, seed=seed)
        return _goldfish_to_dict(report)

    @_safe
    def run_gauntlet(
        self, decklist_text: str, format_: str | None = None,
        name: str = "Deck", sims: int = 200, seed: int | None = None,
    ) -> dict:
        """Run matchup gauntlet against 11 archetypes. Sync — typically
        30-60s total (200 sims × 11 archetypes)."""
        from densa_deck.matchup.gauntlet import run_gauntlet as _run
        deck = self._build_deck(decklist_text, format_, name)
        if isinstance(deck, dict):
            return deck
        report = _run(deck, simulations=sims, seed=seed)
        return _gauntlet_to_dict(report)

    def _build_deck(self, decklist_text: str, format_: str | None, name: str):
        """Shared deck-prep path used by both simulation endpoints.

        Returns an error dict if preparation fails, or a resolved Deck object
        otherwise. Callers must branch on `isinstance(result, dict)`.
        """
        if not decklist_text.strip():
            return {"ok": False, "error": "Decklist is empty."}
        db = self._get_db()
        if db.card_count() == 0:
            return {
                "ok": False,
                "error": "Card database not ingested. Open Settings and run Setup.",
                "error_type": "IngestRequired",
            }
        entries = parse_auto(decklist_text)
        if not entries:
            return {"ok": False, "error": "No cards parsed from the decklist."}
        fmt = Format(format_) if format_ else Format.COMMANDER
        return resolve_deck(entries, db, name=name, format=fmt)


# =============================================================================
# Serialization helpers
# =============================================================================


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, data) -> None:
    """Write JSON to `path` atomically via write-to-temp + rename.

    A plain `Path.write_text()` truncates the target first; a crash mid-write
    leaves a half-written file that fails to parse on next load. Writing to
    a temp file in the same directory and renaming is atomic on POSIX and
    near-atomic on Windows (ReplaceFile is used by os.replace), so the
    only observable state is either the OLD file or the NEW file.

    If the write succeeds but the rename fails (permissions), the tmp file
    is left on disk — better than silently losing the data. If the write
    itself fails (disk full, permissions), we clean up the tmp so we don't
    accumulate orphaned .tmp files across retries.
    """
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        # Clean up the partial / failed tmp file before re-raising so a
        # subsequent retry doesn't trip over it.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    os.replace(tmp, path)


def _is_newer(latest: str, current: str) -> bool:
    """Compare semantic version strings. Handles missing / non-numeric parts
    defensively — if either version is malformed, returns False so we don't
    nag the user about phantom updates."""
    if not latest or not current:
        return False
    def _parts(v: str) -> tuple[int, ...]:
        clean = v.lstrip("v").split("-")[0].split("+")[0]
        out = []
        for p in clean.split("."):
            try:
                out.append(int(p))
            except ValueError:
                out.append(0)
        return tuple(out) or (0,)
    return _parts(latest) > _parts(current)


def _snapshot_to_dict(snap: DeckSnapshot) -> dict:
    """Full snapshot payload — used by load / save endpoints. Includes the
    reconstructed editable text so the frontend can drop it in a textarea."""
    return {
        "deck_id": snap.deck_id,
        "version_number": snap.version_number,
        "saved_at": snap.saved_at,
        "notes": snap.notes,
        "decklist": dict(snap.decklist),
        "zones": dict(snap.zones),
        "scores": dict(snap.scores),
        "metrics": dict(snap.metrics),
        "decklist_text": _snapshot_to_text(snap),
    }


def _snapshot_summary(snap: DeckSnapshot) -> dict:
    """Summary row for the history view — excludes the full card list."""
    return {
        "version_number": snap.version_number,
        "saved_at": snap.saved_at,
        "notes": snap.notes,
        "scores": dict(snap.scores),
        "metrics": dict(snap.metrics),
        "card_count": sum(snap.decklist.values()),
    }


def _snapshot_to_text(snap: DeckSnapshot) -> str:
    """Rebuild an editable decklist string from a stored snapshot.

    Output format is the same shape our parser accepts:

        Commander:
        1 Atraxa, Praetors' Voice

        Mainboard:
        1 Sol Ring
        ...

    We emit zones in a stable order (commander first, mainboard second,
    others trailing) so the reconstructed text is deterministic across
    loads — important because the user will edit and re-save, and a
    shuffled diff would be meaningless noise.
    """
    order = ["commander", "companion", "mainboard", "sideboard", "maybeboard"]
    seen_in_order = [z for z in order if z in snap.zones]
    other_zones = [z for z in snap.zones.keys() if z not in order]
    lines: list[str] = []
    for zone in seen_in_order + other_zones:
        lines.append(f"{zone.capitalize()}:")
        # Count quantities from the decklist, limited to cards listed in this zone
        for name in sorted(set(snap.zones.get(zone, []))):
            qty = snap.decklist.get(name, 0)
            if qty > 0:
                lines.append(f"{qty} {name}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _goldfish_to_dict(report) -> dict:
    """Flatten GoldfishReport for the frontend chart renderer."""
    return {
        "simulations": report.simulations,
        "max_turns": report.max_turns,
        "average_mulligans": report.average_mulligans,
        "mulligan_distribution": {str(k): v for k, v in report.mulligan_distribution.items()},
        "average_damage_by_turn": {str(k): v for k, v in report.average_damage_by_turn.items()},
        "average_kill_turn": report.average_kill_turn,
        "kill_rate": report.kill_rate,
        "kill_turn_distribution": {str(k): v for k, v in report.kill_turn_distribution.items()},
        "average_creatures_by_turn": {str(k): v for k, v in report.average_creatures_by_turn.items()},
        "average_lands_by_turn": {str(k): v for k, v in report.average_lands_by_turn.items()},
        "average_mana_spent_by_turn": {str(k): v for k, v in report.average_mana_spent_by_turn.items()},
        "commander_cast_rate": report.commander_cast_rate,
        "average_commander_turn": report.average_commander_turn,
        "average_spells_cast": report.average_spells_cast,
        "most_cast_spells": [list(pair) for pair in report.most_cast_spells],
        "objective_pass_rates": dict(getattr(report, "objective_pass_rates", {})),
    }


def _gauntlet_to_dict(report) -> dict:
    """Flatten GauntletReport for the frontend table renderer."""
    return {
        "simulations_per_matchup": report.simulations_per_matchup,
        "total_games": report.total_games,
        "overall_win_rate": report.overall_win_rate,
        "weighted_win_rate": report.weighted_win_rate,
        "best_matchup": report.best_matchup,
        "best_win_rate": report.best_win_rate,
        "worst_matchup": report.worst_matchup,
        "worst_win_rate": report.worst_win_rate,
        "speed_score": report.speed_score,
        "resilience_score": report.resilience_score,
        "interaction_score": report.interaction_score,
        "consistency_score": report.consistency_score,
        "matchups": [
            {
                "archetype": m.archetype_name,
                "wins": m.wins,
                "losses": m.losses,
                "simulations": m.simulations,
                "win_rate": m.win_rate,
                "avg_turns": m.avg_turns,
            }
            for m in report.matchups
        ],
    }


def _analysis_to_dict(deck, static_result, archetype, power, advanced, castability, staples, unresolved) -> dict:
    """Flatten the cluster of analysis dataclasses into JSON for the frontend."""
    return {
        "deck_name": deck.name,
        "format": deck.format.value if deck.format else None,
        "archetype": archetype.value if hasattr(archetype, "value") else str(archetype),
        "total_cards": static_result.total_cards,
        "land_count": static_result.land_count,
        "ramp_count": static_result.ramp_count,
        "draw_count": static_result.draw_engine_count,
        "interaction_count": static_result.interaction_count,
        "average_cmc": static_result.average_cmc,
        "mana_curve": dict(static_result.mana_curve),
        "color_distribution": dict(static_result.color_distribution),
        "color_sources": dict(static_result.color_sources),
        "type_distribution": dict(static_result.type_distribution),
        "scores": dict(static_result.scores),
        "issues": [
            {"severity": i.severity, "message": i.message, "card": i.card_name}
            for i in static_result.issues
        ],
        "recommendations": list(static_result.recommendations),
        "power": {
            "overall": power.overall,
            "tier": power.tier,
            "speed": power.speed,
            "interaction": power.interaction,
            "combo_potential": power.combo_potential,
            "mana_efficiency": power.mana_efficiency,
            "win_condition_quality": power.win_condition_quality,
            "card_quality": power.card_quality,
            "reasons_up": list(power.reasons_up),
            "reasons_down": list(power.reasons_down),
        },
        "advanced": {
            "mana_base_grade": advanced.mana_base_grade,
            "mana_base_notes": list(advanced.mana_base_notes),
            "synergies": [
                {"card_a": s.card_a, "card_b": s.card_b, "reason": s.reason, "strength": s.strength}
                for s in advanced.synergies
            ],
            "advanced_recommendations": list(advanced.advanced_recommendations),
        },
        "castability": {
            "unreliable_cards": [
                {
                    "name": c.name, "mana_cost": c.mana_cost,
                    "on_curve_probability": c.on_curve_probability,
                    "bottleneck_color": c.bottleneck_color,
                }
                for c in castability.unreliable_cards
            ],
            "color_bottlenecks": dict(castability.color_bottlenecks),
        },
        "staples": {
            "format": staples.format,
            "color_identity": list(staples.color_identity),
            "staple_coverage": staples.staple_coverage,
            "missing": [
                {"name": s.name, "reason": s.reason, "priority": s.priority}
                for s in staples.missing
            ],
            "present_staples": list(staples.present_staples),
        },
        "unresolved_cards": unresolved,
    }
