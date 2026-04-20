"""Generate-verify-retry loop.

Wraps an LLM backend so that failed verification becomes a retry with the
verifier's hint appended to the original prompt — the PIE / TCE pattern where
errors are context, not just a reason to discard and resample.

Confidence drops with each retry (initial 1.0, -0.2 per retry). If all retries
fail, returns the best-parse output the LLM produced along with a `failed` flag
— callers can decide whether to surface a warning or suppress entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from mtg_deck_engine.analyst.backends import LLMBackend
from mtg_deck_engine.analyst.verifiers import VerificationError


@dataclass
class GenerateResult:
    """Result of a generate-with-verify attempt."""

    output: str
    verified: bool
    confidence: float  # 0.0-1.0; 1.0 on first-try pass, -0.2 per retry, 0.0 on total failure
    attempts: int
    errors: list[str]  # Verification errors along the way, in order


def generate_with_verify(
    backend: LLMBackend,
    prompt: str,
    verify: Callable[[str], None],
    max_retries: int = 2,
    max_tokens: int = 512,
) -> GenerateResult:
    """Generate text and verify; on failure, retry with feedback appended.

    `verify` should raise `VerificationError` on failure (the `.hint` attribute
    becomes the retry feedback). On pass, it returns None.

    Total attempts = 1 + max_retries. Confidence starts at 1.0 and decreases
    by 0.2 per retry. If the verifier never passes, returns the LAST output
    with `verified=False` so callers can decide how to degrade.
    """
    current_prompt = prompt
    errors: list[str] = []
    last_output = ""

    for attempt in range(1 + max_retries):
        output = backend.generate(current_prompt, max_tokens=max_tokens)
        last_output = output
        try:
            verify(output)
        except VerificationError as e:
            errors.append(str(e))
            if attempt >= max_retries:
                break
            current_prompt = _retry_prompt(prompt, output, e.hint)
            continue
        return GenerateResult(
            output=output,
            verified=True,
            confidence=max(0.0, 1.0 - 0.2 * attempt),
            attempts=attempt + 1,
            errors=errors,
        )

    return GenerateResult(
        output=last_output,
        verified=False,
        confidence=0.0,
        attempts=1 + max_retries,
        errors=errors,
    )


def _retry_prompt(original_prompt: str, last_output: str, hint: str) -> str:
    """Build the retry prompt by appending the failed attempt and the hint.

    Keeping the original prompt intact preserves the few-shot example and task
    framing; the retry just pins down what went wrong last time.
    """
    return (
        original_prompt
        + "\n\n[PREVIOUS ATTEMPT — REJECTED]\n"
        + last_output.strip()
        + "\n\n[FEEDBACK]\n"
        + hint
        + "\n\n[OUTPUT — corrected]\n"
    )
