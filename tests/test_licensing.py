"""Tests for the license key system."""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from mtg_deck_engine.licensing import (
    License,
    generate_keypair,
    sign_license_payload,
    verify_license_key,
)


def _make_payload(email="test@example.com", tier="pro", days=365):
    return {
        "id": "test-id-12345",
        "email": email,
        "product": "mtg-deck-engine-pro",
        "tier": tier,
        "issued": datetime.now().date().isoformat(),
        "expires": "never" if tier == "lifetime" else (datetime.now() + timedelta(days=days)).date().isoformat(),
    }


class TestKeyGeneration:
    def test_generate_keypair_returns_valid_keys(self):
        priv, pub = generate_keypair()
        assert len(bytes.fromhex(priv)) == 32
        assert len(bytes.fromhex(pub)) == 32
        assert priv != pub

    def test_keypair_unique(self):
        priv1, _ = generate_keypair()
        priv2, _ = generate_keypair()
        assert priv1 != priv2


class TestLicenseSignAndVerify:
    def test_sign_and_verify_round_trip(self):
        priv, pub = generate_keypair()
        payload = _make_payload()
        key = sign_license_payload(payload, priv)
        license = verify_license_key(key, public_key_hex=pub)
        assert license.valid is True
        assert license.email == "test@example.com"
        assert license.tier == "pro"
        assert license.product == "mtg-deck-engine-pro"

    def test_lifetime_license(self):
        priv, pub = generate_keypair()
        payload = _make_payload(tier="lifetime")
        key = sign_license_payload(payload, priv)
        license = verify_license_key(key, public_key_hex=pub)
        assert license.valid is True
        assert license.expires == "never"
        assert license.is_active() is True
        assert license.grants_pro() is True

    def test_tampered_license_rejected(self):
        priv, pub = generate_keypair()
        payload = _make_payload(email="original@example.com")
        key = sign_license_payload(payload, priv)
        # Tamper with the key (flip a character in the encoded payload)
        tampered = key[:20] + "X" + key[21:]
        license = verify_license_key(tampered, public_key_hex=pub)
        assert license.valid is False

    def test_wrong_public_key_rejected(self):
        priv1, _ = generate_keypair()
        _, pub2 = generate_keypair()
        payload = _make_payload()
        key = sign_license_payload(payload, priv1)
        license = verify_license_key(key, public_key_hex=pub2)
        assert license.valid is False
        assert "signature" in license.error.lower() or "verification" in license.error.lower()

    def test_wrong_product_rejected(self):
        priv, pub = generate_keypair()
        payload = _make_payload()
        payload["product"] = "some-other-product"
        key = sign_license_payload(payload, priv)
        license = verify_license_key(key, public_key_hex=pub)
        assert license.valid is False
        assert "product" in license.error.lower()

    def test_invalid_format_rejected(self):
        license = verify_license_key("not-a-real-key")
        assert license.valid is False
        assert "format" in license.error.lower() or "invalid" in license.error.lower()

    def test_empty_string_rejected(self):
        license = verify_license_key("")
        assert license.valid is False

    def test_expired_license_not_active(self):
        priv, pub = generate_keypair()
        payload = _make_payload(days=-30)  # Expired 30 days ago
        key = sign_license_payload(payload, priv)
        license = verify_license_key(key, public_key_hex=pub)
        assert license.valid is True  # Signature is valid
        assert license.is_active() is False  # But expired
        assert license.grants_pro() is False


class TestLicenseObject:
    def test_active_license_grants_pro(self):
        license = License(
            id="x", email="a@b.com", product="mtg-deck-engine-pro",
            tier="pro", issued="2026-01-01",
            expires=(datetime.now() + timedelta(days=30)).date().isoformat(),
            valid=True,
        )
        assert license.is_active() is True
        assert license.grants_pro() is True

    def test_invalid_license_does_not_grant(self):
        license = License(
            id="x", email="a@b.com", product="mtg-deck-engine-pro",
            tier="pro", issued="2026-01-01", expires="never", valid=False,
        )
        assert license.grants_pro() is False

    def test_lifetime_never_expires(self):
        license = License(
            id="x", email="a@b.com", product="mtg-deck-engine-pro",
            tier="lifetime", issued="2026-01-01", expires="never", valid=True,
        )
        assert license.is_active() is True
        assert license.grants_pro() is True


class TestLicenseFileIO:
    def test_save_and_load_license(self):
        priv, pub = generate_keypair()
        payload = _make_payload()
        key = sign_license_payload(payload, priv)

        # Patch PUBLIC_KEY_HEX and LICENSE_PATH for the test
        tmp_path = Path(tempfile.mkdtemp()) / "license.key"
        with patch("mtg_deck_engine.licensing.PUBLIC_KEY_HEX", pub):
            with patch("mtg_deck_engine.licensing.LICENSE_PATH", tmp_path):
                from mtg_deck_engine.licensing import load_saved_license, save_license
                result = save_license(key)
                assert result.valid is True

                loaded = load_saved_license()
                assert loaded is not None
                assert loaded.valid is True
                assert loaded.email == "test@example.com"

    def test_load_no_license(self):
        tmp_path = Path(tempfile.mkdtemp()) / "no-license.key"
        with patch("mtg_deck_engine.licensing.LICENSE_PATH", tmp_path):
            from mtg_deck_engine.licensing import load_saved_license
            result = load_saved_license()
            assert result is None

    def test_remove_license(self):
        priv, pub = generate_keypair()
        payload = _make_payload()
        key = sign_license_payload(payload, priv)

        tmp_path = Path(tempfile.mkdtemp()) / "license.key"
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        with patch("mtg_deck_engine.licensing.PUBLIC_KEY_HEX", pub):
            with patch("mtg_deck_engine.licensing.LICENSE_PATH", tmp_path):
                from mtg_deck_engine.licensing import remove_license, save_license
                save_license(key)
                assert tmp_path.exists()
                assert remove_license() is True
                assert not tmp_path.exists()
                assert remove_license() is False  # Already gone
