"""Workbook Compare + Custom View Impact Check.

Strictly read-only: downloads the published workbook's definition WITHOUT its
extract, diffs it against a locally-uploaded candidate .twbx/.twb, and flags which
existing custom views could be affected. Never publishes, overwrites, or deletes
anything on the Tableau Server. See workbook_compare_engine.py for the parsing/diff
logic itself.

Unlike the tableau-admin-dashboard version this was ported from, there is no local
cache of workbooks/custom views - both are fetched live from Tableau on every
request, since that's a single cheap REST call each and this app has no other
feature that would justify keeping a sync/cache pipeline warm. Account numbers for
custom-view owners still come from a local cache (db.py's `account_numbers` table),
since that's sourced from BigQuery, not Tableau, and a full sync there is kept warm
by a background thread started in app.py.
"""
import dataclasses
import io
import re
import shutil
import tempfile
import zipfile

from flask import Blueprint, flash, jsonify, redirect, render_template, request, send_file, url_for

import crypto
import db
import site_context
import tableau_client
import workbook_compare_pdf
from auth import login_required
from config import settings
from custom_view_state import DerivedCustomViewState, compute_state_delta
from workbook_compare_engine import (
    apply_derived_state_evidence,
    build_plain_language_summary,
    classify_custom_view_impact,
    classify_view_impact,
    compute_diff,
    parse_workbook,
)

bp = Blueprint("workbook_compare", __name__)

# Sanity cap on the .twb XML itself (not the .twbx/extract) - a legitimate .twb is at
# most a few MB of XML; this only guards against a malformed/hostile upload.
MAX_TWB_BYTES = 200 * 1024 * 1024


def _credentials():
    pat_name = db.get_config("pat_name")
    pat_encrypted = db.get_config("pat_encrypted")
    if not pat_name or not pat_encrypted:
        raise RuntimeError("No PAT configured - complete /setup first.")
    return pat_name, crypto.decrypt_value(pat_encrypted)


def _read_twb_bytes(file_obj, filename: str) -> bytes:
    """Pulls just the .twb XML out of a .twbx/.twb file object, never touching the
    extract. `file_obj` is either an uploaded FileStorage's `.stream` (which Werkzeug
    already spools to a disk-backed temp file for large uploads, so this never loads
    the full multi-GB upload into memory) or a plain file opened from disk."""
    filename_lower = (filename or "").lower()

    if filename_lower.endswith(".twb"):
        file_obj.seek(0)
        data = file_obj.read(MAX_TWB_BYTES + 1)
        if len(data) > MAX_TWB_BYTES:
            raise ValueError("The .twb file exceeds the accepted size limit.")
        return data

    file_obj.seek(0)
    with zipfile.ZipFile(file_obj) as zf:
        twb_names = [n for n in zf.namelist() if n.lower().endswith(".twb")]
        if not twb_names:
            raise ValueError("No .twb file found inside the .twbx archive.")
        info = zf.getinfo(twb_names[0])
        if info.file_size > MAX_TWB_BYTES:
            raise ValueError("The .twb inside the .twbx exceeds the accepted size limit.")
        return zf.read(twb_names[0])


def _severity_for_classification(classification: str) -> str:
    return {
        "directly_impacted": "High",
        "potentially_impacted": "Medium",
        "label_change_only": "Low",
        "no_change": "None",
    }.get(classification, "None")


def _label_for_classification(classification: str) -> str:
    return {
        "directly_impacted": "Directly impacted",
        "potentially_impacted": "Potentially impacted",
        "label_change_only": "Label change only",
        "no_change": "No change detected",
    }.get(classification, "No change detected")


def _sorted_workbook_dicts(wb_items) -> list:
    return sorted(
        [{"id": wb.id, "name": wb.name, "project_name": wb.project_name} for wb in wb_items],
        key=lambda wb: (wb["name"] or "").lower(),
    )


@bp.route("/workbook-compare", methods=["GET"])
@login_required
def show_workbook_compare():
    site = site_context.get_current_site()
    workbooks = []
    try:
        pat_name, pat_secret = _credentials()
        workbooks = tableau_client.call_with_retry(
            settings.server_url, site, pat_name, pat_secret,
            lambda server: _sorted_workbook_dicts(tableau_client.list_workbooks(server)),
        )
    except Exception as exc:
        flash(f"Could not load the workbook list from Tableau Server: {exc}", "error")
    return render_template("workbook_compare.html", workbooks=workbooks, result=None)


@bp.route("/workbook-compare", methods=["POST"])
@login_required
def run_workbook_compare():
    site = site_context.get_current_site()

    workbook_id = request.form.get("workbook_id", "").strip()
    candidate_file = request.files.get("candidate_file")

    if not workbook_id:
        flash("Select a workbook to compare against.", "error")
        return redirect(url_for("workbook_compare.show_workbook_compare"))

    if not candidate_file or not candidate_file.filename:
        flash("Upload the candidate .twbx or .twb file.", "error")
        return redirect(url_for("workbook_compare.show_workbook_compare"))

    filename_lower = candidate_file.filename.lower()
    if not (filename_lower.endswith(".twbx") or filename_lower.endswith(".twb")):
        flash("Candidate file must be a .twbx or .twb file.", "error")
        return redirect(url_for("workbook_compare.show_workbook_compare"))

    try:
        candidate_twb_bytes = _read_twb_bytes(candidate_file.stream, candidate_file.filename)
    except (ValueError, zipfile.BadZipFile) as exc:
        flash(f"Could not read candidate file: {exc}", "error")
        return redirect(url_for("workbook_compare.show_workbook_compare"))

    def _fetch_from_server(server):
        wb_item = tableau_client.find_workbook_by_id_or_name(server, workbook_id)
        if wb_item is None:
            return None
        published_path = tableau_client.download_workbook_definition(server, wb_item.id, published_tmp_dir)

        # Populate the workbook's views to get their content_url for embedding
        # (needed for live filter/parameter state capture via Embedding API v3)
        server.workbooks.populate_views(wb_item)
        view_content_urls = {view.name: view.content_url for view in (wb_item.views or [])}

        custom_view_rows = tableau_client.list_custom_views(server)
        workbooks = _sorted_workbook_dicts(tableau_client.list_workbooks(server))
        return wb_item.name, published_path, custom_view_rows, workbooks, view_content_urls

    published_tmp_dir = tempfile.mkdtemp(prefix="wbcompare-")
    try:
        pat_name, pat_secret = _credentials()
        fetched = tableau_client.call_with_retry(
            settings.server_url, site, pat_name, pat_secret, _fetch_from_server
        )
        if fetched is None:
            flash("Workbook was not found on the server.", "error")
            return redirect(url_for("workbook_compare.show_workbook_compare"))
        workbook_name, published_path, custom_view_rows, workbooks, view_content_urls = fetched

        with open(published_path, "rb") as f:
            published_twb_bytes = _read_twb_bytes(f, published_path)

    except Exception as exc:
        flash(f"Could not download the published workbook: {exc}", "error")
        return redirect(url_for("workbook_compare.show_workbook_compare"))
    finally:
        shutil.rmtree(published_tmp_dir, ignore_errors=True)

    try:
        published = parse_workbook(published_twb_bytes)
        candidate = parse_workbook(candidate_twb_bytes)
    except Exception as exc:
        flash(f"Could not parse workbook XML: {exc}", "error")
        return redirect(url_for("workbook_compare.show_workbook_compare"))

    diff = compute_diff(published, candidate)
    impact = classify_custom_view_impact(published, candidate, diff)

    # tableau_client.list_custom_views() returns (id, name, workbook_name, view_name,
    # owner_name, shared, created_at, updated_at, last_accessed_at) tuples for every
    # custom view the signed-in PAT can see, site-wide - narrow to this workbook the
    # same way the exact-match SQL in the ported original did.
    matched = [row for row in custom_view_rows if row[2] == workbook_name]

    owner_emails = sorted({row[4] for row in matched if row[4]})
    account_numbers = db.get_account_numbers(owner_emails)

    custom_views = []
    for row in matched:
        _id, name, _workbook_name, view_name, owner_name, shared, created_at, updated_at, last_accessed_at = row
        view_classification = classify_view_impact(view_name, published, candidate, diff, impact["classification"])
        view_content_url = view_content_urls.get(view_name)
        custom_views.append({
            "id": _id,
            "name": name,
            "view_name": view_name,
            "owner_name": owner_name,
            # owner_name is email-formatted on this Tableau site (same assumption the
            # account-number lookup below relies on) - kept as its own field for
            # template parity with the monolith, which has the same duplication.
            "owner_email": owner_name,
            "owner_account_number": account_numbers.get(owner_name, ""),
            "shared": shared,
            "created_at": created_at,
            "updated_at": updated_at,
            "last_accessed_at": last_accessed_at,
            "impact_status": _label_for_classification(view_classification),
            "impact_severity": _severity_for_classification(view_classification),
            "view_content_url": view_content_url,
        })

    plain_summary = build_plain_language_summary(impact, custom_views)

    result = {
        "workbook_name": workbook_name,
        "diff": diff,
        "diff_json": dataclasses.asdict(diff),
        "impact": impact,
        "custom_views": custom_views,
        "plain_summary": plain_summary,
    }

    return render_template("workbook_compare.html", workbooks=workbooks, result=result)


@bp.route("/workbook-compare/export-pdf", methods=["POST"])
@login_required
def export_pdf():
    """Builds the Workbook Compare PDF report from data the client already has
    (the same compare result rendered on the page) and streams it back
    directly - never written to disk on the server."""
    payload = request.get_json(silent=True) or {}
    workbook_name = payload.get("workbook_name") or "workbook"
    site = payload.get("site") or site_context.get_current_site()

    pdf_bytes = workbook_compare_pdf.build_report_pdf(
        workbook_name=workbook_name,
        site=site,
        diff=payload.get("diff") or {},
        impact=payload.get("impact") or {},
        custom_views=payload.get("custom_views") or [],
        plain_summary=payload.get("plain_summary") or {},
    )

    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", workbook_name).strip("_") or "workbook"
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"workbook_compare_{safe_name}.pdf",
    )


@bp.route("/workbook-compare/derived-state", methods=["POST"])
@login_required
def apply_derived_state_to_impact():
    """Accept browser-captured filter/parameter state delta and apply it as evidence
    to strengthen Custom View impact assessment.

    The browser sends:
        {
            "base_state": {
                "filters": [{"field_name": "Region", "worksheet_name": "Sales", "applied_values": ["East", "West"]}],
                "parameters": [{"name": "Year", "current_value": "2024"}]
            },
            "custom_view_state": {
                "filters": [...],
                "parameters": [...]
            }
        }

    Returns:
        {
            "confidence": "confirmed" | "unconfirmed" | "not_checked",
            "note": "human-readable summary of evidence",
            "delta_count": <number of differences between base and custom view>
        }
    """
    try:
        payload = request.get_json() or {}

        # Reconstruct DerivedCustomViewState objects from the captured JSON
        base_data = payload.get("base_state", {})
        cv_data = payload.get("custom_view_state", {})

        base_state = _reconstruct_state_from_json(base_data)
        custom_view_state = _reconstruct_state_from_json(cv_data)

        # Compute the delta
        delta = compute_state_delta(base_state, custom_view_state)

        # Apply evidence
        evidence = apply_derived_state_evidence(delta)
        evidence["delta_count"] = len(delta.deltas)

        return jsonify(evidence), 200

    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


def _reconstruct_state_from_json(state_dict: dict):
    """Convert the browser's JSON representation of state back into DerivedCustomViewState."""
    from custom_view_state import FilterState, ParameterState

    filters = []
    for f in state_dict.get("filters", []):
        filters.append(
            FilterState(
                field_name=f.get("field_name", ""),
                worksheet_name=f.get("worksheet_name", ""),
                applied_values=f.get("applied_values", []),
                filter_type=f.get("filter_type", "categorical"),
            )
        )

    parameters = []
    for p in state_dict.get("parameters", []):
        parameters.append(
            ParameterState(
                name=p.get("name", ""),
                current_value=p.get("current_value"),
            )
        )

    return DerivedCustomViewState(filters=filters, parameters=parameters)
