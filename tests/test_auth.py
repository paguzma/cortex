import base64
import re

from cortex import auth


def test_setup_code_format_and_persistence(tmp_path):
    code = auth.ensure_setup_code(tmp_path / "code")
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", code)
    assert auth.ensure_setup_code(tmp_path / "code") == code


def test_password_hash():
    h = auth.hash_password("una-clave-larga")
    assert auth.verify_password(h, "una-clave-larga")
    assert not auth.verify_password(h, "otra")
    assert not auth.verify_password(None, "x")
    assert not auth.verify_password("basura", "x")


def test_same_rejects_empty():
    assert auth.same("a", "a")
    assert not auth.same("", "")
    assert not auth.same("a", None)


def test_check_bearer():
    assert auth.check_bearer("Bearer tok", "tok")
    assert not auth.check_bearer("Bearer otro", "tok")
    assert not auth.check_bearer("tok", "tok")
    assert not auth.check_bearer(None, "tok")
    assert not auth.check_bearer("Bearer ", "")


def test_check_basic():
    good = "Basic " + base64.b64encode(b"cortex:tok").decode()
    assert auth.check_basic(good, "tok")
    assert not auth.check_basic(good, "otro")
    assert not auth.check_basic("Basic ###", "tok")
    assert not auth.check_basic(good, "")


def test_tokens_are_unique():
    assert auth.new_token() != auth.new_token()
