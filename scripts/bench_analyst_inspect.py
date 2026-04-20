"""Diagnostic run: show WHAT each model picks, not just pass/fail counts.

The headline gauntlet only reports aggregate scores. When relevance is low,
you need to see whether the model picked something defensible that just
didn't overlap the gold set, vs. picking something genuinely off. This
script runs each model over the default cases and prints the raw cut
picks + reasons for inspection.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Windows cp1252 stdout eats non-ASCII (✓ ×) that the diagnostic output
# emits — reconfigure to UTF-8 up-front so we don't crash mid-print.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from mtg_deck_engine.analyst import AnalystRunner  # noqa: E402
from mtg_deck_engine.analyst.backends.llama_cpp import LlamaCppBackend  # noqa: E402
from mtg_deck_engine.benchmarks.analyst_gauntlet import default_cases  # noqa: E402


MODELS = [
    ("Qwen2.5-0.5B", Path("D:/LLCWork/plug-in-intelligence-engine/models/qwen2.5-0.5b-instruct-q4_k_m.gguf")),
    ("Qwen2.5-3B",   Path("D:/LLCWork/plug-in-intelligence-engine/models/qwen2.5-3b-instruct-q4_k_m.gguf")),
]


def inspect(label: str, model_path: Path):
    print()
    print("=" * 70)
    print(f"{label}")
    print("=" * 70)
    if not model_path.exists():
        print("SKIP: model not found")
        return

    backend = LlamaCppBackend(model_path=model_path, n_ctx=4096)
    t0 = time.time()
    backend._ensure_loaded()
    print(f"(load: {time.time() - t0:.1f}s)")

    runner = AnalystRunner(backend=backend, max_retries=2)

    for case in default_cases():
        print()
        print(f"--- {case.case_id} ---")
        deck = case.build_deck()
        analysis, power, archetype = case.build_analysis()

        t0 = time.time()
        result = runner.run(
            deck=deck, analysis=analysis, power=power, advanced=None,
            archetype=archetype,
        )
        elapsed = time.time() - t0

        print(f"(gen: {elapsed:.1f}s)")
        print(f"Summary verified={result.summary_verified} confidence={result.summary_confidence:.1f}")
        if result.summary:
            # Just the first 2 lines so we can skim
            first_chunk = result.summary.strip().split("\n\n")[0]
            print(f"  | {first_chunk[:240]}")

        print(f"Cuts verified={result.cuts_verified} confidence={result.cuts_confidence:.1f}")
        if result.cuts:
            for c in result.cuts:
                in_gold = "✓ (gold)" if c.card_name in case.gold_cuts else "  "
                print(f"  {in_gold} [{c.tag}] {c.card_name}: {c.reason[:100]}")
        else:
            # Show the raw attempts for diagnosis
            if result.raw_cuts:
                print(f"  (no picks; verification errors: {result.raw_cuts.errors[:2]})")

        if case.gold_cuts:
            print(f"  gold set: {sorted(case.gold_cuts)[:6]}...")


def main():
    for label, path in MODELS:
        inspect(label, path)


if __name__ == "__main__":
    main()
