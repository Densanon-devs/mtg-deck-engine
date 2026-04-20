"""LLM backends for the analyst.

The `LLMBackend` protocol is minimal — one `generate(prompt, max_tokens)` method —
so swapping PIE for a stub for a future cloud provider is mechanical. MockBackend
is the scriptable in-memory backend used for tests; it returns pre-seeded responses
keyed by a signature of the prompt (task tag + input hash) so retry-loop tests can
simulate failure-then-success sequences deterministically.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMBackend(Protocol):
    """Minimal interface for an LLM provider.

    Backends must be side-effect-free with respect to the analyst's state —
    they don't know about verifiers, retries, or confidence. The pipeline
    wraps them; they just map prompt → text.
    """

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        ...


class MockBackend:
    """Scriptable backend for tests.

    Scripts are a list of (match_substring, response) tuples. On each generate()
    call, the first script entry whose substring is found in the prompt is
    returned — and consumed, so scripted retries can produce different outputs.
    If no script matches, returns the `default` string.

    Example:
        >>> mock = MockBackend(scripts=[
        ...     ("cut the following", "[c01]: needs cutting\\n[c03]: redundant"),
        ...     ("invalid tag", "[c01]: fixed on retry"),
        ... ])
        >>> mock.generate("Cut the following cards...")
        '[c01]: needs cutting\\n[c03]: redundant'
    """

    def __init__(self, scripts: list[tuple[str, str]] | None = None, default: str = ""):
        # Copy so callers can reuse the script list across tests.
        self._scripts = list(scripts or [])
        self._default = default
        self.call_log: list[str] = []

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        self.call_log.append(prompt)
        for i, (needle, response) in enumerate(self._scripts):
            if needle in prompt:
                # Consume the entry so a subsequent matching prompt falls through
                # to the next script line — enables "fail then succeed" scenarios.
                del self._scripts[i]
                return response
        return self._default
