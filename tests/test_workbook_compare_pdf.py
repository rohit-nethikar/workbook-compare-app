"""Tests for workbook_compare_pdf.py. Structural assertions only - there is
no PDF-text-extraction library in requirements.txt, and reportlab's
compressed content streams make substring search on raw bytes unreliable.
Each case just proves build_report_pdf() returns a well-formed PDF without
raising, across the shapes it can realistically be called with."""
from workbook_compare_pdf import build_report_pdf


def _assert_pdf_bytes(data: bytes):
    assert isinstance(data, bytes)
    assert data.startswith(b"%PDF")
    assert len(data) > 500


def test_build_report_pdf_populated_result():
    diff = {
        "groups": [
            {
                "key": "views",
                "title": "Views",
                "items": [
                    {"op": "remove", "label": "View: Sales Summary", "before": None, "after": None, "parts": []},
                    {"op": "add", "label": "View: Sales Summary v2", "before": None, "after": None, "parts": []},
                ],
            },
            {
                "key": "calcs",
                "title": "Calculated Fields",
                "items": [
                    {
                        "op": "change",
                        "label": "Calculation: [Calculation_1]",
                        "before": "SUM([Sales])",
                        "after": "SUM([Sales]) * 1.1",
                        "parts": [],
                    },
                ],
            },
        ],
        "counts": {"views_added": 1, "views_removed": 1, "calcs_changed": 1},
        "total": 3,
    }
    impact = {
        "risk_level": "High",
        "findings": [
            {
                "category": "Views",
                "severity": "High",
                "title": 'Base view "Sales Summary" removed or renamed',
                "detail": "Custom views tied to this view will be orphaned or unloadable.",
                "sheets": ["Sales Summary"],
            },
        ],
        "classification": "directly_impacted",
    }
    custom_views = [
        {
            "name": "My View", "view_name": "Sales Summary", "owner_name": "Jane Doe",
            "owner_email": "jane@example.com", "owner_account_number": "12345", "shared": True,
            "impact_status": "Directly impacted", "impact_severity": "High",
            "created_at": "2026-01-01", "updated_at": "2026-01-02", "last_accessed_at": "2026-01-03",
        },
        {
            "name": "No Email View", "view_name": "Sales Summary", "owner_name": "No Email",
            "owner_email": None, "owner_account_number": None, "shared": False,
            "impact_status": "Directly impacted", "impact_severity": "High",
            "created_at": None, "updated_at": None, "last_accessed_at": None,
        },
    ]

    pdf_bytes = build_report_pdf("Orders", "InteractiveDashboards", diff, impact, custom_views)
    _assert_pdf_bytes(pdf_bytes)


def test_build_report_pdf_empty_result_does_not_raise():
    pdf_bytes = build_report_pdf("Empty Workbook", "SiteA", {}, {}, [])
    _assert_pdf_bytes(pdf_bytes)


def test_build_report_pdf_escapes_hostile_text():
    """Regression guard on the escape() call in _text/_cell - names/details
    containing markup-like characters must never raise, and must never be
    interpreted as reportlab Paragraph markup."""
    diff = {
        "groups": [
            {
                "key": "views",
                "title": "Views",
                "items": [
                    {
                        "op": "remove",
                        "label": 'View: <script>alert(1)</script> & "quote"',
                        "before": None, "after": None, "parts": [],
                    },
                ],
            },
        ],
        "counts": {},
        "total": 1,
    }
    impact = {
        "risk_level": "High",
        "findings": [
            {
                "category": "Views",
                "severity": "High",
                "title": '<b>bold</b> & "quote" \'',
                "detail": "<script>alert(1)</script>",
                "sheets": ["<sheet>"],
            },
        ],
        "classification": "directly_impacted",
    }
    custom_views = [
        {
            "name": "<script>alert(1)</script>", "view_name": "&\"'<>", "owner_name": None,
            "owner_email": None, "owner_account_number": None, "shared": False,
            "impact_status": "Directly impacted", "impact_severity": "High",
            "created_at": None, "updated_at": None, "last_accessed_at": None,
        },
    ]

    pdf_bytes = build_report_pdf('Wb <script>&"\'', "SiteA", diff, impact, custom_views)
    _assert_pdf_bytes(pdf_bytes)


def test_build_report_pdf_with_plain_summary():
    plain_summary = {
        "headline": "Yes - 1 of 1 existing custom view(s) will likely be affected by this change.",
        "tone": "danger",
        "reasons": [
            {
                "category": "Views",
                "severity": "High",
                "text": "A sheet or dashboard was renamed or removed.",
            },
        ],
        "view_counts": {"directly_impacted": 1, "potentially_impacted": 0, "label_change_only": 0, "no_change": 0},
        "total_views": 1,
    }
    pdf_bytes = build_report_pdf(
        "Orders", "SiteA", {}, {}, [], plain_summary=plain_summary
    )
    _assert_pdf_bytes(pdf_bytes)


def test_build_report_pdf_without_plain_summary_does_not_raise():
    """plain_summary is optional - older cached JSON payloads in a client's
    browser (from before this field existed) must still render."""
    pdf_bytes = build_report_pdf("Orders", "SiteA", {}, {}, [])
    _assert_pdf_bytes(pdf_bytes)


def test_build_report_pdf_unknown_severity_does_not_raise():
    """A severity value absent from _ROW_TINTS must fall back gracefully
    (no tint) rather than raising a KeyError."""
    impact = {
        "risk_level": "Critical",
        "findings": [
            {
                "category": "Fields", "severity": "Critical", "title": "Unknown severity finding",
                "detail": "detail", "sheets": [],
            },
        ],
        "classification": "potentially_impacted",
    }
    custom_views = [
        {
            "name": "V", "view_name": "V", "owner_name": "X", "owner_email": "x@example.com",
            "owner_account_number": "1", "shared": True, "impact_status": "Potentially impacted",
            "impact_severity": "Critical", "created_at": None, "updated_at": None, "last_accessed_at": None,
        },
    ]
    pdf_bytes = build_report_pdf("Wb", "SiteA", {}, impact, custom_views)
    _assert_pdf_bytes(pdf_bytes)
