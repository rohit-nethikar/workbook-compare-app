import os
import threading
from urllib.parse import urlparse

from flask import Flask, redirect, request, session, url_for
from waitress import serve

import db
import site_context
from config import INSTANCE_DIR, settings

FLASK_SECRET_PATH = os.path.join(INSTANCE_DIR, "flask_secret.key")
_account_sync_lock = threading.Lock()


def _load_or_create_flask_secret() -> bytes:
    if os.path.exists(FLASK_SECRET_PATH):
        with open(FLASK_SECRET_PATH, "rb") as f:
            return f.read()
    secret = os.urandom(32)
    with open(FLASK_SECRET_PATH, "wb") as f:
        f.write(secret)
    return secret


def _sync_account_numbers_background():
    """Refreshes db.py's `account_numbers` cache from BigQuery once at startup, in a
    background thread so it never blocks Waitress from accepting requests. See
    bigquery_sync.py - this app has no scheduler, so this is the only time the cache
    refreshes; restart the app to pick up new BigQuery data."""
    if not _account_sync_lock.acquire(blocking=False):
        return
    try:
        import bigquery_sync

        result = bigquery_sync.sync_account_numbers(db)
        if result["status"] == "success":
            print(f"Account number sync successful: {result['message']}")
        else:
            print(f"Account number sync failed: {result['message']}")
    except Exception as error:
        print(f"WARNING: Background account number sync failed: {error}")
        import traceback
        traceback.print_exc()
    finally:
        _account_sync_lock.release()


def _start_account_number_sync_async():
    thread = threading.Thread(
        target=_sync_account_numbers_background,
        name="account-number-sync",
        daemon=True,
    )
    thread.start()
    return thread


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or _load_or_create_flask_secret()

    db.init_db()

    from routes import auth_routes, custom_views, setup, workbook_compare

    app.register_blueprint(setup.bp)
    app.register_blueprint(auth_routes.bp)
    app.register_blueprint(workbook_compare.bp)
    app.register_blueprint(custom_views.bp)

    @app.context_processor
    def inject_site_context():
        return {
            "current_site": site_context.get_current_site(),
            "available_sites": settings.sites,
            "server_host": urlparse(settings.server_url).netloc or settings.server_url,
        }

    @app.route("/switch-site", methods=["POST"])
    def switch_site():
        site_context.set_current_site(request.form.get("site", ""))
        return redirect(url_for("workbook_compare.show_workbook_compare"))

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}, 200

    @app.route("/")
    def index():
        if session.get("authed"):
            return redirect(url_for("workbook_compare.show_workbook_compare"))
        if db.is_setup_complete():
            return redirect(url_for("auth_routes.login"))
        return redirect(url_for("setup.setup"))

    _start_account_number_sync_async()

    return app


app = create_app()

if __name__ == "__main__":
    print("Starting Tableau Workbook Compare (HTTP-only mode)")
    # Waitress defaults to a 1 GiB request body cap, which rejects a multi-GB .twbx
    # upload before it ever reaches Flask. Raise it.
    serve(app, host=settings.host, port=settings.port, max_request_body_size=10 * 1024 ** 3)
