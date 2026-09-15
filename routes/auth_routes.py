from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import db
from auth import verify_passcode

bp = Blueprint("auth_routes", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not db.is_setup_complete():
        return redirect(url_for("setup.setup"))

    if request.method == "GET":
        return render_template("login.html")

    passcode = request.form.get("passcode", "")
    if verify_passcode(passcode):
        session["authed"] = True
        return redirect(url_for("workbook_compare.show_workbook_compare"))

    flash("Incorrect passcode.", "error")
    return redirect(url_for("auth_routes.login"))


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth_routes.login"))
