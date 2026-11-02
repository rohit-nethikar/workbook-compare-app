"""Regression: a very long diff value used to make one table row taller than a page,
which raised ReportLab's LayoutError and failed the whole PDF export (HTTP 500)."""
import workbook_compare_pdf as pdf


def test_oversized_diff_value_still_builds_pdf():
    big = " ".join(f"Member{i}" for i in range(3000))
    diff = {
        "total": 1,
        "groups": [{
            "key": "groups",
            "title": "Groups & Sets",
            "items": [{"op": "change", "label": "Group: Region", "before": big, "after": big + " X"}],
        }],
    }
    impact = {"findings": [], "doc_category": "Safe"}

    pdf_bytes = pdf.build_report_pdf("Workbook", "Site", diff, impact, [])

    assert pdf_bytes.startswith(b"%PDF")


def test_clip_leaves_short_values_unchanged_and_truncates_long_ones():
    assert pdf._clip("short") == "short"
    assert pdf._clip(None) == ""
    clipped = pdf._clip("x" * (pdf.MAX_CELL_CHARS + 50))
    assert len(clipped) < pdf.MAX_CELL_CHARS + 100
    assert "truncated" in clipped
