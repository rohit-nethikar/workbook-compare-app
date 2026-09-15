"""Tests for routes/custom_views.py.

Mirrors tests/test_workbook_compare_routes.py's approach: a throwaway Flask app
registering ONLY routes.custom_views.bp plus stub setup/auth_routes blueprints, so
this never imports the real app.py (which would start the background BigQuery sync
thread and hit the real instance/cache.db).
"""
import pytest
from flask import Blueprint, Flask

import routes.custom_views as cv_routes


def test_unauthenticated_custom_views_redirects_to_login(monkeypatch):
    import auth

    monkeypatch.setattr(auth.db, "is_setup_complete", lambda: True)

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.testing = True
    app.register_blueprint(cv_routes.bp)

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
    response = client.get("/custom-views")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
