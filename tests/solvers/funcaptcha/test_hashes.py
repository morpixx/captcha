from __future__ import annotations

from captcha.solvers.funcaptcha.hashes import feature_hash, ife_hash, khash, md5_hex, salted


def test_empty_input_is_zero():
    assert khash("") == "0" * 32


def test_known_vector_matches_murmur3_x64_128():
    # mmh3.hash128("hello") in the x64/128 variant, seed 0.
    assert khash("hello") == "cbd8a7b341bd9b025b1e906a48ae1d19"
    assert khash("hello", 38) == "a7668c780af43f902e8f9d7d297d1b84"


def test_seed_changes_the_digest():
    assert khash("fe-list", 0) != khash("fe-list", 38)


def test_feature_hash_uses_values_only_and_semicolons():
    fe = ["DNT:unknown", "L:en-US", "H:8"]
    assert feature_hash(fe) == khash("unknown;en-US;8", 0)


def test_ife_hash_keeps_prefixes_and_uses_comma_space():
    fe = ["DNT:unknown", "L:en-US", "H:8"]
    assert ife_hash(fe) == khash("DNT:unknown, L:en-US, H:8", 38)


def test_salted_is_stable_per_salt():
    first = salted("machine_hash", "salt-a")
    assert first == salted("machine_hash", "salt-a")
    assert first != salted("machine_hash", "salt-b")
    assert first == md5_hex("salt-a|machine_hash")
