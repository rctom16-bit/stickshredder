"""Key and trust-store locations and the own-key bootstrap in core.config."""

from __future__ import annotations

import core.config as config
from cert.signing import KEY_FILE_NAME, SigningKey
from cert.verify_cert import TrustStore


def test_key_dir_and_trust_file_live_in_the_config_dir():
    import subprocess
    import sys
    from pathlib import Path

    # The autouse fixture redirects them for tests; check the real defaults in
    # a fresh interpreter (importing core.config creates no files).
    src = Path(__file__).resolve().parent.parent / "src"
    code = (
        "import core.config as c;"
        "print(c.KEY_DIR == c.CONFIG_DIR, c.TRUST_FILE == c.CONFIG_DIR / 'trusted_keys.json')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=src, capture_output=True, text=True, check=True
    )
    assert out.stdout.split() == ["True", "True"]


def test_autouse_fixture_keeps_keys_out_of_the_real_profile():
    from pathlib import Path

    home_cfg = Path.home() / ".stickshredder"
    assert config.KEY_DIR != home_cfg
    assert config.TRUST_FILE.parent != home_cfg


def test_load_signing_key_creates_key_and_trusts_it():
    key = config.load_signing_key()
    assert (config.KEY_DIR / KEY_FILE_NAME).exists()
    trusted = TrustStore.load(config.TRUST_FILE).as_dict()
    assert trusted == {key.fingerprint(): config.OWN_KEY_NAME}


def test_load_signing_key_returns_the_same_key_again():
    first = config.load_signing_key()
    second = config.load_signing_key()
    assert first.public_key_bytes() == second.public_key_bytes()


def test_removed_own_key_is_not_re_added_on_later_loads():
    key = config.load_signing_key()
    store = TrustStore.load(config.TRUST_FILE)
    store.remove(key.fingerprint())
    config.load_signing_key()
    assert TrustStore.load(config.TRUST_FILE).as_dict() == {}


def test_damaged_trust_store_does_not_block_key_creation():
    config.TRUST_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.TRUST_FILE.write_text("{not json", "utf-8")
    key = config.load_signing_key()
    assert isinstance(key, SigningKey)
    assert config.TRUST_FILE.read_text("utf-8") == "{not json"


def test_trust_own_key_adds_an_existing_key():
    key = SigningKey.from_seed(bytes(range(1, 33)))
    config.trust_own_key(key)
    assert TrustStore.load(config.TRUST_FILE).as_dict() == {
        "65B6-0673-D6ED-884B-F01C-2C22-2D82-ADA0": config.OWN_KEY_NAME
    }
