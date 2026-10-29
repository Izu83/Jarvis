"""Face ID for permissions: the PC only accepts an Allow signed by the registered key, with the face checked,
for exactly that prompt, from the same site. Signatures are made here the way a phone's authenticator makes them."""
import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from jarvis import faceid

ORIGIN = "https://izu83.github.io"


@pytest.fixture
def phone(tmp_path, monkeypatch):
    monkeypatch.setattr(faceid, "PATH", tmp_path / "faceid.json")
    key = ec.generate_private_key(ec.SECP256R1())
    spki = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.DER,
                                                          serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
    client = faceid.b64u(json.dumps({"type": "webauthn.create", "challenge": "x", "origin": ORIGIN}).encode())
    assert faceid.add("cred1", spki, client, "iPhone") == ""

    def sign(challenge, flags=faceid.UP | faceid.UV, origin=ORIGIN, rp="izu83.github.io", cred="cred1", k=key):
        raw_client = json.dumps({"type": "webauthn.get", "challenge": challenge, "origin": origin}).encode()
        auth = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + b"\0\0\0\1"
        sig = k.sign(auth + hashlib.sha256(raw_client).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": cred, "authenticatorData": faceid.b64u(auth), "clientDataJSON": faceid.b64u(raw_client),
                "signature": faceid.b64u(sig)}
    return sign


def test_a_real_face_checked_answer_passes(phone):
    ch = faceid.challenge()
    assert faceid.enabled() and faceid.ids() == ["cred1"]
    assert faceid.verify(phone(ch), ch)


def test_everything_else_is_refused(phone):
    ch = faceid.challenge()
    assert not faceid.verify(None, ch)                                       # no Face ID at all
    assert not faceid.verify(phone(faceid.challenge()), ch)                  # signed for another prompt
    assert not faceid.verify(phone(ch, flags=faceid.UP), ch)                 # face not checked
    assert not faceid.verify(phone(ch, origin="https://evil.example"), ch)   # another site
    assert not faceid.verify(phone(ch, rp="evil.example"), ch)               # another site's key
    assert not faceid.verify(phone(ch, cred="other"), ch)                    # unknown key
    other = ec.generate_private_key(ec.SECP256R1())
    assert not faceid.verify(phone(ch, k=other), ch)                         # signed by a different key
    tampered = phone(ch)
    tampered["signature"] = faceid.b64u(b"\x30\x06\x02\x01\x01\x02\x01\x01")
    assert not faceid.verify(tampered, ch)


def test_only_one_setup_and_clear(phone):
    assert "already" in faceid.add("cred2", "AAAA", "AAAA")
    faceid.clear()
    assert not faceid.enabled()
