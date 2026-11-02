"""Mapping of findings to the four deployment risk categories in
'Tableau Custom View Impact Findings' (Safe, Regression-test required, At-risk,
Destructive)."""
import pytest

from workbook_compare_engine import (
    DOC_AT_RISK,
    DOC_DESTRUCTIVE,
    DOC_REGRESSION,
    DOC_SAFE,
    doc_category_for_finding,
    view_doc_category,
    worst_doc_category,
)


@pytest.mark.parametrize("category,title,expected", [
    # Destructive: a dependency is removed or the originating view is replaced
    ("Views", 'Base view "Sales" removed or renamed', DOC_DESTRUCTIVE),
    ("Fields", 'Field "Region" removed', DOC_DESTRUCTIVE),
    ("Parameters", 'Parameter "Year" removed', DOC_DESTRUCTIVE),
    ("Filters", 'Filter on removed field(s) in "Overview"', DOC_DESTRUCTIVE),
    ("Groups & Sets", 'Group/Set "Top" removed', DOC_DESTRUCTIVE),
    ("Groups & Sets", 'Computed set "Hot" removed', DOC_DESTRUCTIVE),
    # At-risk: saved state may reset, map differently, or behave unexpectedly
    ("Fields", 'Field "Sales" datatype changed', DOC_AT_RISK),
    ("Fields", 'Field "Sales" role changed', DOC_AT_RISK),
    ("Parameters", 'Parameter "Year" changed', DOC_AT_RISK),
    ("Data Sources", 'Data source "ds" connection changed', DOC_AT_RISK),
    ("Filters", 'Filter card removed in "Overview"', DOC_AT_RISK),
    ("Data Source Filters", 'Row-level security filter removed from "ds"', DOC_AT_RISK),
    # Regression-test required: output may change
    ("Calculations", 'Calculation "Margin" now references different fields', DOC_REGRESSION),
    ("Filters", 'Filter condition changed in "Overview"', DOC_REGRESSION),
    ("Filters", 'New filter added in "Overview"', DOC_REGRESSION),
    ("Groups & Sets", 'Group/Set "Top" membership changed', DOC_REGRESSION),
    ("Marks", 'Mark type changed in "Overview"', DOC_REGRESSION),
    ("Sorts", 'Sort order changed in "Overview"', DOC_REGRESSION),
    ("Encodings", 'Shelf/encoding pills changed in "Overview"', DOC_REGRESSION),
    ("Fields", 'Field "Sales" default aggregation changed', DOC_REGRESSION),
    # Safe: labels and additions that leave saved state alone
    ("Fields", 'Field "Sales" caption changed', DOC_SAFE),
    ("Parameters", 'Parameter "Year" caption changed', DOC_SAFE),
    ("Parameters", 'New parameter "Region" added', DOC_SAFE),
    ("Views", 'New view "Extra" added', DOC_SAFE),
    ("Groups & Sets", 'New Group/Set "Top" added', DOC_SAFE),
    ("Sheet Visibility", 'Sheet "Overview" is now visible', DOC_SAFE),
])
def test_finding_maps_to_document_category(category, title, expected):
    assert doc_category_for_finding({"category": category, "title": title}) == expected


def test_unmatched_finding_falls_back_to_at_risk():
    finding = {"category": "Something New", "title": "Unknown change"}
    assert doc_category_for_finding(finding) == DOC_AT_RISK


def test_worst_category_wins():
    assert worst_doc_category([DOC_SAFE, DOC_AT_RISK, DOC_REGRESSION]) == DOC_AT_RISK
    assert worst_doc_category([DOC_DESTRUCTIVE, DOC_SAFE]) == DOC_DESTRUCTIVE


def test_no_findings_is_safe():
    assert worst_doc_category([]) == DOC_SAFE


def test_view_category_only_counts_its_own_sheet_and_workbook_wide_findings():
    impact = {
        "findings": [
            {"category": "Views", "title": 'Base view "Sales" removed or renamed',
             "sheets": ["Sales"], "doc_category": DOC_DESTRUCTIVE},
            {"category": "Filters", "title": 'Filter condition changed in "Overview"',
             "sheets": ["Overview"], "doc_category": DOC_REGRESSION},
            {"category": "Fields", "title": 'Field "Sales" datatype changed',
             "sheets": [], "doc_category": DOC_AT_RISK},
        ]
    }
    # Sales: its own removal, plus the workbook-wide datatype change
    assert view_doc_category("Sales", impact) == DOC_DESTRUCTIVE
    # Overview: only the regression on its sheet and the workbook-wide at-risk change
    assert view_doc_category("Overview", impact) == DOC_AT_RISK
    # Unrelated view: only the workbook-wide change applies
    assert view_doc_category("Other", impact) == DOC_AT_RISK


def test_view_with_no_applicable_findings_is_safe():
    impact = {
        "findings": [
            {"category": "Filters", "title": 'Filter condition changed in "Overview"',
             "sheets": ["Overview"], "doc_category": DOC_REGRESSION},
        ]
    }
    assert view_doc_category("Other", impact) == DOC_SAFE
