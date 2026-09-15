"""Tests for routes/workbook_compare.py.

Pure unit tests for the module's non-Flask helper functions, plus exactly
one minimal Flask smoke test for the login gate. The smoke test registers
ONLY routes.workbook_compare.bp on a throwaway Flask app - it never imports
the real app.py, which would trigger db.init_db(), scheduler.start(), and a
background BigQuery sync thread against the real instance/cache.db.
"""
import io
import zipfile

import pytest
from flask import Blueprint, Flask

import routes.workbook_compare as wc_routes


def _twb_bytes() -> bytes:
    return b"<workbook></workbook>"


def test_read_twb_bytes_from_plain_twb():
    data = _twb_bytes()
    stream = io.BytesIO(data)
    result = wc_routes._read_twb_bytes(stream, "candidate.twb")
    assert result == data


def test_read_twb_bytes_from_twbx_zip():
    twb_data = _twb_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("candidate.twb", twb_data)
        zf.writestr("Data/Extracts/extract.hyper", b"not-a-real-extract")
    buf.seek(0)

    result = wc_routes._read_twb_bytes(buf, "candidate.twbx")
    assert result == twb_data


def test_read_twb_bytes_oversized_twb_raises(monkeypatch):
    monkeypatch.setattr(wc_routes, "MAX_TWB_BYTES", 10)
    stream = io.BytesIO(b"x" * 11)
    with pytest.raises(ValueError):
        wc_routes._read_twb_bytes(stream, "candidate.twb")


def test_read_twbx_with_no_twb_inside_raises():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Data/Extracts/extract.hyper", b"not-a-real-extract")
    buf.seek(0)

    with pytest.raises(ValueError):
        wc_routes._read_twb_bytes(buf, "candidate.twbx")


def test_read_twbx_corrupt_zip_raises():
    stream = io.BytesIO(b"this is not a zip file at all")
    with pytest.raises(zipfile.BadZipFile):
        wc_routes._read_twb_bytes(stream, "candidate.twbx")


@pytest.mark.parametrize("classification,expected", [
    ("directly_impacted", "High"),
    ("potentially_impacted", "Medium"),
    ("label_change_only", "Low"),
    ("no_change", "None"),
    ("something_unknown", "None"),
])
def test_severity_for_classification(classification, expected):
    assert wc_routes._severity_for_classification(classification) == expected


@pytest.mark.parametrize("classification,expected", [
    ("directly_impacted", "Directly impacted"),
    ("potentially_impacted", "Potentially impacted"),
    ("label_change_only", "Label change only"),
    ("no_change", "No change detected"),
    ("something_unknown", "No change detected"),
])
def test_label_for_classification(classification, expected):
    assert wc_routes._label_for_classification(classification) == expected


def test_unauthenticated_workbook_compare_redirects_to_login(monkeypatch):
    """Proves the blueprint/route/login_required wiring works without
    stubbing the ~28 other nav/logout/site endpoints that base.html would
    otherwise require via url_for on an authenticated render."""
    import auth

    monkeypatch.setattr(auth.db, "is_setup_complete", lambda: True)

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.testing = True
    app.register_blueprint(wc_routes.bp)

    setup_bp = Blueprint("setup", __name__)

    @setup_bp.route("/setup")
    def setup():
        return "setup", 200

    auth_routes_bp = Blueprint("auth_routes", __name__)

    @auth_routes_bp.route("/login")
    def login():
        return "login", 200

    app.register_blueprint(setup_bp)
    app.register_blueprint(auth_routes_bp)

    client = app.test_client()
    response = client.get("/workbook-compare")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
