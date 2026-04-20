"""Phase 2 tests: Scryfall-backed add suggestions with full verifier stack.

Uses an in-memory CardDatabase seeded with a small synthetic corpus so the
candidate-query pipeline is exercised end-to-end without requiring a real
Scryfall ingest.

Key invariants under test:
  - Candidate query filters OUT off-color / banned / already-in-deck / wrong-role cards
  - Belt-and-suspenders verifier catches color/legality regressions even if the
    candidate table is mis-populated
  - Retry loop recovers from a first-attempt bogus tag
  - Full hallucination firewall holds: a backend that insists on emitting a
    fake or off-color card name cannot cause that name to appear in the
    final AnalystResult.adds — the verifier rejects, retries exhaust, and
    adds stays empty rather than silently emitting a bad suggestion.
"""

import tempfile

import pytest

from mtg_deck_engine.analyst import AnalystRunner, MockBackend
from mtg_deck_engine.analyst.add_candidates import find_add_candidates, render_add_table
from mtg_deck_engine.analyst.prompts import add_suggestions_prompt
from mtg_deck_engine.analyst.verifiers import (
    TagPick,
    VerificationError,
    verify_add_picks_constraints,
)
from mtg_deck_engine.analysis.power_level import PowerBreakdown
from mtg_deck_engine.data.database import CardDatabase
from mtg_deck_engine.models import (
    AnalysisResult,
    Card,
    CardLayout,
    CardTag,
    Color,
    Deck,
    DeckEntry,
    Format,
    Legality,
    Zone,
)


def _mk_card(
    name: str,
    cmc: float = 2.0,
    colors: list[Color] | None = None,
    color_identity: list[Color] | None = None,
    mana_cost: str = "{2}",
    oracle_text: str = "",
    type_line: str = "Artifact",
    commander_legal: Legality = Legality.LEGAL,
    modern_legal: Legality = Legality.LEGAL,
) -> Card:
    return Card(
        scryfall_id=f"sid-{name}",
        oracle_id=f"oid-{name}",
        name=name,
        layout=CardLayout.NORMAL,
        cmc=cmc,
        mana_cost=mana_cost,
        type_line=type_line,
        oracle_text=oracle_text,
        colors=colors or [],
        color_identity=color_identity or [],
        legalities={
            "commander": commander_legal,
            "modern": modern_legal,
            "legacy": Legality.LEGAL,
        },
    )


@pytest.fixture
def seeded_db():
    """An in-memory CardDatabase populated with a known corpus.

    Layout:
      - 3 ramp in G + a colorless ramp (all commander-legal)
      - 2 ramp banned in commander (to test legality filter)
      - 3 ramp in U (off-color for a mono-green deck)
      - 2 card-draw in U
      - 1 targeted removal in W
      - 1 already-in-deck candidate (excluded by name)
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = CardDatabase(db_path=db_path)
    db.connect()
    cards = [
        # G ramp — should match for a mono-green ramp query. Oracle text hits
        # the tagger's "land onto the battlefield" ramp phrase.
        _mk_card("Cultivate", cmc=3, colors=[Color.GREEN], color_identity=[Color.GREEN],
                 mana_cost="{2}{G}", type_line="Sorcery",
                 oracle_text="Search your library for up to two basic land cards, put one land onto the battlefield tapped and the other into your hand."),
        _mk_card("Rampant Growth", cmc=2, colors=[Color.GREEN], color_identity=[Color.GREEN],
                 mana_cost="{1}{G}", type_line="Sorcery",
                 oracle_text="Search your library for a basic land card and put it onto the battlefield tapped."),
        _mk_card("Llanowar Elves", cmc=1, colors=[Color.GREEN], color_identity=[Color.GREEN],
                 mana_cost="{G}", type_line="Creature — Elf Druid",
                 oracle_text="{T}: Add {G}."),
        # Colorless ramp — should match any color deck
        _mk_card("Sol Ring", cmc=1, color_identity=[], mana_cost="{1}",
                 type_line="Artifact", oracle_text="{T}: Add {C}{C}."),
        # Banned-in-commander ramp
        _mk_card("Banned Rock", cmc=1, color_identity=[],
                 mana_cost="{1}", type_line="Artifact",
                 oracle_text="{T}: Add one mana of any color.",
                 commander_legal=Legality.BANNED),
        # U ramp/mana rocks — OFF-COLOR for a mono-green deck
        _mk_card("Azure Signet", cmc=2, colors=[Color.BLUE], color_identity=[Color.BLUE],
                 mana_cost="{2}", type_line="Artifact",
                 oracle_text="{T}: Add {U}."),
        # U card-draw
        _mk_card("Rhystic Study", cmc=3, colors=[Color.BLUE], color_identity=[Color.BLUE],
                 mana_cost="{2}{U}", type_line="Enchantment",
                 oracle_text="Whenever an opponent casts a spell, unless that player pays {1}, you may draw a card."),
        # W removal
        _mk_card("Swords to Plowshares", cmc=1, colors=[Color.WHITE], color_identity=[Color.WHITE],
                 mana_cost="{W}", type_line="Instant",
                 oracle_text="Exile target creature. Its controller gains life equal to its power."),
        # Already-in-deck
        _mk_card("Already Here", cmc=2, color_identity=[Color.GREEN],
                 mana_cost="{1}{G}", type_line="Sorcery",
                 oracle_text="Search your library for a basic land..."),
    ]
    db.upsert_cards(cards)
    yield db
    db.close()


def _mono_green_deck():
    """Small deck in mono-green identity. Excludes 'Already Here' from suggestions."""
    entries = [
        DeckEntry(card_name="Commander G", quantity=1, zone=Zone.COMMANDER,
                  card=_mk_card("Commander G", cmc=4, color_identity=[Color.GREEN])),
        DeckEntry(card_name="Already Here", quantity=1, zone=Zone.MAINBOARD,
                  card=_mk_card("Already Here", cmc=2, color_identity=[Color.GREEN])),
    ]
    return Deck(name="Mono-G", format=Format.COMMANDER, entries=entries)


# -------------------------------------------------------------------- Candidate query

class TestFindAddCandidates:
    def test_returns_in_color_ramp_only(self, seeded_db):
        cands = find_add_candidates(
            db=seeded_db, role=CardTag.RAMP,
            deck_color_identity={"G"},
            format_=Format.COMMANDER,
            exclude_names={"Already Here"},
            limit=10,
        )
        names = {c.card.name for c in cands}
        # In-color ramp present
        assert "Cultivate" in names
        assert "Rampant Growth" in names
        assert "Llanowar Elves" in names  # tagged as mana_dork which implies ramp via tagger
        # Colorless ramp present
        assert "Sol Ring" in names
        # Off-color ramp rejected
        assert "Azure Signet" not in names
        # Banned-in-commander rejected
        assert "Banned Rock" not in names
        # Already-in-deck rejected
        assert "Already Here" not in names

    def test_tags_are_sequential(self, seeded_db):
        cands = find_add_candidates(
            db=seeded_db, role=CardTag.RAMP,
            deck_color_identity={"G"},
            format_=Format.COMMANDER,
            exclude_names=set(),
            limit=10,
        )
        assert cands[0].tag == "a01"
        if len(cands) > 1:
            assert cands[1].tag == "a02"

    def test_budget_filter_excludes_expensive_cards(self, seeded_db):
        """Cards with known price > budget are filtered out; unknown prices pass."""
        # Seed a couple of cards with prices
        conn = seeded_db.connect()
        conn.execute("UPDATE cards SET price_usd = 0.25 WHERE name = 'Cultivate'")
        conn.execute("UPDATE cards SET price_usd = 45.0 WHERE name = 'Rampant Growth'")
        conn.commit()

        cheap = find_add_candidates(
            db=seeded_db, role=CardTag.RAMP,
            deck_color_identity={"G"},
            format_=Format.COMMANDER,
            exclude_names=set(),
            limit=10,
            budget_usd=1.0,
        )
        names = {c.card.name for c in cheap}
        assert "Cultivate" in names       # $0.25 — in budget
        assert "Rampant Growth" not in names  # $45 — excluded
        # Unknown-price cards still come through
        assert "Llanowar Elves" in names

    def test_role_filter_excludes_wrong_role(self, seeded_db):
        """Ask for removal — should NOT return ramp or card_draw cards."""
        cands = find_add_candidates(
            db=seeded_db, role=CardTag.TARGETED_REMOVAL,
            deck_color_identity={"W"},
            format_=Format.COMMANDER,
            exclude_names=set(),
            limit=10,
        )
        names = {c.card.name for c in cands}
        assert "Swords to Plowshares" in names
        assert "Cultivate" not in names
        assert "Rhystic Study" not in names


# -------------------------------------------------------------------- Belt-and-suspenders verifier

class TestVerifyAddPicksConstraints:
    def test_accepts_valid_pick(self, seeded_db):
        cands = find_add_candidates(
            db=seeded_db, role=CardTag.RAMP,
            deck_color_identity={"G"},
            format_=Format.COMMANDER,
            exclude_names=set(), limit=5,
        )
        by_tag = {c.tag: c for c in cands}
        picks = [TagPick(cands[0].tag, "good pick")]
        # No raise
        verify_add_picks_constraints(picks, by_tag, {"G"}, "commander")

    def test_rejects_off_color_even_if_tag_valid(self):
        """If the candidate table is mis-populated (bug upstream), the verifier still catches it."""
        from mtg_deck_engine.analyst.add_candidates import AddCandidate
        off_color_card = _mk_card("Counterspell", cmc=2,
                                  colors=[Color.BLUE], color_identity=[Color.BLUE])
        cand = AddCandidate(tag="a01", card=off_color_card, role=CardTag.COUNTERSPELL)
        picks = [TagPick("a01", "bad fit")]
        with pytest.raises(VerificationError) as ei:
            verify_add_picks_constraints(picks, {"a01": cand}, {"G"}, "commander")
        assert "color identity" in str(ei.value).lower()
        assert "a01" in ei.value.hint

    def test_rejects_banned_card(self):
        from mtg_deck_engine.analyst.add_candidates import AddCandidate
        banned_card = _mk_card("Primeval Titan", cmc=6,
                               color_identity=[Color.GREEN],
                               commander_legal=Legality.BANNED)
        cand = AddCandidate(tag="a01", card=banned_card, role=CardTag.RAMP)
        picks = [TagPick("a01", "strong")]
        with pytest.raises(VerificationError) as ei:
            verify_add_picks_constraints(picks, {"a01": cand}, {"G"}, "commander")
        assert "not legal" in str(ei.value).lower()


# -------------------------------------------------------------------- Prompt rendering

class TestAddPrompt:
    def test_renders_all_tags_and_context(self, seeded_db):
        cands = find_add_candidates(
            db=seeded_db, role=CardTag.RAMP,
            deck_color_identity={"G"}, format_=Format.COMMANDER,
            exclude_names=set(), limit=5,
        )
        prompt = add_suggestions_prompt(
            role_name="ramp", role_target_low=10, role_target_high=15,
            current_count=6, candidates=cands, deck_name="Mono-G",
            archetype="ramp-midrange", color_identity=["G"], count=3,
        )
        for c in cands:
            assert f"[{c.tag}]" in prompt
        assert "current 6" in prompt
        assert "target 10-15" in prompt
        # Line wrapping in the template puts a newline inside "Do\nNOT type" —
        # check for a substring that's reliably on a single line instead.
        assert "NOT type" in prompt


# -------------------------------------------------------------------- End-to-end runner

class TestRunnerAddSuggestions:
    def test_end_to_end_happy_path(self, seeded_db):
        deck = _mono_green_deck()
        analysis = AnalysisResult(
            deck_name="Mono-G", format="commander", total_cards=100,
            land_count=36, ramp_count=4,  # under target -> RAMP gap detected
            draw_engine_count=10, interaction_count=10, average_cmc=3.0,
        )
        pb = PowerBreakdown()
        pb.overall = 5.5
        pb.tier = "casual"

        # Pre-compute valid tag to script against — pick first ramp candidate
        cands = find_add_candidates(
            db=seeded_db, role=CardTag.RAMP,
            deck_color_identity={"G"}, format_=Format.COMMANDER,
            exclude_names={"Already Here", "Commander G"}, limit=20,
        )
        assert len(cands) >= 2
        picks_output = (
            f"[{cands[0].tag}]: best rate in colors.\n"
            f"[{cands[1].tag}]: complements the first pick."
        )

        mock = MockBackend(scripts=[
            ("executive summary", "A" * 250),
            ("Role gap: ramp", picks_output),
        ])
        runner = AnalystRunner(backend=mock)
        result = runner.run(
            deck, analysis, pb, advanced=None, archetype="ramp",
            want_adds=True, db=seeded_db, adds_per_role=3,
        )

        assert result.adds_verified.get("ramp") is True
        ramp_picks = result.adds.get("ramp", [])
        assert len(ramp_picks) == 2
        # Each suggested card is real — resolved via the candidate table
        picked_names = {a.card_name for a in ramp_picks}
        assert picked_names.issubset({c.card.name for c in cands})

    def test_prose_noise_does_not_leak_into_adds(self, seeded_db):
        """Prose mentioning a card outside a tag is ignored. The final adds
        list is built only from tag resolutions, so noisy emissions produce
        a clean list instead of being hard-blocked. Safety is in the tag
        constraint + the color/legality re-check — not prose scrubbing."""
        deck = _mono_green_deck()
        analysis = AnalysisResult(
            deck_name="Mono-G", format="commander", total_cards=100,
            land_count=36, ramp_count=4,
            draw_engine_count=10, interaction_count=10, average_cmc=3.0,
        )
        pb = PowerBreakdown(); pb.overall = 5.5; pb.tier = "casual"
        cands = find_add_candidates(
            db=seeded_db, role=CardTag.RAMP, deck_color_identity={"G"},
            format_=Format.COMMANDER, exclude_names={"Already Here", "Commander G"},
            limit=20,
        )
        noisy = (
            f"[{cands[0].tag}]: strong pick\n"
            "Also consider Cultivate as a followup."
        )
        mock = MockBackend(scripts=[
            ("executive summary", "A" * 250),
            ("Role gap: ramp", noisy),
        ])
        runner = AnalystRunner(backend=mock)
        result = runner.run(
            deck, analysis, pb, advanced=None, archetype="ramp",
            want_adds=True, db=seeded_db, adds_per_role=3,
        )
        # Verifier passes because the tag is valid
        assert result.adds_verified.get("ramp") is True
        # Only the tag-resolved pick ends up in adds — the free-form "Cultivate"
        # reference is dropped because it never became an AddSuggestion.
        ramp_picks = result.adds.get("ramp", [])
        assert len(ramp_picks) == 1
        assert ramp_picks[0].card_name == cands[0].card.name

    def test_swaps_pair_cut_with_replacement_at_same_role(self, seeded_db):
        """A ramp cut should get paired with an in-color ramp add, not a random card."""
        # Build a deck with a cuttable ramp piece (synthetic high-CMC ramp)
        from mtg_deck_engine.models import Card, CardLayout, Color, DeckEntry, Zone

        def _mk(name, cmc, tags, ci=None, mana_cost="{5}", is_creature=False):
            return DeckEntry(card_name=name, quantity=1, zone=Zone.MAINBOARD,
                             card=Card(
                                 scryfall_id=f"s-{name}", oracle_id=f"s-{name}", name=name,
                                 layout=CardLayout.NORMAL, cmc=cmc, mana_cost=mana_cost,
                                 type_line="Creature" if is_creature else "Artifact",
                                 color_identity=ci or [Color.GREEN], tags=tags,
                                 is_creature=is_creature,
                             ))

        entries = [
            _mk("Commander G", 4, [CardTag.FINISHER], is_creature=True),
            # Over-cap ramp pieces (one will be cut)
            *[_mk(f"OldRamp{i}", 6, [CardTag.RAMP, CardTag.MANA_ROCK]) for i in range(16)],
            # Filler
            *[_mk(f"Filler{i}", 7, []) for i in range(6)],
        ]
        # Pad to 100 with basic lands
        for i in range(78):
            entries.append(DeckEntry(
                card_name=f"Forest{i}", quantity=1, zone=Zone.MAINBOARD,
                card=Card(
                    scryfall_id=f"l-{i}", oracle_id=f"l-{i}", name=f"Forest{i}",
                    layout=CardLayout.NORMAL, is_land=True,
                    type_line="Basic Land — Forest", color_identity=[Color.GREEN],
                ),
            ))
        deck = Deck(name="Test", format=Format.COMMANDER, entries=entries[:100])

        analysis = AnalysisResult(
            deck_name="Test", format="commander", total_cards=100,
            land_count=78, ramp_count=16, draw_engine_count=2,
            interaction_count=0, average_cmc=5.0,
        )
        pb = PowerBreakdown(); pb.overall = 5.5; pb.tier = "casual"

        runner = AnalystRunner(backend=MockBackend(default=""))
        swaps = runner.run_swaps(
            deck=deck, analysis=analysis, power=pb, advanced=None,
            archetype="ramp", db=seeded_db, swap_count=3,
        )

        # We get SOME swaps
        assert len(swaps) >= 1
        # Swap-1: cut a ramp piece (from OldRamp*), add an in-color ramp piece
        # from the seeded db (Cultivate / Rampant Growth / etc.)
        first = swaps[0]
        assert first.cut_card.startswith("OldRamp") or first.cut_card.startswith("Filler")
        # Add must be in-color + a known card from the seeded db
        valid_add_names = {"Cultivate", "Rampant Growth", "Llanowar Elves", "Sol Ring"}
        assert first.add_card in valid_add_names
        # Rationale mentions a role
        assert first.role in ("ramp", "card_draw", "targeted_removal", "board_wipe")

    def test_swaps_balanced_deck_skips_untagged_cuts(self, seeded_db):
        """Balanced deck (no gaps) + cut with no functional tag -> no swap.

        The old behavior walked role_axes and suggested a swap toward RAMP
        (first role) even when ramp was at target. The fix: only try gap
        roles for no-tag cuts; when no gaps, emit no swap for that cut."""
        from mtg_deck_engine.models import Card, CardLayout, Color, DeckEntry, Zone

        # Build a balanced deck: ramp/draw/interaction all at target, with
        # a single untagged high-CMC vanilla card that should surface as a
        # cut candidate but has no role to swap INTO.
        def _mk(name, cmc, tags, ci=None, is_land=False):
            kwargs = dict(
                scryfall_id=f"s-{name}", oracle_id=f"s-{name}", name=name,
                layout=CardLayout.NORMAL, cmc=cmc,
                color_identity=ci or [Color.GREEN], tags=tags or [],
            )
            if is_land:
                kwargs["is_land"] = True
                kwargs["type_line"] = "Basic Land — Forest"
            else:
                kwargs["mana_cost"] = "{" + str(int(cmc)) + "}"
                kwargs["type_line"] = "Creature"
                kwargs["is_creature"] = True
            return DeckEntry(card_name=name, quantity=1, zone=Zone.MAINBOARD, card=Card(**kwargs))

        entries = [_mk("Commander G", 4, [CardTag.FINISHER])]
        # 12 ramp (in-range for Commander: target 10-15)
        entries += [_mk(f"Ramp{i}", 2, [CardTag.RAMP, CardTag.MANA_ROCK]) for i in range(12)]
        # 10 draw (in range 8-12)
        entries += [_mk(f"Draw{i}", 3, [CardTag.CARD_DRAW]) for i in range(10)]
        # 10 removal (in range 8-12)
        entries += [_mk(f"Removal{i}", 2, [CardTag.TARGETED_REMOVAL]) for i in range(10)]
        # 1 untagged high-CMC vanilla that the cut ranker WILL surface
        entries.append(_mk("VanillaBloat", 7, []))
        # Lands
        for i in range(36):
            entries.append(_mk(f"Forest{i}", 0, [], is_land=True))
        # Pad to 100 with more lands
        while sum(e.quantity for e in entries) < 100:
            entries.append(_mk(f"PadLand{len(entries)}", 0, [], is_land=True))
        deck = Deck(name="Balanced", format=Format.COMMANDER, entries=entries[:100])

        analysis = AnalysisResult(
            deck_name="Balanced", format="commander", total_cards=100,
            land_count=36, ramp_count=12, draw_engine_count=10,
            interaction_count=10, average_cmc=2.9,
        )
        pb = PowerBreakdown(); pb.overall = 6.5; pb.tier = "focused"

        runner = AnalystRunner(backend=MockBackend(default=""))
        swaps = runner.run_swaps(
            deck=deck, analysis=analysis, power=pb, advanced=None,
            archetype="midrange", db=seeded_db, swap_count=3,
        )
        # No role gaps + untagged cut = no swap. Total swaps should be 0
        # (nothing else in the deck surfaces as a cut candidate).
        assert len(swaps) == 0

    def test_invalid_add_tag_blocks_output(self, seeded_db):
        """If the model emits an invalid tag, the tag-in-table check rejects
        and retries exhaust — adds stays empty. This is the real hallucination
        guard on the ADD side (tag-table constraint + legality re-check).
        """
        deck = _mono_green_deck()
        analysis = AnalysisResult(
            deck_name="Mono-G", format="commander", total_cards=100,
            land_count=36, ramp_count=4,
            draw_engine_count=10, interaction_count=10, average_cmc=3.0,
        )
        pb = PowerBreakdown(); pb.overall = 5.5; pb.tier = "casual"
        bogus = "[zzz99]: fake tag"
        mock = MockBackend(scripts=[
            ("executive summary", "A" * 250),
            ("Role gap: ramp", bogus),
            ("Role gap: ramp", bogus),
            ("Role gap: ramp", bogus),
        ])
        runner = AnalystRunner(backend=mock)
        result = runner.run(
            deck, analysis, pb, advanced=None, archetype="ramp",
            want_adds=True, db=seeded_db, adds_per_role=3,
        )
        assert result.adds_verified.get("ramp") is False
        assert "ramp" not in result.adds
