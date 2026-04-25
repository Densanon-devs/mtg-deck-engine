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
            "combo_refresh": {"pct": 0, "message": "idle", "done": True, "error": None, "running": False},
        }
        # Lazily resolved by _get_combo_store() on first combo call.
        self._combo_store = None
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
        # Populated by _do_ingest when the ingest was an UPDATE (i.e. the
        # card DB already had cards pre-ingest). Frontend fetches this
        # via get_last_ingest_diff() to render the "what changed" modal.
        # None on first-run ingests and after dismiss_last_ingest_diff().
        self._last_ingest_diff: dict | None = None
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
        # Combos store opens lazily on first detect/refresh — close it
        # too so the SQLite file handle isn't left dangling. Without this,
        # Windows test tempdirs can't be cleaned up because the .db file
        # is still locked by our connection.
        if getattr(self, "_combo_store", None) is not None:
            try:
                self._combo_store.close()
            except Exception:
                pass
            self._combo_store = None

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

        # Combo detection — fed into archetype + power estimators so the
        # downstream recommendations are combo-coherent. Skipped silently
        # when the cache hasn't been refreshed yet.
        detected_combo_count = 0
        near_miss_combo_count = 0
        try:
            cstore = self._get_combo_store()
            if cstore.combo_count() > 0:
                from densa_deck.combos import detect_combos, detect_near_miss_combos
                deck_card_names = [e.card.name for e in deck.entries if e.card]
                deck_color_identity = sorted({
                    c.value for e in deck.entries if e.card for c in e.card.color_identity
                })
                matches = detect_combos(
                    store=cstore, deck_card_names=deck_card_names,
                    deck_color_identity=deck_color_identity, limit=50,
                )
                detected_combo_count = len(matches)
                near_miss_combo_count = len(detect_near_miss_combos(
                    store=cstore, deck_card_names=deck_card_names,
                    deck_color_identity=deck_color_identity, max_missing=1, limit=50,
                ))
        except Exception:
            # Non-fatal — analyze_deck still returns its core fields.
            pass

        archetype = detect_archetype(deck, detected_combo_count=detected_combo_count)
        power = estimate_power_level(
            deck,
            detected_combo_count=detected_combo_count,
            near_miss_combo_count=near_miss_combo_count,
        )
        advanced = run_advanced_analysis(deck, static_result.color_sources)
        castability = analyze_castability(deck, static_result.color_sources)
        staples = check_staples(deck)

        # Combo-shaped recommendations appended after the rule engine —
        # surface "X combo lines detected" / "no combos despite combo
        # archetype" so users see the combo context inline with the rest
        # of the analysis. Doesn't change the AnalysisResult schema; just
        # extends the recommendations list.
        if detected_combo_count > 0:
            static_result.recommendations.append(
                f"{detected_combo_count} combo line(s) detected — surfaced separately under "
                f"Detected combos in the analysis output."
            )
        elif near_miss_combo_count >= 3:
            static_result.recommendations.append(
                f"{near_miss_combo_count} combos within 1 card of completion — "
                f"see the near-miss panel to pick which one to lean into."
            )
        if str(getattr(archetype, "value", archetype)).lower() == "combo" and detected_combo_count == 0:
            static_result.recommendations.append(
                "Archetype reads as combo but no concrete combo lines were detected — "
                "either refresh the combo cache (Settings → Combo data) or pivot the deck."
            )

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
        """Diff two versions of a deck. Returns added/removed/score-delta
        plus combo gains/losses if the combo cache is populated."""
        store = self._get_vstore()
        a = store.get_version(deck_id, version_a)
        b = store.get_version(deck_id, version_b)
        if a is None or b is None:
            return {"ok": False, "error": "One or both versions not found."}
        d = diff_versions(a, b)

        # Combo diff — surface which combo lines became complete or broken
        # between the two versions. Skipped when the cache is empty.
        combo_gained: list[dict] = []
        combo_lost: list[dict] = []
        try:
            cstore = self._get_combo_store()
            if cstore.combo_count() > 0:
                from densa_deck.combos import diff_combos
                # Reconstruct card-name list per version from the snapshot's
                # decklist dict (key = card name, value = qty). Color identity
                # comes from the latest version's metadata-free path: we
                # collect any color we can see in either snapshot's zones.
                a_names = list(a.decklist.keys())
                b_names = list(b.decklist.keys())
                # We don't have color identity directly on snapshots; pass
                # None so diff_combos uses any-color semantics. The combo
                # MUST be fully present in either version anyway, so a
                # color-mismatch combo just won't have appeared in the
                # MatchedCombo result for the prior version.
                cdiff = diff_combos(
                    store=cstore,
                    before_card_names=a_names,
                    after_card_names=b_names,
                    color_identity=None,
                )
                combo_gained = [_combo_to_dict(m) for m in cdiff["gained"][:10]]
                combo_lost = [_combo_to_dict(m) for m in cdiff["lost"][:10]]
        except Exception:
            # Non-fatal — if anything blows up in the combo path, the
            # version diff still returns its core fields.
            pass

        return {
            "version_a": a.version_number,
            "version_b": b.version_number,
            "added": dict(d.added),
            "removed": dict(d.removed),
            "changed_qty": {k: list(v) for k, v in d.changed_qty.items()},
            "total_added": d.total_added,
            "total_removed": d.total_removed,
            "score_deltas": dict(d.score_deltas),
            "combo_gained": combo_gained,
            "combo_lost": combo_lost,
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

    # ------------------------------------------------------------------ card DB update + preferences

    @_safe
    def check_card_db_update(self) -> dict:
        """Ask Scryfall whether its bulk oracle_cards file is newer than what
        we last ingested. Cheap (tiny manifest call — no full bulk download).

        Returns `{available, remote_updated_at, local_updated_at, size_mb}`.
        Fails open on any network or parse error with `{available: False,
        error: "..."}` so an offline launch doesn't nag the user.

        Note: `available=True` only when the remote timestamp is strictly
        newer than our recorded `scryfall_bulk_updated_at`. If the local
        metadata is missing (e.g. user ingested on an older build that didn't
        record it), we fall back to `available=True` so they get a chance to
        refresh. First-run ingests (no card DB at all) are handled upstream
        — the Settings panel shows Setup instead of Update.
        """
        import asyncio
        from densa_deck.data.scryfall import fetch_bulk_data_manifest

        db = self._get_db()
        # If there are no cards at all, the frontend should route the user
        # through the first-run setup, not an "update available" banner.
        if db.card_count() == 0:
            return {"available": False, "reason": "no_local_db"}

        local_ts = db.get_metadata("scryfall_bulk_updated_at") or ""

        loop = asyncio.new_event_loop()
        try:
            try:
                manifest = loop.run_until_complete(fetch_bulk_data_manifest())
            except Exception as e:
                return {"available": False, "error": str(e)}
        finally:
            loop.close()

        remote_ts = str(manifest.get("updated_at", ""))
        size_bytes = manifest.get("size") or 0
        try:
            size_mb = round(int(size_bytes) / (1024 * 1024), 1)
        except (TypeError, ValueError):
            size_mb = 0.0

        # String comparison works for Scryfall's ISO-8601 timestamps (they
        # all use the same offset), but guard against a blank remote to
        # avoid flagging every launch when Scryfall gives us nothing useful.
        if not remote_ts:
            return {
                "available": False,
                "remote_updated_at": "",
                "local_updated_at": local_ts,
                "size_mb": size_mb,
                "reason": "no_remote_timestamp",
            }
        available = (remote_ts > local_ts) if local_ts else True
        return {
            "available": available,
            "remote_updated_at": remote_ts,
            "local_updated_at": local_ts,
            "size_mb": size_mb,
        }

    @_safe
    def get_user_preferences(self) -> dict:
        """Read user preferences from ~/.densa-deck/config.json.

        Returns the full preferences dict with defaults filled in for any
        missing keys so the frontend can trust the shape. New keys default to
        the safe/off state — both auto_check_card_db and auto_download_card_db
        are off unless the user opts in from Settings.
        """
        prefs = _load_user_prefs()
        return {
            "tier": prefs.get("tier", "free"),
            "auto_check_card_db": bool(prefs.get("auto_check_card_db", False)),
            "auto_download_card_db": bool(prefs.get("auto_download_card_db", False)),
        }

    @_safe
    def set_user_preferences(self, prefs: dict) -> dict:
        """Patch the preferences file. Accepts a partial dict — only keys
        present in the payload are updated, the rest stay untouched.

        Enforces the auto_download-implies-auto_check invariant server-side
        so a buggy or malicious frontend can't POST an inconsistent state
        that would silently re-enable auto-download after the user turned
        auto-check off. Unknown keys are ignored (forward-compatibility for
        a newer frontend rolled back to an older backend).
        """
        if not isinstance(prefs, dict):
            return {"ok": False, "error": "Preferences payload must be a dict."}

        current = _load_user_prefs()
        # Merge the effective new state so the constraint check sees the
        # final values, not just the delta.
        merged = dict(current)
        allowed_keys = {"auto_check_card_db", "auto_download_card_db"}
        for key in allowed_keys:
            if key in prefs:
                merged[key] = bool(prefs[key])

        if merged.get("auto_download_card_db") and not merged.get("auto_check_card_db"):
            return {
                "ok": False,
                "error": "auto_download requires auto_check",
                "error_type": "InvalidPreference",
            }

        _save_user_prefs(merged)
        return {
            "tier": merged.get("tier", "free"),
            "auto_check_card_db": bool(merged.get("auto_check_card_db", False)),
            "auto_download_card_db": bool(merged.get("auto_download_card_db", False)),
        }

    @_safe
    def get_last_ingest_diff(self) -> dict:
        """Return the pending "what changed" diff from the most recent update
        ingest, then clear it (single-use).

        The frontend calls this once, right after ingest_progress reports
        done=True on an update-path ingest. Returning None (via the error
        envelope's `data: null`) means either (a) the last ingest was a
        first-run, not an update, or (b) the diff was already consumed.
        Keeping it single-use avoids re-showing the modal every time the
        user switches to Settings after the initial dismissal.

        Held under _progress_lock because _do_ingest writes
        self._last_ingest_diff from a worker thread; without the lock,
        a poll racing the worker's final assignment could read torn
        state or lose the diff entirely.
        """
        with self._progress_lock:
            diff = self._last_ingest_diff
            self._last_ingest_diff = None
        return diff

    # ------------------------------------------------------------------ deckbuilder (Build tab)

    @_safe
    def search_cards(self, query: dict | None = None) -> dict:
        """Structured card search powering the Build tab's left column.

        `query` is a dict with any of: name, colors, color_match, cmc_min,
        cmc_max, types, format_legal, rarity, max_price, set_code, limit,
        offset. See `CardDatabase.search_structured` for semantics. Returns
        `{cards: [...], total: N, offset: N, limit: N}` — the frontend uses
        offset + total to decide whether to show "Load more".

        Hard-capped at 120 results per call to keep payload size sane; the
        frontend can paginate with offset for more.
        """
        query = query or {}
        db = self._get_db()
        if db.card_count() == 0:
            return {
                "ok": False,
                "error": "Card database not ingested. Open Settings and run Setup.",
                "error_type": "IngestRequired",
            }

        limit = min(int(query.get("limit") or 60), 120)
        offset = max(int(query.get("offset") or 0), 0)
        cards, total = db.search_structured(
            name=query.get("name"),
            colors=query.get("colors"),
            color_match=query.get("color_match") or "identity",
            cmc_min=query.get("cmc_min"),
            cmc_max=query.get("cmc_max"),
            types=query.get("types"),
            format_legal=query.get("format_legal"),
            rarity=query.get("rarity"),
            max_price=query.get("max_price"),
            set_code=query.get("set_code"),
            limit=limit,
            offset=offset,
        )
        return {
            "cards": [_card_to_builder_dict(c) for c in cards],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    @_safe
    def get_card(self, name: str) -> dict:
        """Single-card lookup used for hover popups + details panels.

        Returns the same shape as `search_cards` rows, plus the full
        oracle text + face list so the popup can render the card
        faithfully. Uses `lookup_by_name` (canonical + split/DFC fallback)
        so flavor-name imports and alt-name lookups resolve the same
        way the deck resolver handles them.
        """
        if not name or not name.strip():
            return {"ok": False, "error": "Name is required."}
        db = self._get_db()
        card = db.lookup_by_name(name)
        if card is None:
            return {"ok": False, "error": f"No card named '{name}'."}
        return _card_to_builder_dict(card, include_full=True)

    def _builder_draft_path(self) -> Path:
        """Resolve the draft file path against THIS AppApi's db_path so
        tests and custom-DB-path installs don't leak drafts into the
        default `~/.densa-deck/drafts.json`. Falls back to the global
        helper when no override was supplied at construction time."""
        if self._db_path:
            return Path(self._db_path).parent / "drafts.json"
        return _builder_draft_path()

    @_safe
    def save_builder_draft(self, draft: dict) -> dict:
        """Autosave the current builder draft to ~/.densa-deck/drafts.json.

        `draft` is the frontend's `builderState.deck` + metadata (name,
        format, zone counts). Written atomically so a crash mid-write
        can't leave the next launch with a corrupt draft. Overwrites
        silently — builder drafts are single-slot in v1 (one draft at a
        time), not a multi-draft history.
        """
        if not isinstance(draft, dict):
            return {"ok": False, "error": "Draft payload must be a dict."}
        path = self._builder_draft_path()
        try:
            _atomic_write_json(path, draft)
        except OSError as e:
            return {"ok": False, "error": f"Failed to save draft: {e}"}
        return {"saved_at": _now_iso(), "path": str(path)}

    @_safe
    def load_builder_draft(self) -> dict:
        """Restore the builder draft from disk, or None if no draft exists.

        Missing file returns None (via the {ok:true, data:null} envelope);
        corrupt file returns None too — the frontend treats both the same
        ("start fresh"). A corrupt draft is quarantined beside the real
        file for support forensics, matching the coach-sessions recovery
        pattern.

        Wrapped in a broad OSError catch so the FIRST call ever — before
        anything has touched ~/.densa-deck/ to create the dir — doesn't
        crash the bridge if Path.exists() / read_text() can't even reach
        the directory (permission denied, network share unavailable, etc).
        """
        path = self._builder_draft_path()
        try:
            if not path.exists():
                return None
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._quarantine_bad_file(path, reason=str(exc))
            return None
        except OSError:
            return None
        return data if isinstance(data, dict) else None

    @_safe
    def clear_builder_draft(self) -> dict:
        """Delete the builder draft file. Called when the user saves the
        draft as a real deck or clicks Clear in the builder."""
        path = self._builder_draft_path()
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            return {"ok": False, "error": f"Failed to clear draft: {e}"}
        return {"cleared": True}

    # ------------------------------------------------------------------ combos (Commander Spellbook)

    @_safe
    def get_combo_status(self) -> dict:
        """Return the combo-cache status for the Settings panel.

        Empty cache = "Refresh combos" button surfaced. Non-empty =
        show the combo count and last-refresh timestamp.
        """
        from densa_deck.combos import ComboStore
        store = self._get_combo_store()
        return {
            "combo_count": store.combo_count(),
            "last_refresh_at": store.get_metadata("last_refresh_at") or "",
            "source": store.get_metadata("source") or "",
        }

    @_safe
    def detect_near_miss_combos_for_deck(
        self,
        decklist_text: str,
        format_: str | None = None,
        name: str = "Deck",
        max_missing: int = 1,
        limit: int = 25,
    ) -> dict:
        """Find combos the deck is missing only N cards from completing.

        Powers the "you're 1 card away" surface in the Build tab and the
        Analyze view. Same gates as detect_combos_for_deck — empty cache
        returns ComboCacheEmpty so the frontend prompts the user to
        refresh combo data first.
        """
        store = self._get_combo_store()
        if store.combo_count() == 0:
            return {
                "ok": False,
                "error": "Combo data not loaded yet. Refresh from Settings → Combo data first.",
                "error_type": "ComboCacheEmpty",
            }
        deck = self._build_deck(decklist_text, format_, name)
        if isinstance(deck, dict):
            return deck
        from densa_deck.combos import detect_near_miss_combos
        deck_card_names = [e.card.name for e in deck.entries if e.card]
        deck_color_identity = sorted({
            c.value for e in deck.entries if e.card for c in e.card.color_identity
        })
        near = detect_near_miss_combos(
            store=store,
            deck_card_names=deck_card_names,
            deck_color_identity=deck_color_identity,
            max_missing=int(max_missing or 1),
            limit=int(limit or 25),
        )
        return {
            "deck_name": deck.name,
            "match_count": len(near),
            "max_missing": max_missing,
            "color_identity": deck_color_identity,
            "near_combos": [_near_miss_to_dict(n) for n in near],
        }

    @_safe
    def suggest_deckbuild_additions(
        self,
        decklist_text: str,
        format_: str | None = None,
        name: str = "Deck",
        count: int = 8,
        budget_usd: float | None = None,
    ) -> dict:
        """Suggest cards to add to the in-progress deck. Pro-gated.

        Combines three signals into a single ranked list:
          1. Role-gap fillers — pulled from the existing
             `find_add_candidates` for each gap detected by
             `_detect_role_gaps`.
          2. Combo-completion adds — cards that, if added, complete a
             near-miss combo. Surfaced from the combo store when
             populated; skipped quietly when it isn't.
          3. Top-pick land/ramp/draw fillers when the deck lacks the
             format-target floor.

        Returns a single sorted list of `{name, mana_cost, cmc,
        type_line, role, reason, source}` rows the Build tab can render
        as clickable +1 buttons. No LLM call — deterministic so it's
        fast enough to refresh as the user edits the draft.

        Pro-gated because it concentrates a lot of value into one
        call — the Free-tier user can still see static analysis +
        combo detection.
        """
        if get_user_tier() != Tier.PRO:
            return {
                "ok": False,
                "error": "AI deckbuild suggestions require Densa Deck Pro.",
                "error_type": "ProRequired",
            }
        deck = self._build_deck(decklist_text, format_, name)
        if isinstance(deck, dict):
            return deck

        from densa_deck.analyst.add_candidates import find_add_candidates
        from densa_deck.analyst.runner import _detect_role_gaps
        from densa_deck.models import Format

        analysis = run_static_analysis(deck)
        deck_color_identity = {
            c.value for e in deck.entries if e.card for c in e.card.color_identity
        }
        deck_names = {e.card.name for e in deck.entries if e.card}
        try:
            fmt = Format(format_) if format_ else (deck.format or Format.COMMANDER)
        except ValueError:
            fmt = Format.COMMANDER

        suggestions: list[dict] = []
        seen_names: set[str] = set()

        # Pass 1: role-gap candidates — three per detected gap so the user
        # sees a spread, not 8 ramp pieces stacked on top of each other.
        gaps = _detect_role_gaps(analysis)
        per_role = max(1, count // max(1, len(gaps) or 1))
        db = self._get_db()
        for role in gaps:
            cands = find_add_candidates(
                db=db, role=role, deck_color_identity=deck_color_identity,
                format_=fmt, exclude_names=deck_names | seen_names,
                limit=per_role, budget_usd=budget_usd,
            )
            for cand in cands:
                if cand.card.name in seen_names:
                    continue
                seen_names.add(cand.card.name)
                suggestions.append({
                    "name": cand.card.name,
                    "mana_cost": cand.card.mana_cost or "",
                    "cmc": float(cand.card.cmc or 0),
                    "type_line": cand.card.type_line or "",
                    "role": role.value,
                    "source": "role-gap",
                    "reason": f"Closes {role.value.replace('_', ' ')} gap",
                    "image_url": _image_url_safe(cand.card.scryfall_id),
                    "price_usd": cand.card.price_usd,
                })

        # Pass 2: combo-completion picks — only when the cache is populated.
        # We score "combo completion" highly because it's a unique add path
        # that role-gap detection can't surface.
        store = self._get_combo_store()
        if store.combo_count() > 0:
            from densa_deck.combos import detect_near_miss_combos
            near = detect_near_miss_combos(
                store=store,
                deck_card_names=list(deck_names),
                deck_color_identity=sorted(deck_color_identity),
                max_missing=1,
                limit=20,
            )
            for nm in near:
                # Single-card-away combos are the highest-value adds; we
                # surface up to 3 of them at the top of the suggestion list.
                if len(suggestions) >= count + 3:
                    break
                if not nm.missing_cards:
                    continue
                missing_card = nm.missing_cards[0]
                if missing_card in seen_names or missing_card in deck_names:
                    continue
                # Look up the card in the DB so we can show mana cost / image.
                card = db.lookup_by_name(missing_card)
                if card is None:
                    continue
                # Color-identity guard — find_add_candidates does this for
                # role-gap picks; do it again here so combo completions
                # don't slip in cards outside the deck's CI.
                card_ci = {c.value for c in card.color_identity}
                if not card_ci.issubset(deck_color_identity):
                    continue
                seen_names.add(card.name)
                suggestions.append({
                    "name": card.name,
                    "mana_cost": card.mana_cost or "",
                    "cmc": float(card.cmc or 0),
                    "type_line": card.type_line or "",
                    "role": "combo",
                    "source": "combo-completion",
                    "reason": f"Completes combo: {nm.combo.short_label()}",
                    "image_url": _image_url_safe(card.scryfall_id),
                    "price_usd": card.price_usd,
                    "combo_url": nm.combo.spellbook_url,
                })

        # Sort: combo-completions first (highest impact), then role-gaps by
        # role priority. Within each group, sort by CMC ascending (cheaper
        # = easier to slot).
        source_order = {"combo-completion": 0, "role-gap": 1}
        suggestions.sort(key=lambda s: (
            source_order.get(s["source"], 9),
            s.get("cmc", 99),
            s.get("name", ""),
        ))
        # Cap to the requested count so the UI list isn't overwhelmed.
        suggestions = suggestions[: int(count or 8)]

        return {
            "deck_name": deck.name,
            "count": len(suggestions),
            "gaps": [g.value for g in gaps],
            "color_identity": sorted(deck_color_identity),
            "suggestions": suggestions,
        }

    @_safe
    def export_deck_format(
        self,
        decklist_text: str,
        target: str,
        format_: str | None = None,
        name: str = "Deck",
    ) -> dict:
        """Export the resolved deck into one of: 'mtgo', 'mtga', 'moxfield'.

        Free tier — these are commodity formats every deckbuilder needs.
        Returns `{format, content, filename_hint}`. Frontend can offer
        a download / copy-to-clipboard.
        """
        deck = self._build_deck(decklist_text, format_, name)
        if isinstance(deck, dict):
            return deck

        target = (target or "").lower().strip()
        if target == "mtgo":
            content, fname = _export_mtgo(deck)
        elif target == "mtga":
            content, fname = _export_mtga(deck)
        elif target == "moxfield":
            content, fname = _export_moxfield_text(deck)
        else:
            return {"ok": False, "error": f"Unknown export target '{target}'. Valid: mtgo / mtga / moxfield."}
        return {
            "format": target,
            "content": content,
            "filename_hint": fname,
        }

    @_safe
    def assess_bracket_fit(
        self,
        decklist_text: str,
        target_bracket: str,
        format_: str | None = None,
        name: str = "Deck",
    ) -> dict:
        """Assess how a deck fits a target Commander bracket (1-precon ... 5-cedh).

        Returns the verdict + headline + over/under signals + a punch-list
        of concrete recommendations. Pure rule-engine output (no LLM call)
        so it's fast enough to run inline in the Analyze view.
        """
        deck = self._build_deck(decklist_text, format_, name)
        if isinstance(deck, dict):
            return deck

        from densa_deck.analysis.brackets import bracket_fit, BRACKETS
        from densa_deck.analysis.power_level import estimate_power_level

        analysis = run_static_analysis(deck)
        power = estimate_power_level(deck)

        valid_labels = {b[0] for b in BRACKETS}
        if target_bracket not in valid_labels:
            return {
                "ok": False,
                "error": f"Unknown bracket '{target_bracket}'. Valid: {sorted(valid_labels)}",
            }

        # Combo count for the constraint check — only when the cache is
        # populated. Empty cache means "we don't know about combos yet";
        # we treat unknown as zero rather than blocking the bracket call.
        combo_count = 0
        store = self._get_combo_store()
        if store.combo_count() > 0:
            from densa_deck.combos import detect_combos
            deck_card_names = [e.card.name for e in deck.entries if e.card]
            deck_color_identity = sorted({
                c.value for e in deck.entries if e.card for c in e.card.color_identity
            })
            combo_count = len(detect_combos(
                store=store,
                deck_card_names=deck_card_names,
                deck_color_identity=deck_color_identity,
                limit=50,
            ))

        fit = bracket_fit(
            deck=deck, target_label=target_bracket,
            power_overall=float(power.overall),
            interaction_count=int(analysis.interaction_count),
            ramp_count=int(analysis.ramp_count),
            detected_combo_count=combo_count,
        )
        return {
            "detected_label": fit.detected_label,
            "detected_name": fit.detected_name,
            "target_label": fit.target_label,
            "target_name": fit.target_name,
            "verdict": fit.verdict,
            "headline": fit.headline,
            "delta": fit.delta,
            "over_signals": list(fit.over_signals),
            "under_signals": list(fit.under_signals),
            "recommendations": list(fit.recommendations),
            "power_overall": round(float(power.overall), 2),
            "combo_count": combo_count,
        }

    @_safe
    def detect_combos_for_deck(
        self,
        decklist_text: str,
        format_: str | None = None,
        name: str = "Deck",
        limit: int = 25,
    ) -> dict:
        """Run combo detection on the given decklist.

        Returns a dict with the matched combos sorted by popularity. The
        UI can render the most-popular 25 by default and let the user
        load more. Empty result set is fine — most casual decks won't
        have combos.

        Frontend should also call get_combo_status first; if the cache is
        empty, prompt the user to click "Refresh combos" before running
        detection (we don't auto-refresh on demand to keep this call cheap).
        """
        store = self._get_combo_store()
        if store.combo_count() == 0:
            return {
                "ok": False,
                "error": "Combo data not loaded yet. Refresh from Settings → Combo data first.",
                "error_type": "ComboCacheEmpty",
            }
        deck = self._build_deck(decklist_text, format_, name)
        if isinstance(deck, dict):  # error envelope
            return deck
        from densa_deck.combos import detect_combos
        deck_card_names = [e.card.name for e in deck.entries if e.card]
        deck_color_identity = sorted({
            c.value for e in deck.entries if e.card for c in e.card.color_identity
        })
        matches = detect_combos(
            store=store,
            deck_card_names=deck_card_names,
            deck_color_identity=deck_color_identity,
            limit=limit,
        )
        return {
            "deck_name": deck.name,
            "match_count": len(matches),
            "color_identity": deck_color_identity,
            "combos": [_combo_to_dict(m) for m in matches],
        }

    @_safe
    def combo_refresh_start(self) -> dict:
        """Kick off a background combo-data refresh from Commander Spellbook.

        Polls via combo_refresh_progress() — same UX shape as ingest_start
        / analyst_pull_start.
        """
        with self._progress_lock:
            current = self._progress.get("combo_refresh", {})
            existing_thread = self._threads.get("combo_refresh")
            if current.get("running") or (existing_thread and existing_thread.is_alive()):
                return {"ok": False, "error": "Combo refresh already running"}
            self._progress["combo_refresh"] = {
                "pct": 0, "message": "Starting...", "done": False,
                "error": None, "running": True,
            }
            t = threading.Thread(target=self._do_combo_refresh, daemon=True)
            self._threads["combo_refresh"] = t
            t.start()
        return {"ok": True, "started": True}

    @_safe
    def combo_refresh_progress(self) -> dict:
        return self._read_progress("combo_refresh")

    def _get_combo_store(self):
        """Lazily resolve a ComboStore instance scoped to this AppApi.

        Stored next to the card DB so all per-user state stays under
        a single ~/.densa-deck/ directory.
        """
        from densa_deck.combos import ComboStore, DEFAULT_COMBO_DB_PATH
        if getattr(self, "_combo_store", None) is None:
            if self._db_path:
                path = Path(self._db_path).parent / "combos.db"
            else:
                path = DEFAULT_COMBO_DB_PATH
            self._combo_store = ComboStore(db_path=path)
        return self._combo_store

    def _do_combo_refresh(self):
        """Background worker for combo refresh.

        Walks the Commander Spellbook /variants/ pagination, writing into
        the local SQLite store. Updates self._progress["combo_refresh"]
        at every page boundary so the UI bar advances without the worker
        having to estimate a total upfront.
        """
        try:
            from densa_deck.combos import refresh_combo_snapshot
            store = self._get_combo_store()

            def _on_page(pages: int, combos_seen: int):
                # The dataset is ~30k items at PAGE_SIZE=500 → ~60 pages.
                # Cap reported pct at 95 until the upsert finishes so the
                # bar visibly completes only when the data is actually
                # written, not just downloaded.
                est_pct = min(int((pages / 60.0) * 95), 95)
                self._update_progress(
                    "combo_refresh",
                    pct=est_pct,
                    message=f"Fetched {combos_seen} combos across {pages} pages...",
                )

            written = refresh_combo_snapshot(
                store=store,
                user_agent=f"DensaDeck/0.2.0 (combo-fetch)",
                progress_cb=_on_page,
            )
            self._update_progress(
                "combo_refresh",
                pct=100,
                message=f"Done — {written} combos cached.",
                done=True, running=False,
            )
        except Exception as e:
            self._update_progress(
                "combo_refresh",
                error=str(e),
                message=f"Combo refresh failed: {e}",
                done=True, running=False,
            )

    # ------------------------------------------------------------------ analyst Phase 6

    @_safe
    def compare_decks_analyst(
        self,
        deck_a_id: str,
        deck_b_id: str,
    ) -> dict:
        """Compare two saved decks and return analyst prose + numeric deltas.

        Both decks must be saved (have at least one VersionStore snapshot).
        Loads the latest of each, runs static analysis on both, computes
        the diff, and runs the compare-decks prompt against the cached
        coach backend.
        """
        store = self._get_vstore()
        snap_a = store.get_latest(deck_a_id)
        snap_b = store.get_latest(deck_b_id)
        if snap_a is None:
            return {"ok": False, "error": f"No saved versions for deck '{deck_a_id}'."}
        if snap_b is None:
            return {"ok": False, "error": f"No saved versions for deck '{deck_b_id}'."}

        from densa_deck.analyst.phase6 import compare_decks
        from densa_deck.analysis.power_level import estimate_power_level
        from densa_deck.formats.profiles import detect_archetype
        from densa_deck.versioning.storage import diff_versions

        deck_a = self._build_deck(_snapshot_to_text(snap_a), snap_a.format,
                                  snap_a.name or deck_a_id)
        if isinstance(deck_a, dict):
            return deck_a
        deck_b = self._build_deck(_snapshot_to_text(snap_b), snap_b.format,
                                  snap_b.name or deck_b_id)
        if isinstance(deck_b, dict):
            return deck_b

        ar = run_static_analysis(deck_a)
        br = run_static_analysis(deck_b)
        pa = estimate_power_level(deck_a)
        pb = estimate_power_level(deck_b)
        archetype_a = detect_archetype(deck_a)
        archetype_b = detect_archetype(deck_b)

        # Use the existing diff_versions on the snapshot pair so the
        # "added" / "removed" lists have the same shape the diff modal
        # already understands.
        d = diff_versions(snap_a, snap_b)
        added_cards = list(d.added.keys())
        removed_cards = list(d.removed.keys())

        # Score deltas: use static analysis scores (b - a per axis).
        score_deltas = {
            k: float(br.scores.get(k, 0.0) - ar.scores.get(k, 0.0))
            for k in (ar.scores.keys() | br.scores.keys())
        }
        role_deltas = {
            "lands":       int(br.land_count - ar.land_count),
            "ramp":        int(br.ramp_count - ar.ramp_count),
            "draw":        int(br.draw_engine_count - ar.draw_engine_count),
            "interaction": int(br.interaction_count - ar.interaction_count),
            "threats":     int(br.threat_count - ar.threat_count),
        }

        backend = self._get_coach_backend()
        result = compare_decks(
            backend=backend,
            deck_a_name=deck_a.name,
            deck_b_name=deck_b.name,
            deck_a_archetype=archetype_a.value if hasattr(archetype_a, "value") else str(archetype_a),
            deck_b_archetype=archetype_b.value if hasattr(archetype_b, "value") else str(archetype_b),
            deck_a_power=float(pa.overall),
            deck_b_power=float(pb.overall),
            added_cards=added_cards,
            removed_cards=removed_cards,
            score_deltas=score_deltas,
            role_deltas=role_deltas,
        )
        return {
            "summary": result.summary,
            "confidence": result.confidence,
            "verified": result.verified,
            "added_cards": result.added_in_b,
            "removed_cards": result.removed_in_b,
            "score_deltas": dict(result.score_deltas),
            "role_deltas": dict(result.role_deltas),
            "power_gap": result.power_gap,
        }

    @_safe
    def explain_card_in_deck(
        self,
        decklist_text: str,
        card_name: str,
        format_: str | None = None,
        name: str = "Deck",
    ) -> dict:
        """Explain why one named card was flagged in this deck.

        Pulls the rule-engine flags for the named card from castability +
        cuts ranking + advanced analysis, then runs the explain-card
        prompt against the coach backend. The frontend's "Why is this
        card flagged?" link from the unreliable-cards table calls this.
        """
        deck = self._build_deck(decklist_text, format_, name)
        if isinstance(deck, dict):
            return deck

        # Find the entry for the requested card.
        entry = next((e for e in deck.entries if e.card and e.card.name.lower() == card_name.lower()), None)
        if entry is None or entry.card is None:
            return {"ok": False, "error": f"Card '{card_name}' not in deck."}

        result = run_static_analysis(deck)
        castability = analyze_castability(deck, result.color_sources)
        # Compute rule-engine flags for this card.
        flags: list[str] = []
        on_curve = None
        bottleneck = None
        for c in castability.unreliable_cards:
            if c.name.lower() == card_name.lower():
                on_curve = float(c.on_curve_probability)
                bottleneck = c.bottleneck_color or None
                flags.append(
                    f"unreliable on curve (P={on_curve:.2f})"
                    + (f"; bottleneck color {bottleneck}" if bottleneck else "")
                )
                break

        # Cut-candidate ranker — pull the same signals here that the
        # cuts pass would produce.
        from densa_deck.analyst.candidates import rank_cut_candidates
        for cand in rank_cut_candidates(deck, limit=20):
            if cand.entry.card and cand.entry.card.name.lower() == card_name.lower():
                flags.extend(cand.reasons)
                break

        deck_colors = sorted({
            c.value for e in deck.entries if e.card for c in e.card.color_identity
        })
        from densa_deck.analyst.phase6 import explain_card
        backend = self._get_coach_backend()
        result_obj = explain_card(
            backend=backend,
            card_name=entry.card.name,
            mana_cost=entry.card.mana_cost or "",
            cmc=float(entry.card.cmc or 0.0),
            deck_name=deck.name,
            deck_colors=deck_colors,
            color_sources=dict(result.color_sources),
            on_curve_prob=on_curve,
            bottleneck_color=bottleneck,
            flags=flags,
            role_tags=[t.value for t in (entry.card.tags or [])],
        )
        return {
            "card_name": result_obj.card_name,
            "summary": result_obj.summary,
            "confidence": result_obj.confidence,
            "verified": result_obj.verified,
            "flags": result_obj.flags,
            "on_curve_prob": result_obj.on_curve_prob,
            "bottleneck_color": result_obj.bottleneck_color,
        }

    @_safe
    def build_rule0_worksheet(
        self,
        decklist_text: str,
        format_: str | None = None,
        name: str = "Deck",
        include_combos: bool = True,
    ) -> dict:
        """Assemble + render a Rule 0 pre-game worksheet.

        Pure rule-engine output — no LLM call needed (the worksheet
        narrates structured data deterministically). When `include_combos`
        is True and the combo cache is populated, surfaces detected
        combo lines on the worksheet too.
        """
        deck = self._build_deck(decklist_text, format_, name)
        if isinstance(deck, dict):
            return deck

        from densa_deck.analysis.power_level import estimate_power_level
        from densa_deck.analyst.phase6 import build_rule0_worksheet as _build, render_rule0_text
        from densa_deck.formats.profiles import detect_archetype

        analysis = run_static_analysis(deck)
        power = estimate_power_level(deck)
        archetype = detect_archetype(deck)
        color_identity = sorted({
            c.value for e in deck.entries if e.card for c in e.card.color_identity
        })

        combo_lines: list[str] = []
        if include_combos:
            store = self._get_combo_store()
            if store.combo_count() > 0:
                from densa_deck.combos import detect_combos
                deck_card_names = [e.card.name for e in deck.entries if e.card]
                matches = detect_combos(
                    store=store,
                    deck_card_names=deck_card_names,
                    deck_color_identity=color_identity,
                    limit=5,
                )
                combo_lines = [m.combo.short_label() for m in matches]

        notable_cards = [e.card.name for e in deck.entries
                         if e.card and "Legendary" in (e.card.type_line or "")][:6]

        ws = _build(
            deck_name=deck.name,
            archetype=archetype.value if hasattr(archetype, "value") else str(archetype),
            color_identity=color_identity,
            power=power,
            analysis=analysis,
            goldfish_report=None,
            combo_lines=combo_lines,
            notable_cards=notable_cards,
        )
        return {
            "deck_name": ws.deck_name,
            "archetype": ws.archetype,
            "color_identity": ws.color_identity,
            "power_overall": ws.power_overall,
            "power_tier": ws.power_tier,
            "bracket": ws.bracket,
            "avg_kill_turn": ws.avg_kill_turn,
            "fastest_kill_turn": ws.fastest_kill_turn,
            "interaction_count": ws.interaction_count,
            "interaction_density": ws.interaction_density,
            "combo_lines": ws.combo_lines,
            "notable_cards": ws.notable_cards,
            "pre_game_notes": ws.pre_game_notes,
            "land_count": ws.land_count,
            "ramp_count": ws.ramp_count,
            "draw_count": ws.draw_count,
            "rendered_text": render_rule0_text(ws),
        }

    @_safe
    def save_builder_as_deck(
        self,
        deck_id: str,
        name: str,
        format_: str,
        decklist_text: str,
        notes: str = "",
    ) -> dict:
        """Pro-gated save — wraps `save_deck_version` with an explicit tier
        check so free-tier users get a clear "upgrade to save" envelope
        rather than the underlying analyze-flow error.

        The frontend catches `error_type == "ProRequired"` to surface the
        upgrade modal; pro users land directly in `save_deck_version`,
        which parses + resolves + snapshots the deck into the same
        VersionStore as the Analyze tab's Save button.
        """
        if get_user_tier() != Tier.PRO:
            return {
                "ok": False,
                "error": "Saving decks requires Densa Deck Pro. "
                         "Your draft is preserved — activate Pro on the Settings tab to save it as a tracked deck.",
                "error_type": "ProRequired",
            }
        return self.save_deck_version(
            deck_id=deck_id, name=name,
            decklist_text=decklist_text, format_=format_, notes=notes,
        )

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

        # Detect combos against the local cache (when populated). The
        # coach session sheet surfaces combo lines so the coach can
        # narrate the deck's win plan accurately, and we feed the same
        # data into estimate_power_level + detect_archetype so the
        # power/archetype shown in the sheet reflects combo reality.
        combo_lines: list[str] = []
        detected_combo_count = 0
        try:
            store = self._get_combo_store()
            if store.combo_count() > 0:
                from densa_deck.combos import detect_combos
                deck_card_names_for_combo = [e.card.name for e in deck.entries if e.card]
                deck_color_identity_for_combo = sorted({
                    c.value for e in deck.entries if e.card for c in e.card.color_identity
                })
                matches = detect_combos(
                    store=store,
                    deck_card_names=deck_card_names_for_combo,
                    deck_color_identity=deck_color_identity_for_combo,
                    limit=8,
                )
                combo_lines = [m.combo.short_label() for m in matches]
                detected_combo_count = len(matches)
        except Exception:
            # Non-fatal — coach starts without combo context if anything
            # in the cache lookup blows up.
            pass

        result = _analyze(deck)
        power = estimate_power_level(deck, detected_combo_count=detected_combo_count)
        archetype = detect_archetype(deck, detected_combo_count=detected_combo_count)

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
            combo_lines=combo_lines,
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
        """Pick the LLM backend lazily. Prefers the real LlamaCppBackend
        when llama-cpp-python is importable AND the GGUF file is on disk;
        falls back to the MockBackend otherwise so the Coach tab still
        renders for users who haven't downloaded a model yet.

        Important: the cached backend can be INVALIDATED by
        refresh_coach_backend() so a mid-session model download (or an
        installer that arrived with the model already on disk) is picked
        up on the next coach call without requiring an app restart.

        The `MTG_ANALYST_BACKEND` env var is still honored for explicit
        "force mock" (useful in CI / tests) — set it to "mock" to bypass
        the real backend selection entirely.
        """
        if self._coach_backend is not None:
            return self._coach_backend
        with self._backend_lock:
            if self._coach_backend is not None:
                return self._coach_backend
            import os
            forced = os.environ.get("MTG_ANALYST_BACKEND", "").lower().strip()
            # Anything other than "mock" / "" falls through to the real
            # backend. Historically this env var defaulted to "mock" which
            # meant the real model was never used unless the user
            # explicitly opted in — that was the v0.1.5 "no model
            # attached" bug. New default is: real-if-available, else mock.
            if forced != "mock":
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
    def refresh_coach_backend(self) -> dict:
        """Invalidate the cached coach backend so the next coach call
        re-probes the model file + llama_cpp availability.

        Called by the frontend after `analyst_pull_start` completes AND
        on app launch after an in-place installer update, so the backend
        picks up a newly-downloaded or newly-bundled model without
        requiring a full app restart.

        Returns the status of whatever backend gets selected on the
        next _get_coach_backend call.
        """
        with self._backend_lock:
            self._coach_backend = None
        # Re-select immediately and report which backend won so the UI
        # can show "Real analyst active" vs "Mock placeholder active".
        backend = self._get_coach_backend()
        from densa_deck.analyst.backends.llama_cpp import LlamaCppBackend
        is_real = isinstance(backend, LlamaCppBackend)
        return {
            "backend": type(backend).__name__,
            "is_real": is_real,
        }

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
                download_bulk_file, fetch_bulk_data_manifest, load_bulk_file,
            )

            cache_dir = db.db_path.parent / "bulk"
            cache_dir.mkdir(parents=True, exist_ok=True)
            dest = cache_dir / "oracle_cards.json"

            loop = asyncio.new_event_loop()
            try:
                # Snapshot the pre-ingest card set so the frontend's
                # "what changed" diff modal can report added / removed /
                # updated oracle_ids after the ingest completes. Empty on
                # first-run (existing == 0), in which case the UI skips
                # the diff view entirely.
                pre_snapshot: dict[str, str] = {}
                if existing > 0:
                    pre_snapshot = db.snapshot_oracle_identities()

                # Phase 1: resolve the bulk-data manifest (tiny HTTP call).
                # We fetch the full manifest entry, not just the URL, so we
                # can record the Scryfall `updated_at` timestamp in metadata
                # — that's what future update-check comparisons key off.
                manifest = loop.run_until_complete(fetch_bulk_data_manifest())
                url = manifest["download_uri"]
                remote_updated_at = manifest.get("updated_at", "")
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
                # Bulk manifest timestamp — what the "update available"
                # check compares against so we don't re-ingest just because
                # a day passed; we only pull when Scryfall itself has a
                # newer build.
                if remote_updated_at:
                    db.set_metadata("scryfall_bulk_updated_at", remote_updated_at)
                # Local ingest completion timestamp — for UI display ("last
                # synced 3 days ago") and for the "check at most once per
                # 24h even if auto-check is on" throttle.
                db.set_metadata("last_ingest_completed_at", _now_iso())

                # Capture the post-ingest snapshot and stash the diff on
                # the progress dict so the frontend can fetch it once
                # it sees done=True. Skip entirely for first-run ingests.
                # Held under _progress_lock because get_last_ingest_diff
                # reads + clears this field from the dispatcher thread.
                if pre_snapshot:
                    post_snapshot = db.snapshot_oracle_identities()
                    new_diff = _compute_card_db_diff(
                        pre_snapshot, post_snapshot,
                    )
                else:
                    new_diff = None
                with self._progress_lock:
                    self._last_ingest_diff = new_diff

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
        include_combos: bool = True,
    ) -> dict:
        """Run goldfish simulation. Sync — typically 3-15s for 1000 sims.

        Pro-gated at the UI layer; the API runs if called. Lower default sim
        count than CLI (1000 vs. 10000) because the GUI cares about latency
        over tight error bars — users can re-run with a higher count via the
        sims param if they want.

        When include_combos is True (default) and the user has refreshed
        their Commander Spellbook combo cache, the goldfish run also tracks
        combo-assembly turns and reports combo_win_rate / average_combo_turn /
        combo_win_turn_distribution alongside the damage stats. Pass False
        to skip combo evaluation (saves a few ms per game on 30k-row decks
        when the user only cares about the damage clock).
        """
        from densa_deck.goldfish.runner import run_goldfish_batch
        deck = self._build_deck(decklist_text, format_, name)
        if isinstance(deck, dict):  # error envelope
            return deck
        combos = self._fetch_relevant_combos(deck) if include_combos else []
        report = run_goldfish_batch(deck, simulations=sims, seed=seed, combos=combos)
        return _goldfish_to_dict(report)

    @_safe
    def run_gauntlet(
        self, decklist_text: str, format_: str | None = None,
        name: str = "Deck", sims: int = 200, seed: int | None = None,
        include_combos: bool = True,
    ) -> dict:
        """Run matchup gauntlet against 11 archetypes. Sync — typically
        30-60s total (200 sims × 11 archetypes).

        When include_combos is True (default) and the user has refreshed
        their Commander Spellbook combo cache, every archetype matchup
        also tracks combo-as-win-condition. The serializer surfaces
        per-matchup wins_by_combo / combo_win_rate / avg_combo_win_turn
        and the gauntlet-wide combo_win_rate_overall + top_combo_lines_overall.
        """
        from densa_deck.matchup.gauntlet import run_gauntlet as _run
        deck = self._build_deck(decklist_text, format_, name)
        if isinstance(deck, dict):
            return deck
        combos = self._fetch_relevant_combos(deck) if include_combos else []
        report = _run(deck, simulations=sims, seed=seed, combos=combos)
        return _gauntlet_to_dict(report)

    def _fetch_relevant_combos(self, deck) -> list:
        """Pull deck-relevant combos from the local cache, if populated.

        Mirrors the goldfish path so gauntlet / duel can share the
        narrowing — only combos with at least one card in the deck are
        worth checking. Empty list when the cache hasn't been refreshed.
        """
        store = self._get_combo_store()
        if store.combo_count() == 0:
            return []
        seen_ids: set[str] = set()
        out: list = []
        deck_card_names = {e.card.name for e in deck.entries if e.card}
        for n in deck_card_names:
            for cid in store.lookup_combos_for_card(n):
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                c = store.get_combo(cid)
                if c is not None:
                    out.append(c)
        return out

    @_safe
    def duel_decks(
        self, deck_a_id: str, deck_b_id: str,
        sims: int = 100, seed: int | None = None,
    ) -> dict:
        """Deck-vs-deck duel: pit two saved decks against each other.

        Reuses the archetype matchup engine by deriving a virtual
        ArchetypeProfile for each deck from its static analysis (role
        counts + power sub-scores), then runs the sim twice — once with
        each deck as the "hero" side — and reports both perspectives.

        Returns an error dict if either deck can't be resolved; otherwise
        a structured report the frontend renders with win rate, avg kill
        turn, and per-axis deltas so the user can see "deck A wins on
        speed, deck B wins on interaction" at a glance.
        """
        from densa_deck.analysis.static import analyze_deck as static_analyze
        from densa_deck.analysis.power_level import estimate_power_level
        from densa_deck.formats.profiles import detect_archetype
        from densa_deck.matchup.deck_as_opponent import deck_to_profile
        from densa_deck.matchup.simulator import simulate_matchup

        def _prepare(deck_id: str):
            snap = self._get_vstore().get_latest(deck_id)
            if snap is None:
                return {"ok": False, "error": f"No saved versions for deck '{deck_id}'."}
            deck = self._build_deck(
                _snapshot_to_text(snap),
                snap.format,
                snap.name or deck_id,
            )
            if isinstance(deck, dict):
                return deck
            result = static_analyze(deck)
            power = estimate_power_level(deck)
            label = detect_archetype(deck)
            archetype_str = label.value if hasattr(label, "value") else str(label)
            profile = deck_to_profile(
                deck=deck,
                analysis=result,
                power=power,
                archetype_label=archetype_str,
                display_name=snap.name or deck_id,
            )
            return {
                "deck": deck, "analysis": result, "power": power,
                "archetype": archetype_str, "profile": profile,
                "snapshot": snap,
            }

        a_ctx = _prepare(deck_a_id)
        if isinstance(a_ctx, dict) and "ok" in a_ctx and a_ctx["ok"] is False:
            return a_ctx
        b_ctx = _prepare(deck_b_id)
        if isinstance(b_ctx, dict) and "ok" in b_ctx and b_ctx["ok"] is False:
            return b_ctx

        # Clamp sims to a reasonable band — 20 min, 1000 max — so a
        # malformed frontend call can't burn CPU time for 10 minutes.
        sims = max(20, min(1000, int(sims or 100)))

        # Run both perspectives so the result is symmetric: A-sees-B and
        # B-sees-A using the same sim engine. Win-rate + kill-turn come
        # from each perspective directly. Combo lists are per-deck so
        # each side gets its own — A's combos applied to A's games,
        # B's combos applied to B's games.
        a_combos = self._fetch_relevant_combos(a_ctx["deck"])
        b_combos = self._fetch_relevant_combos(b_ctx["deck"])
        a_vs_b = simulate_matchup(
            a_ctx["deck"], b_ctx["profile"], simulations=sims, seed=seed,
            combos=a_combos,
        )
        b_vs_a = simulate_matchup(
            b_ctx["deck"], a_ctx["profile"], simulations=sims,
            seed=(seed + 1) if seed is not None else None,
            combos=b_combos,
        )

        def _side_summary(ctx, result):
            return {
                "deck_id": ctx["snapshot"].deck_id,
                "name": ctx["snapshot"].name or ctx["snapshot"].deck_id,
                "archetype": ctx["archetype"],
                "power": {
                    "overall": round(float(ctx["power"].overall), 2),
                    "tier": ctx["power"].tier,
                    "speed": round(float(ctx["power"].speed), 1),
                    "interaction": round(float(ctx["power"].interaction), 1),
                    "combo_potential": round(float(ctx["power"].combo_potential), 1),
                    "mana_efficiency": round(float(ctx["power"].mana_efficiency), 1),
                    "win_condition_quality": round(float(ctx["power"].win_condition_quality), 1),
                    "card_quality": round(float(ctx["power"].card_quality), 1),
                },
                "wins": int(result.wins),
                "losses": int(result.losses),
                "win_rate": round(float(result.win_rate) * 100.0, 1),
                "avg_turns": round(float(result.avg_turns), 2),
                "avg_damage_dealt": round(float(result.avg_our_damage), 2),
                "avg_damage_taken": round(float(result.avg_opponent_damage), 2),
                "avg_permanents_removed": round(float(result.avg_permanents_removed), 2),
                "wins_by_damage": int(result.wins_by_damage),
                "wins_by_combo": int(getattr(result, "wins_by_combo", 0) or 0),
                "losses_by_clock": int(result.losses_by_clock),
                "losses_by_timeout": int(result.losses_by_timeout),
                "combos_evaluated": int(getattr(result, "combos_evaluated", 0) or 0),
                "combo_win_rate": round(float(getattr(result, "combo_win_rate", 0.0) or 0.0) * 100.0, 1),
                "avg_combo_win_turn": round(float(getattr(result, "avg_combo_win_turn", 0.0) or 0.0), 2),
                "top_combo_lines": [list(p) for p in getattr(result, "top_combo_lines", [])],
            }

        return {
            "simulations": sims,
            "a_vs_b": _side_summary(a_ctx, a_vs_b),
            "b_vs_a": _side_summary(b_ctx, b_vs_a),
            # Head-to-head winner: favour whichever side took a higher
            # win-rate across their perspective. Ties (within 2 pp) are
            # explicitly marked so the UI can show "roughly even".
            "verdict": _duel_verdict(a_vs_b, b_vs_a, a_ctx, b_ctx),
        }

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


def _compute_card_db_diff(
    pre: dict[str, str], post: dict[str, str], top_n: int = 500,
) -> dict:
    """Diff two snapshots of oracle_id -> identity_hash and summarize.

    Called after an update-path ingest (i.e. when there was an existing
    card DB before the re-ingest). Returns a dict the frontend can
    render as a collapsible "what changed" modal:

      {
        "added": [name, ...],    # oracle_ids in post but not pre
        "removed": [name, ...],  # in pre but not post — rare (ban/errata)
        "updated": [name, ...],  # in both but the identity hash changed
        "counts": {"added": N, "removed": N, "updated": N},
        "truncated": bool,       # list trimmed to top_n for render speed
      }

    Names are derived from the identity-hash's first line (which is the
    card name — see CardDatabase.snapshot_oracle_identities).
    """
    def _name_of(identity: str) -> str:
        return identity.split("\n", 1)[0] if identity else ""

    pre_ids = set(pre.keys())
    post_ids = set(post.keys())
    added_ids = sorted(post_ids - pre_ids, key=lambda i: _name_of(post[i]))
    removed_ids = sorted(pre_ids - post_ids, key=lambda i: _name_of(pre[i]))
    shared = pre_ids & post_ids
    updated_ids = sorted(
        (oid for oid in shared if pre[oid] != post[oid]),
        key=lambda i: _name_of(post[i]),
    )

    def _trim(ids, source):
        return [_name_of(source[i]) for i in ids[:top_n]]

    counts = {"added": len(added_ids), "removed": len(removed_ids), "updated": len(updated_ids)}
    truncated = any(c > top_n for c in counts.values())
    return {
        "added": _trim(added_ids, post),
        "removed": _trim(removed_ids, pre),
        "updated": _trim(updated_ids, post),
        "counts": counts,
        "truncated": truncated,
        "top_n": top_n,
    }


def _duel_verdict(a_vs_b, b_vs_a, a_ctx: dict, b_ctx: dict) -> dict:
    """Pick a winner from the two-perspective duel sim.

    A "true" duel would play both decks against each other in a single
    game, but the matchup engine is deck-vs-profile; we instead run two
    independent sims (A as hero vs B as profile, then B as hero vs A as
    profile) and use both win rates as corroborating evidence. Ties
    within 2 percentage points are marked "roughly even" so users don't
    over-interpret statistical noise.
    """
    a_wr = float(a_vs_b.win_rate) * 100.0
    b_wr = float(b_vs_a.win_rate) * 100.0
    delta = a_wr - b_wr
    if abs(delta) < 2.0:
        winner = "even"
        headline = "Roughly even — within 2pp margin of error"
    elif delta > 0:
        winner = "a"
        headline = f"{a_ctx['snapshot'].name or a_ctx['snapshot'].deck_id} wins by {abs(delta):.1f}pp"
    else:
        winner = "b"
        headline = f"{b_ctx['snapshot'].name or b_ctx['snapshot'].deck_id} wins by {abs(delta):.1f}pp"
    # Per-axis deltas (power sub-scores) give users an at-a-glance sense
    # of where the gap lives: "B is faster, A has more interaction", etc.
    def _delta(axis: str):
        return round(float(getattr(a_ctx["power"], axis) - getattr(b_ctx["power"], axis)), 1)
    return {
        "winner": winner,
        "headline": headline,
        "a_win_rate": round(a_wr, 1),
        "b_win_rate": round(b_wr, 1),
        "axis_deltas": {
            "speed": _delta("speed"),
            "interaction": _delta("interaction"),
            "combo_potential": _delta("combo_potential"),
            "mana_efficiency": _delta("mana_efficiency"),
            "win_condition_quality": _delta("win_condition_quality"),
            "card_quality": _delta("card_quality"),
        },
    }


def _image_url_safe(scryfall_id: str) -> str:
    try:
        from densa_deck.legal import scryfall_image_url
        return scryfall_image_url(scryfall_id)
    except Exception:
        return ""


# =============================================================================
# Multi-format deck export — MTGO / MTGA / Moxfield-paste
# =============================================================================


def _export_mtga(deck) -> tuple[str, str]:
    """MTGA paste format: lines like `4 Lightning Bolt`. Commander zone
    is emitted as a leading "Commander" section if present, mirroring
    MTGA's import expectations.
    """
    from densa_deck.models import Zone
    lines: list[str] = []
    zones_in_order = [
        ("Commander", Zone.COMMANDER),
        ("Deck", Zone.MAINBOARD),
        ("Sideboard", Zone.SIDEBOARD),
    ]
    for label, zone in zones_in_order:
        entries = [e for e in deck.entries if e.zone == zone and e.card]
        if not entries:
            continue
        lines.append(label)
        # Aggregate quantities by name in case the parser emitted multiple rows.
        agg: dict[str, int] = {}
        for e in entries:
            agg[e.card.name] = agg.get(e.card.name, 0) + e.quantity
        for name, qty in sorted(agg.items()):
            lines.append(f"{qty} {name}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", f"{_safe_filename(deck.name)}.txt"


def _export_moxfield_text(deck) -> tuple[str, str]:
    """Moxfield's import-by-paste format. Same as MTGA in shape but
    section headers use Moxfield's vocabulary (`Commander`, `Deck`,
    `Sideboard`). Moxfield is permissive about format so this is
    effectively the same body.
    """
    return _export_mtga(deck)


def _export_mtgo(deck) -> tuple[str, str]:
    """MTGO `.dek` file format. Documented at https://mtgo.com — the
    XML schema accepts `<Cards>` entries with `Number` (qty), `Sideboard`
    (bool), and `Name` attributes. Keeps it minimal — no card IDs, just
    names.
    """
    import xml.sax.saxutils as _sax
    from densa_deck.models import Zone
    lines: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>', "<Deck>"]
    lines.append(f"  <NetDeckID>0</NetDeckID>")
    lines.append(f"  <PreconstructedDeckID>0</PreconstructedDeckID>")

    def _emit(zone, sideboard_flag: str):
        agg: dict[str, int] = {}
        for e in deck.entries:
            if e.zone != zone or e.card is None:
                continue
            agg[e.card.name] = agg.get(e.card.name, 0) + e.quantity
        for name, qty in sorted(agg.items()):
            esc = _sax.escape(name)
            lines.append(
                f'  <Cards CatID="0" Quantity="{qty}" Sideboard="{sideboard_flag}" '
                f'Name="{esc}" Annotation="0" />'
            )

    _emit(Zone.COMMANDER, "true")  # MTGO treats Commander as a separate zone but the .dek format doesn't have a column for it; flag as sideboard so the user knows it's special
    _emit(Zone.MAINBOARD, "false")
    _emit(Zone.SIDEBOARD, "true")
    lines.append("</Deck>")
    return "\n".join(lines) + "\n", f"{_safe_filename(deck.name)}.dek"


def _safe_filename(name: str) -> str:
    """Slugify a deck name for export filenames — drops everything that
    isn't alnum / dash / underscore so we don't hand the user a file
    name Windows refuses to save."""
    import re
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", (name or "deck").strip()).strip("-")
    return cleaned or "deck"


def _near_miss_to_dict(near) -> dict:
    """Flatten a NearMissCombo for the desktop bridge.

    Keeps the same wire shape as a regular MatchedCombo plus a
    `missing_cards` list so the UI can render "you need: <card list>"
    next to each near-miss row.
    """
    c = near.combo
    return {
        "combo_id": c.combo_id,
        "cards": list(c.cards),
        "templates": list(c.templates),
        "produces": list(c.produces),
        "color_identity": c.color_identity,
        "bracket_tag": c.bracket_tag,
        "description": c.description,
        "popularity": c.popularity,
        "spellbook_url": c.spellbook_url,
        "short_label": c.short_label(),
        "in_deck_cards": list(near.in_deck_cards),
        "missing_cards": list(near.missing_cards),
        "missing_count": near.missing_count,
        "unsatisfied_templates": near.unsatisfied_templates,
    }


def _combo_to_dict(matched) -> dict:
    """Flatten a MatchedCombo for the desktop bridge."""
    c = matched.combo
    return {
        "combo_id": c.combo_id,
        "cards": list(c.cards),
        "templates": list(c.templates),
        "produces": list(c.produces),
        "color_identity": c.color_identity,
        "bracket_tag": c.bracket_tag,
        "description": c.description,
        "popularity": c.popularity,
        "spellbook_url": c.spellbook_url,
        "short_label": c.short_label(),
        "in_deck_cards": list(matched.in_deck_cards),
        "unsatisfied_templates": matched.unsatisfied_templates,
    }


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _user_prefs_path() -> Path:
    """Resolve the preferences file path.

    Shared with `densa_deck.tiers._CONFIG_PATH` — both read/write the same
    file. Kept as a separate helper so test monkey-patches can redirect
    just the preference-write path without stomping on the tier-saving
    import in tiers.py.
    """
    from densa_deck.tiers import _CONFIG_PATH
    return _CONFIG_PATH


def _load_user_prefs() -> dict:
    """Read the preferences JSON. Missing / malformed file returns {} so a
    corrupt config doesn't block app launch — callers re-apply defaults
    on top of whatever comes back.
    """
    path = _user_prefs_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_user_prefs(prefs: dict) -> None:
    """Write preferences atomically so a crash mid-write can't leave the
    next launch with a half-parsed config."""
    _atomic_write_json(_user_prefs_path(), prefs)


def _builder_draft_path() -> Path:
    """Single-slot draft file for the Build tab. Lives alongside the card
    database so all user data is in one ~/.densa-deck/ directory."""
    from densa_deck.data.database import DEFAULT_DB_PATH
    return DEFAULT_DB_PATH.parent / "drafts.json"


def _card_to_builder_dict(card, include_full: bool = False) -> dict:
    """Flatten a Card for the builder's JSON wire format.

    Trims aggressively by default — search results return dozens of
    cards at once and every kilobyte over the pywebview bridge slows
    the UI. `include_full=True` is used by `get_card` (single-card
    lookup) to surface full oracle text + faces.
    """
    def _image_url(scryfall_id: str) -> str:
        # Scryfall hotlink pattern — identical to the one CardDatabase
        # serializes into card.image_url / legal.scryfall_image_url.
        try:
            from densa_deck.legal import scryfall_image_url
            return scryfall_image_url(scryfall_id)
        except Exception:
            return ""

    out = {
        "name": card.name,
        "scryfall_id": card.scryfall_id,
        "oracle_id": card.oracle_id,
        "cmc": card.cmc,
        "mana_cost": card.mana_cost,
        "type_line": card.type_line,
        "colors": [c.value for c in card.colors],
        "color_identity": [c.value for c in card.color_identity],
        "rarity": card.rarity,
        "set_code": card.set_code,
        "price_usd": card.price_usd,
        "image_url": _image_url(card.scryfall_id),
        "is_land": card.is_land,
        "is_creature": card.is_creature,
        "is_instant": card.is_instant,
        "is_sorcery": card.is_sorcery,
        "is_artifact": card.is_artifact,
        "is_enchantment": card.is_enchantment,
        "is_planeswalker": card.is_planeswalker,
        "is_battle": card.is_battle,
    }
    if include_full:
        out.update({
            "oracle_text": card.oracle_text,
            "power": card.power,
            "toughness": card.toughness,
            "loyalty": card.loyalty,
            "keywords": list(card.keywords),
            "faces": [
                {
                    "name": f.name, "mana_cost": f.mana_cost, "cmc": f.cmc,
                    "type_line": f.type_line, "oracle_text": f.oracle_text,
                    "power": f.power, "toughness": f.toughness, "loyalty": f.loyalty,
                }
                for f in card.faces
            ],
        })
    return out


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
        # Combo-win fields (zero/empty when no combos were tracked or when
        # the cache wasn't populated). The frontend should only render the
        # combo section when combos_evaluated > 0.
        "combos_evaluated": getattr(report, "combos_evaluated", 0),
        "combo_win_rate": getattr(report, "combo_win_rate", 0.0),
        "average_combo_win_turn": getattr(report, "average_combo_win_turn", 0.0),
        "combo_win_turn_distribution": {
            str(k): v for k, v in getattr(report, "combo_win_turn_distribution", {}).items()
        },
        "top_combo_lines": [list(pair) for pair in getattr(report, "top_combo_lines", [])],
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
        # Combo aggregates — zero/empty when no combos were tracked.
        "combos_evaluated": getattr(report, "combos_evaluated", 0),
        "combo_win_rate_overall": getattr(report, "combo_win_rate_overall", 0.0),
        "avg_combo_win_turn_overall": getattr(report, "avg_combo_win_turn_overall", 0.0),
        "top_combo_lines_overall": [
            list(p) for p in getattr(report, "top_combo_lines_overall", [])
        ],
        "matchups": [
            {
                "archetype": m.archetype_name,
                "wins": m.wins,
                "losses": m.losses,
                "simulations": m.simulations,
                "win_rate": m.win_rate,
                "avg_turns": m.avg_turns,
                "wins_by_damage": int(getattr(m, "wins_by_damage", 0) or 0),
                "wins_by_combo": int(getattr(m, "wins_by_combo", 0) or 0),
                "combos_evaluated": int(getattr(m, "combos_evaluated", 0) or 0),
                "combo_win_rate": float(getattr(m, "combo_win_rate", 0.0) or 0.0),
                "avg_combo_win_turn": float(getattr(m, "avg_combo_win_turn", 0.0) or 0.0),
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
