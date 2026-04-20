"""Local GGUF-backed LLM backend via llama-cpp-python.

Phase 3 primary inference path. We deliberately avoid importing PIE's full
engine stack: the hallucination-mitigation patterns that matter (verify/retry,
tag-constrained prompts, knowledge-gate) are already implemented in
mtg_deck_engine.analyst. What we need from PIE's toolbox is the in-process
GGUF inference loop — and llama-cpp-python provides that directly without
committing us to PIE's router, memory, or KV-cache machinery.

If future gauntlet results show that PIE's few-shot retrieval (module-level
example banks) outperforms our static few-shot in the prompt templates, a
sibling `pie.py` backend can vendor the relevant engine/experts.py subset
without touching the analyst pipeline.

Model search path (first hit wins):
  1. `MTG_ANALYST_MODEL` env var (absolute path to a .gguf file)
  2. `~/.mtg-deck-engine/models/analyst.gguf`

Recommended default: Llama 3.2 1B Instruct Q4_K_M (~770 MB). Configure via
`mtg-engine analyst pull llama-3.2-1b` (future subcommand) or drop the file
at the default path.
"""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_MODEL_PATH = Path.home() / ".mtg-deck-engine" / "models" / "analyst.gguf"


class LlamaCppBackend:
    """LLMBackend backed by llama-cpp-python and a local GGUF file.

    Lazy-loads the model on first `generate()` call so constructing the backend
    (which might happen just to answer "is PIE available?") doesn't pay the
    model-load cost. The underlying `Llama` object is cached for the process
    lifetime — callers pay the load once per CLI invocation.

    Sampling defaults target determinism: low temperature, nucleus sampling
    disabled. Deterministic-ish output is good for the verifier/retry loop
    (retries with feedback should converge on the corrected shape rather than
    drift to a totally new angle).
    """

    def __init__(
        self,
        model_path: str | Path | None = None,
        n_ctx: int = 4096,
        n_threads: int | None = None,
        temperature: float = 0.2,
        seed: int = 42,
        n_gpu_layers: int = 0,
    ):
        self._model_path = Path(
            model_path
            or os.environ.get("MTG_ANALYST_MODEL")
            or DEFAULT_MODEL_PATH
        )
        self._n_ctx = n_ctx
        self._n_threads = n_threads
        self._temperature = temperature
        # Default 42 (deterministic) rather than 0, because llama.cpp's convention
        # treats seed=0 as "randomize" in some versions — that would silently break
        # the "same seed + same prompt = same output" guarantee the CLI advertises.
        # Users who want fresh rolls should pass different non-zero seeds.
        self._seed = seed if seed else 42
        # n_gpu_layers: 0 = CPU only, -1 = offload all layers to GPU. The
        # user-facing default is 0 so shipping the package on a CPU-only box
        # doesn't try to load CUDA libs it doesn't have. The bench script
        # overrides to -1 when CUDA is available.
        self._n_gpu_layers = n_gpu_layers
        self._llama = None  # Lazy-initialised on first generate()

    @property
    def model_path(self) -> Path:
        return self._model_path

    def is_available(self) -> bool:
        """True if the model file exists and llama-cpp-python is importable."""
        if not self._model_path.exists():
            return False
        try:
            import llama_cpp  # noqa: F401
            return True
        except ImportError:
            return False

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        self._ensure_loaded()
        output = self._llama.create_completion(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=self._temperature,
            # Stop on the [OUTPUT] / [INPUT] markers so the model doesn't
            # hallucinate continued "template" exchanges after its answer.
            stop=["[INPUT]", "[OUTPUT]", "[EXAMPLE]", "[/EXAMPLE]"],
        )
        choices = output.get("choices", []) if isinstance(output, dict) else []
        if not choices:
            return ""
        return choices[0].get("text", "").strip()

    # ------------------------------------------------------------------ internals

    def _ensure_loaded(self):
        if self._llama is not None:
            return
        if not self._model_path.exists():
            raise FileNotFoundError(
                f"Analyst model not found at {self._model_path}. "
                "Set MTG_ANALYST_MODEL or place a GGUF file at "
                f"{DEFAULT_MODEL_PATH}."
            )
        try:
            from llama_cpp import Llama
        except ImportError as e:
            raise ImportError(
                "llama-cpp-python is not installed. "
                "Install with: pip install 'mtg-deck-engine[analyst]'"
            ) from e
        kwargs = dict(
            model_path=str(self._model_path),
            n_ctx=self._n_ctx,
            seed=self._seed,
            verbose=False,
            n_gpu_layers=self._n_gpu_layers,
        )
        if self._n_threads is not None:
            kwargs["n_threads"] = self._n_threads
        self._llama = Llama(**kwargs)
