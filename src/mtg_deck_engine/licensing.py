"""Offline license key system using ed25519 signatures.

License keys are signed payloads that can be verified offline using
a bundled public key. No server, no internet connection required after
purchase.

Key format: base64url(payload_json + ":" + signature_hex)

Payload fields:
  - id: unique license ID (UUID)
  - email: customer email
  - product: "mtg-deck-engine-pro"
  - tier: "pro" | "lifetime"
  - issued: ISO date
  - expires: ISO date or "never"
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


# Public key for verifying licenses (hex-encoded)
# This is bundled in the binary. The matching private key is held by the seller
# and used to generate licenses via scripts/generate_license.py.
#
# To rotate keys: generate a new keypair, replace this constant, and rebuild.
# Old licenses signed with the previous key will become invalid.
PUBLIC_KEY_HEX = (
    # Default development key — REPLACE for production builds.
    # The matching private key is in scripts/dev_private_key.txt (gitignored).
    # For production: run `python scripts/generate_license.py keypair`,
    # save the private key securely, and replace this constant with the new public key.
    "399ae237c39d209206afd4a3d34d579959a4cd3afce4a78568084eff15cb2a82"
)


LICENSE_PATH = Path.home() / ".mtg-deck-engine" / "license.key"


@dataclass
class License:
    """A parsed and validated license."""

    id: str
    email: str
    product: str
    tier: str
    issued: str
    expires: str
    valid: bool = False
    error: str = ""

    def is_active(self) -> bool:
        """Check if the license is currently active (not expired)."""
        if not self.valid:
            return False
        if self.expires == "never":
            return True
        try:
            exp = datetime.fromisoformat(self.expires)
            return datetime.now() < exp
        except (ValueError, TypeError):
            return False

    def grants_pro(self) -> bool:
        """Check if this license grants Pro tier access."""
        return self.is_active() and self.tier in ("pro", "lifetime")


def verify_license_key(key: str, public_key_hex: str | None = None) -> License:
    """Parse and verify a license key string. Returns License object."""
    if public_key_hex is None:
        public_key_hex = PUBLIC_KEY_HEX

    license = License(id="", email="", product="", tier="", issued="", expires="")

    try:
        decoded = base64.urlsafe_b64decode(key.encode("ascii") + b"==").decode("utf-8")
        payload_str, sig_hex = decoded.rsplit(":", 1)
        payload = json.loads(payload_str)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        license.error = "Invalid license format"
        return license

    # Populate fields
    license.id = payload.get("id", "")
    license.email = payload.get("email", "")
    license.product = payload.get("product", "")
    license.tier = payload.get("tier", "")
    license.issued = payload.get("issued", "")
    license.expires = payload.get("expires", "never")

    # Verify signature
    try:
        pub_bytes = bytes.fromhex(public_key_hex)
        if len(pub_bytes) != 32:
            license.error = "Invalid public key length"
            return license
        public_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
        signature = bytes.fromhex(sig_hex)
        public_key.verify(signature, payload_str.encode("utf-8"))
        license.valid = True
    except InvalidSignature:
        license.error = "Invalid signature — license may be tampered with"
        return license
    except (ValueError, TypeError) as e:
        license.error = f"Verification failed: {e}"
        return license

    # Check product
    if license.product != "mtg-deck-engine-pro":
        license.valid = False
        license.error = f"License is for '{license.product}', not mtg-deck-engine-pro"
        return license

    return license


def sign_license_payload(payload: dict[str, Any], private_key_hex: str) -> str:
    """Sign a license payload with a private key. Returns the encoded license key.

    Used by the license generator script (admin tool, not shipped to users).
    """
    priv_bytes = bytes.fromhex(private_key_hex)
    private_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)

    payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    signature = private_key.sign(payload_str.encode("utf-8"))
    sig_hex = signature.hex()

    combined = f"{payload_str}:{sig_hex}"
    encoded = base64.urlsafe_b64encode(combined.encode("utf-8")).decode("ascii").rstrip("=")
    return encoded


def generate_keypair() -> tuple[str, str]:
    """Generate a new ed25519 keypair. Returns (private_hex, public_hex)."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    from cryptography.hazmat.primitives import serialization
    priv_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv_bytes.hex(), pub_bytes.hex()


def save_license(key: str) -> License:
    """Validate and save a license key to the user's config directory."""
    license = verify_license_key(key)
    if not license.valid:
        return license

    LICENSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LICENSE_PATH.write_text(key, encoding="utf-8")
    return license


def load_saved_license() -> License | None:
    """Load and verify the saved license, if any."""
    if not LICENSE_PATH.exists():
        return None
    try:
        key = LICENSE_PATH.read_text(encoding="utf-8").strip()
        if not key:
            return None
        return verify_license_key(key)
    except OSError:
        return None


def remove_license() -> bool:
    """Remove the saved license. Returns True if a license was removed."""
    if LICENSE_PATH.exists():
        LICENSE_PATH.unlink()
        return True
    return False
