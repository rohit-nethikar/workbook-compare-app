"""Passcode gate for the local UI (separate from the Tableau PAT). Single-admin app:
one passcode, one session, no user accounts."""
from functools import wraps

from flask import redirect, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import db


def hash_passcode(passcode: str) -> str:
    return generate_password_hash(passcode)


def verify_passcode(passcode: str) -> bool:
    stored_hash = db.get_config("passcode_hash")
    if not stored_hash:
        return False
    return check_password_hash(stored_hash, passcode)


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not db.is_setup_complete():
            return redirect(url_for("setup.setup"))
        if not session.get("authed"):
            return redirect(url_for("auth_routes.login"))
        return view_func(*args, **kwargs)

    return wrapped
