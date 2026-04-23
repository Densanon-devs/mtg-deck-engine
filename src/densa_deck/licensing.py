"""Pro license management — hash-based, no server needed.

Matches the same pattern as D-Brief: keys are deterministic hashes of the
Stripe session_id + a salt. The app validates by re-hashing the segments
and checking the checksum (no need to know the original session_id).

The hash function is intentionally identical to the JavaScript version
on toolkit.densanon.com/densa-deck-success.html so keys generated in the browser
validate correctly in the desktop app.

Format: DD-XXXX-XXXX-XXXX
  - p1: first 4 chars of hashKey(session_id)
  - p2: next 4 chars of hashKey(session_id)
  - p3: first 4 chars of hashKey(p1-p2) — checksum

Master key bypasses all checks (developer access).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Master key — always unlocks Pro. Only the developer knows this.
MASTER_KEY = "densanon-densa-deck-2026"

# Salt for hashing license keys (must match the JS in densa-deck-success.html)
LICENSE_SALT = "Densa-Deck-pro-v1"

LICENSE_PATH = Path.home() / ".densa-deck" / "license.key"

_KEY_PATTERN = re.compile(r"^DD-([A-Z0-9]{4})-([A-Z0-9]{4})-([A-Z0-9]{4})$")


@dataclass
class License:
    """A parsed license key."""

    key: str
    valid: bool = False
    is_master: bool = False
    activated_at: str = ""
    error: str = ""

    def grants_pro(self) -> bool:
        return self.valid


def _hash_key(input_str: str) -> str:
    """Simple deterministic hash, matching the JavaScript version exactly.

    Mirrors:
        let hash = 0;
        for (let i = 0; i < input.length; i++) {
            const char = input.charCodeAt(i);
            hash = ((hash << 5) - hash) + char;
            hash = hash & hash;  // 32-bit
        }
        return Math.abs(hash).toString(36);

    Python's int doesn't auto-truncate, so we mask to 32 bits and handle
    sign exactly the way JavaScript does.
    """
    full_input = f"{LICENSE_SALT}:{input_str.strip().lower()}"
    h = 0
    for ch in full_input:
        c = ord(ch)
        h = ((h << 5) - h) + c
        # Convert to 32-bit signed int (matching JavaScript's `hash & hash`)
        h = h & 0xFFFFFFFF
        if h & 0x80000000:
            h = h - 0x100000000
    # Math.abs + base 36
    return _to_base36(abs(h))


def _to_base36(n: int) -> str:
    """Convert non-negative int to base 36 string (matching JS toString(36))."""
    if n == 0:
        return "0"
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = ""
    while n > 0:
        result = chars[n % 36] + result
        n //= 36
    return result


def generate_license_key(seed: str) -> str:
    """Generate a deterministic license key from a seed (e.g. Stripe session_id).

    This is used by the success page in the browser. Replicated here for
    testing and admin use. Customers receive the generated key directly.
    """
    h = _hash_key(seed)
    padded = h.ljust(8, "0")
    p1 = padded[:4].upper()
    p2 = padded[4:8].upper()
    check_input = f"{p1}-{p2}"
    check_hash = _hash_key(check_input)
    p3 = check_hash.ljust(4, "0")[:4].upper()
    return f"DD-{p1}-{p2}-{p3}"


def validate_key(key: str) -> bool:
    """Validate a license key by checksum verification.

    Accepts:
      - Master key (developer access)
      - Properly formatted DD-XXXX-XXXX-XXXX with valid checksum
    """
    cleaned = key.strip()

    # Master key bypass
    if cleaned == MASTER_KEY:
        return True

    # Format check
    match = _KEY_PATTERN.match(cleaned.upper())
    if not match:
        return False

    p1, p2, p3 = match.group(1), match.group(2), match.group(3)

    # Recompute checksum
    check_input = f"{p1}-{p2}"
    check_hash = _hash_key(check_input)
    expected_p3 = check_hash.ljust(4, "0")[:4].upper()

    return p3 == expected_p3


def verify_license_key(key: str) -> License:
    """Parse and verify a license key. Returns a License object whose
    `error` field is a specific, user-actionable reason on failure:

      - "Empty license key" — nothing was pasted.
      - "Wrong prefix — expected DD-XXXX-XXXX-XXXX (yours starts with ...)"
      - "Wrong length — expected 16 characters (yours is N)"
      - "Unexpected characters in the key — only A-Z and 0-9 allowed"
      - "Checksum mismatch — typo in the last segment?"

    The frontend shows this directly so users who pasted a stray space,
    transposed a digit, or lost a character know exactly what to fix.
    """
    cleaned = (key or "").strip()
    license = License(key=cleaned)

    if not cleaned:
        license.error = "Empty license key — paste the DD-XXXX-XXXX-XXXX value from your receipt."
        return license

    # Master key bypass — covered here so the granular error messages below
    # don't accidentally reject it (its length / format is intentionally
    # different from a real license).
    if cleaned == MASTER_KEY:
        license.valid = True
        license.is_master = True
        return license

    upper = cleaned.upper()
    match = _KEY_PATTERN.match(upper)
    if match is None:
        # Break down why the format check failed so the user can see which
        # part of the key looks off.
        if not upper.startswith("DD-"):
            license.error = (
                "Wrong prefix — expected a DD-XXXX-XXXX-XXXX key "
                f"(yours starts with '{upper[:3]}')."
            )
        elif len(upper) != len("DD-XXXX-XXXX-XXXX"):
            license.error = (
                "Wrong length — expected 16 characters "
                f"including the DD- prefix and dashes (yours is {len(upper)})."
            )
        elif not re.fullmatch(r"[A-Z0-9-]+", upper):
            license.error = (
                "Unexpected characters in the key — only letters and "
                "digits are allowed between the dashes."
            )
        else:
            license.error = (
                "Dashes are in the wrong places — the key should look "
                "like DD-XXXX-XXXX-XXXX with dashes after DD and every 4 characters."
            )
        return license

    # Format is valid; recompute the checksum segment.
    p1, p2, p3 = match.group(1), match.group(2), match.group(3)
    check_hash = _hash_key(f"{p1}-{p2}")
    expected_p3 = check_hash.ljust(4, "0")[:4].upper()
    if p3 == expected_p3:
        license.valid = True
    else:
        license.error = (
            "Checksum doesn't match — the last 4 characters look like a "
            "typo. Copy the full key from your Stripe receipt again or "
            "email admin@densanon.com if you need help."
        )
    return license


def save_license(key: str) -> License:
    """Validate and save a license key to the user's config directory.

    Writes atomically (temp file + os.replace) so a crash mid-write can't
    truncate the license.key file and leave the user re-activating on
    every restart.
    """
    license = verify_license_key(key)
    if not license.valid:
        return license

    LICENSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "key": key.strip(),
        "activated_at": datetime.now().isoformat(),
    }
    payload = json.dumps(data)
    # NamedTemporaryFile in the same directory so os.replace is atomic
    # (cross-filesystem rename isn't guaranteed atomic on Windows).
    fd, tmp_path = tempfile.mkstemp(
        prefix=".license.key.", suffix=".tmp", dir=str(LICENSE_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                # fsync can fail on some remote filesystems — not fatal
                pass
        os.replace(tmp_path, LICENSE_PATH)
    except Exception:
        # Clean up the temp file on any failure so we don't pollute the
        # config dir with stale .license.key.*.tmp files.
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass
        raise
    license.activated_at = data["activated_at"]
    return license


def load_saved_license() -> License | None:
    """Load and verify the saved license, if any.

    Returns None on "no license present" (missing file or empty file) and
    also on I/O / parse errors — in the error case the damaged file is
    moved aside to `license.key.corrupt-<timestamp>.bak` so a future save
    can recreate cleanly, and a breadcrumb is left in
    `~/.densa-deck/load-errors.log` for support diagnostics.
    """
    if not LICENSE_PATH.exists():
        return None
    try:
        content = LICENSE_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        _quarantine_license(reason=f"read failed: {exc}")
        return None
    if not content:
        return None
    # Try JSON first (new format), fall back to raw key (old format).
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Treat as legacy raw-key format.
        return verify_license_key(content)
    key = data.get("key", "") if isinstance(data, dict) else ""
    license = verify_license_key(key)
    if not license.valid:
        # The file parsed as JSON but didn't contain a usable key —
        # quarantine so we don't keep tripping on it every launch.
        _quarantine_license(reason="parsed license.key contained no valid key")
        return None
    license.activated_at = data.get("activated_at", "") if isinstance(data, dict) else ""
    return license


def _quarantine_license(reason: str) -> None:
    """Move a bad license.key aside so the next save_license starts fresh.

    Mirrors the quarantine pattern AppApi._quarantine_bad_file uses for
    coach_sessions.json. Best-effort — a failure here must not block app
    startup, so all filesystem calls are wrapped in try/except.
    """
    from datetime import datetime as _dt
    stamp = _dt.now().strftime("%Y%m%d-%H%M%S")
    try:
        LICENSE_PATH.rename(LICENSE_PATH.with_suffix(
            LICENSE_PATH.suffix + f".corrupt-{stamp}.bak"
        ))
    except OSError:
        try:
            LICENSE_PATH.unlink()
        except OSError:
            pass
    try:
        log_path = LICENSE_PATH.parent / "load-errors.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] quarantined license.key: {reason}\n")
    except OSError:
        pass


def remove_license() -> bool:
    """Remove the saved license. Returns True if a license was removed."""
    if LICENSE_PATH.exists():
        LICENSE_PATH.unlink()
        return True
    return False
