"""Site-wide Custom Views directory.

tableau_client.list_custom_views() already returns every custom view the signed-in
PAT can see, site-wide - Workbook Compare only ever narrows that down to one
workbook. This page just shows the unfiltered list, browsable/searchable via the
same DataTables search box every other page uses - which incidentally also covers
"which workbooks does this account touch" (search by account number or email) and
"what does this person own" (search by owner name) for free.

Strictly read-only, like the rest of this app - never publishes, overwrites, or
deletes anything on the Tableau Server.
"""
from flask import Blueprint, flash, render_template

import crypto
import db
import site_context
import tableau_client
from auth import login_required
from config import settings

bp = Blueprint("custom_views", __name__)


def _credentials():
    pat_name = db.get_config("pat_name")
    pat_encrypted = db.get_config("pat_encrypted")
    if not pat_name or not pat_encrypted:
        raise RuntimeError("No PAT configured - complete /setup first.")
    return pat_name, crypto.decrypt_value(pat_encrypted)


@bp.route("/custom-views", methods=["GET"])
@login_required
def list_custom_views():
    site = site_context.get_current_site()
    custom_views = []
    try:
        pat_name, pat_secret = _credentials()
        with tableau_client.signed_in_server(settings.server_url, site, pat_name, pat_secret) as server:
            rows = tableau_client.list_custom_views(server)

        owner_emails = sorted({row[4] for row in rows if row[4]})
        account_numbers = db.get_account_numbers(owner_emails)

        for row in rows:
            _id, name, workbook_name, view_name, owner_name, shared, created_at, updated_at, last_accessed_at = row
            custom_views.append({
                "id": _id,
                "name": name,
                "workbook_name": workbook_name,
                "view_name": view_name,
                "owner_name": owner_name,
                "owner_email": owner_name,
                "owner_account_number": account_numbers.get(owner_name, ""),
                "shared": shared,
                "created_at": created_at,
                "updated_at": updated_at,
                "last_accessed_at": last_accessed_at,
            })
    except Exception as exc:
        flash(f"Could not load custom views from Tableau Server: {exc}", "error")

    return render_template("custom_views.html", custom_views=custom_views)
