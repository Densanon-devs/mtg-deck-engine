"""Run the analyst gauntlet against multiple local GGUF models back-to-back.

Usage (from mtg-deck-engine root):
    PYTHONPATH=src py -3.10 scripts/bench_analyst_models.py

The script iterates a list of (label, path) tuples, running the default
gauntlet cases through each. Scores side-by-side for hard-pass, relevance,
and latency.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Make `mtg_deck_engine` importable without installing the package — this
# script is expected to run from the repo root against whatever Python
# has a working llama-cpp-python install (3.10 with 0.3.20 + CUDA here).
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

# Windows cp1252 stdout eats the em dash and unicode markers — reconfigure.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from mtg_deck_engine.analyst import AnalystRunner  # noqa: E402
from mtg_deck_engine.analyst.backends.llama_cpp import LlamaCppBackend  # noqa: E402
from mtg_deck_engine.benchmarks.analyst_gauntlet import (  # noqa: E402
    default_cases,
    print_report,
    run_gauntlet,
)


MODELS = [
    (
        "Qwen2.5-0.5B-Instruct (Q4_K_M)",
        Path("D:/LLCWork/plug-in-intelligence-engine/models/qwen2.5-0.5b-instruct-q4_k_m.gguf"),
    ),
    (
        "Qwen2.5-3B-Instruct (Q4_K_M)",
        Path("D:/LLCWork/plug-in-intelligence-engine/models/qwen2.5-3b-instruct-q4_k_m.gguf"),
    ),
]


def bench_one(label: str, model_path: Path, n_gpu_layers: int = 0):
    print()
    print("#" * 70)
    print(f"# {label}   (gpu_layers={n_gpu_layers})")
    print(f"# {model_path}")
    print("#" * 70)

    if not model_path.exists():
        print(f"SKIP: model not found at {model_path}")
        return None

    t_load_start = time.time()
    backend = LlamaCppBackend(
        model_path=model_path,
        n_ctx=4096,
        n_gpu_layers=n_gpu_layers,
    )
    backend._ensure_loaded()
    t_loaded = time.time() - t_load_start
    print(f"Model loaded in {t_loaded:.2f}s")

    runner = AnalystRunner(backend=backend, max_retries=2)

    t_run_start = time.time()
    res = run_gauntlet(runner, cases=default_cases(), verbose=True)
    t_run = time.time() - t_run_start

    print_report(res)
    print(f"Total gauntlet wall time: {t_run:.2f}s  ({t_run / max(1, res.total_cases):.2f}s per case)")
    return {"label": label, "result": res, "load_s": t_loaded, "run_s": t_run}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu-layers", type=int, default=0,
                        help="n_gpu_layers for llama-cpp. -1 offloads all layers. 0 = CPU.")
    args = parser.parse_args()

    os.environ.setdefault("MTG_ANALYST_BACKEND", "llama_cpp")

    all_results = []
    for label, path in MODELS:
        rec = bench_one(label, path, n_gpu_layers=args.gpu_layers)
        if rec:
            all_results.append(rec)

    if len(all_results) >= 2:
        print()
        print("=" * 78)
        print("SIDE-BY-SIDE")
        print("=" * 78)
        print(f"{'Model':<30} {'HardPass':>10} {'Strict':>10} {'Defensible':>12} {'Wall s':>10}")
        for r in all_results:
            lbl = r["label"][:28]
            res = r["result"]
            print(f"{lbl:<30} "
                  f"{int(res.hard_pass_rate * 100):>9}% "
                  f"{int(res.cuts_strict_overlap * 100):>9}% "
                  f"{int(res.cuts_defensible_rate * 100):>11}% "
                  f"{r['run_s']:>10.2f}")


if __name__ == "__main__":
    main()
