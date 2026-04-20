"""Per-case diagnostic run against one model.

Shows for every case: gold set, ranker top-5, model picks, and whether each
pick is in gold. Writes a markdown-ish table so patterns across 30 cases
are easy to spot.

Usage:
    PYTHONPATH=src py -3.10 scripts/bench_analyst_diag.py [--model 0.5b|3b]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mtg_deck_engine.analyst import AnalystRunner  # noqa: E402
from mtg_deck_engine.analyst.backends.llama_cpp import LlamaCppBackend  # noqa: E402
from mtg_deck_engine.analyst.candidates import rank_cut_candidates  # noqa: E402
from mtg_deck_engine.benchmarks.analyst_gauntlet import default_cases  # noqa: E402


MODEL_PATHS = {
    "0.5b": Path("D:/LLCWork/plug-in-intelligence-engine/models/qwen2.5-0.5b-instruct-q4_k_m.gguf"),
    "3b":   Path("D:/LLCWork/plug-in-intelligence-engine/models/qwen2.5-3b-instruct-q4_k_m.gguf"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=list(MODEL_PATHS), default="3b")
    ap.add_argument("--gpu-layers", type=int, default=-1)
    args = ap.parse_args()

    backend = LlamaCppBackend(model_path=MODEL_PATHS[args.model], n_ctx=4096, n_gpu_layers=args.gpu_layers)
    backend._ensure_loaded()
    runner = AnalystRunner(backend=backend)

    print(f"\n{'case_id':<22} | {'top5_ranker':<50} | {'gold':<45} | model_picks")
    print("-" * 170)

    matches = 0
    total_picks = 0

    for case in default_cases():
        deck = case.build_deck()
        analysis, power, archetype = case.build_analysis()

        # What the ranker surfaces
        cands = rank_cut_candidates(deck, limit=12)
        top5 = [f"{c.tag}={c.entry.card.name}" for c in cands[:5]]

        t0 = time.time()
        result = runner.run(deck=deck, analysis=analysis, power=power, advanced=None, archetype=archetype)
        elapsed = time.time() - t0

        picks = []
        for c in result.cuts:
            hit = "★" if c.card_name in case.gold_cuts else " "
            picks.append(f"{hit}{c.tag}={c.card_name}")
            total_picks += 1
            if c.card_name in case.gold_cuts:
                matches += 1

        gold_short = sorted(case.gold_cuts)[:4]
        status = ", ".join(picks) if picks else f"∅ verify_fail errs={result.raw_cuts.errors[:1] if result.raw_cuts else []}"
        print(f"{case.case_id:<22} | {', '.join(top5)[:48]:<50} | {', '.join(gold_short)[:43]:<45} | {status[:100]}  ({elapsed:.1f}s)")

    print()
    print(f"{matches}/{total_picks} picks in gold ({matches/max(1, total_picks)*100:.1f}%)")


if __name__ == "__main__":
    main()
