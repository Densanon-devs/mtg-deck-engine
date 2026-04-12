"""License key generator (admin tool — DO NOT SHIP TO USERS).

Usage:
  # First time: generate a keypair
  python scripts/generate_license.py keypair

  # Then issue a license
  python scripts/generate_license.py issue --email user@example.com --private-key HEX

  # Lifetime license
  python scripts/generate_license.py issue --email user@example.com --tier lifetime --private-key HEX

The private key should be stored securely (password manager, encrypted vault).
The public key gets baked into the build via PUBLIC_KEY_HEX in licensing.py.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timedelta

from mtg_deck_engine.licensing import generate_keypair, sign_license_payload, verify_license_key


def cmd_keypair():
    """Generate a new keypair for signing licenses."""
    priv, pub = generate_keypair()
    print()
    print("=" * 60)
    print("NEW KEYPAIR GENERATED")
    print("=" * 60)
    print()
    print("PRIVATE KEY (keep secret — used to sign new licenses):")
    print(f"  {priv}")
    print()
    print("PUBLIC KEY (bake into licensing.py PUBLIC_KEY_HEX):")
    print(f"  {pub}")
    print()
    print("Next steps:")
    print("  1. Save the private key to a secure password manager")
    print("  2. Replace PUBLIC_KEY_HEX in src/mtg_deck_engine/licensing.py")
    print("  3. Rebuild the desktop binary")
    print("  4. Issue licenses with: python scripts/generate_license.py issue --email X --private-key Y")
    print()


def cmd_issue(args):
    """Generate a signed license for a customer."""
    payload = {
        "id": str(uuid.uuid4()),
        "email": args.email,
        "product": "mtg-deck-engine-pro",
        "tier": args.tier,
        "issued": datetime.now().date().isoformat(),
        "expires": "never" if args.tier == "lifetime" else (
            (datetime.now() + timedelta(days=args.days)).date().isoformat()
        ),
    }

    key = sign_license_payload(payload, args.private_key)

    # Verify it round-trips
    from mtg_deck_engine.licensing import generate_keypair
    # Derive public key from private to verify locally
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv_obj = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(args.private_key))
    from cryptography.hazmat.primitives import serialization
    pub_hex = priv_obj.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()

    verified = verify_license_key(key, public_key_hex=pub_hex)

    print()
    print("=" * 60)
    print("LICENSE GENERATED")
    print("=" * 60)
    print()
    print(f"Email:    {args.email}")
    print(f"Tier:     {args.tier}")
    print(f"Issued:   {payload['issued']}")
    print(f"Expires:  {payload['expires']}")
    print(f"ID:       {payload['id']}")
    print(f"Verified: {verified.valid and verified.grants_pro()}")
    print()
    print("LICENSE KEY (send to customer):")
    print()
    print(key)
    print()
    print("Customer activates with:")
    print(f"  mtg-engine license activate {key[:40]}...")
    print()


def main():
    parser = argparse.ArgumentParser(description="MTG Deck Engine license generator (admin tool)")
    sub = parser.add_subparsers(dest="action")

    sub.add_parser("keypair", help="Generate a new signing keypair")

    issue = sub.add_parser("issue", help="Issue a new license")
    issue.add_argument("--email", type=str, required=True, help="Customer email")
    issue.add_argument("--tier", type=str, default="pro", choices=["pro", "lifetime"])
    issue.add_argument("--days", type=int, default=365, help="License duration (default 365)")
    issue.add_argument("--private-key", type=str, required=True, help="Hex private signing key")

    args = parser.parse_args()

    if args.action == "keypair":
        cmd_keypair()
    elif args.action == "issue":
        cmd_issue(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
