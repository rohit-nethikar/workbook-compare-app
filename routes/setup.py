from flask import Blueprint, flash, redirect, render_template, request, url_for

import crypto
import db
import site_context
import tableau_client
from auth import hash_passcode
from config import settings

bp = Blueprint("setup", __name__)


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    if request.method == "GET":
        return render_template(
            "setup.html",
            server_url=settings.server_url,
            sites=settings.sites,
            already_configured=db.is_setup_complete(),
        )

    pat_name = request.form.get("pat_name", "").strip()
    pat_secret = request.form.get("pat_secret", "").strip()
    passcode = request.form.get("passcode", "").strip()
    passcode_confirm = request.form.get("passcode_confirm", "").strip()

    if not pat_name or not pat_secret:
        flash("Personal Access Token name and secret are both required.", "error")
        return redirect(url_for("setup.setup"))

    if not passcode or passcode != passcode_confirm:
        flash("Passcode is required and must match its confirmation.", "error")
        return redirect(url_for("setup.setup"))

    # Validate against the real server before saving anything.
    try:
        with tableau_client.signed_in_server(
            settings.server_url, settings.default_site, pat_name, pat_secret
        ):
            pass
    except Exception as exc:
        flash(f"Could not sign in to Tableau Server with those credentials: {exc}", "error")
        return redirect(url_for("setup.setup"))

    db.set_config("pat_name", pat_name)
    db.set_config("pat_encrypted", crypto.encrypt_value(pat_secret))
    db.set_config("passcode_hash", hash_passcode(passcode))
    site_context.set_current_site(settings.default_site)

    flash("Setup complete. Log in with your passcode to continue.", "success")
    return redirect(url_for("auth_routes.login"))
