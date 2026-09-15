"""Builds an in-memory PDF report for a Workbook Compare result.

Takes the same diff/impact/custom_views data already shown on the
workbook_compare.html page and lays it out as a document, so it can be
reviewed before publishing and used to identify which custom-view owners to
notify. Generated entirely in memory (io.BytesIO) - never written to disk.
Report only: this module does not send anything anywhere.
"""
import io
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_RISK_COLORS = {
    "High": colors.HexColor("#dc3545"),
    "Medium": colors.HexColor("#e8a100"),
    "Low": colors.HexColor("#6c757d"),
    "None": colors.HexColor("#198754"),
}

_ROW_TINTS = {
    "High": colors.HexColor("#f8d7da"),
    "Medium": colors.HexColor("#fff3cd"),
    "Low": colors.HexColor("#e2e3e5"),
}

_OP_LABELS = {"add": "+ Added", "remove": "- Removed", "change": "~ Changed"}

_HEADER_STYLE = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#343a40")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dee2e6")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
    ]
)


def _text(value) -> str:
    if value is None:
        return ""
    return escape(str(value))


def _cell(value, style) -> Paragraph:
    return Paragraph(_text(value), style)


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("SmallBody", parent=styles["BodyText"], fontSize=8, leading=10))
    return styles


def _risk_banner(risk_level: str, total_changes: int, custom_view_count: int, styles) -> list:
    color = _RISK_COLORS.get(risk_level, colors.grey)
    banner = Table([[f"Overall Risk: {risk_level}"]], colWidths=[6.8 * inch])
    banner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), color),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 13),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    summary = Paragraph(
        f"<b>{total_changes}</b> structural change(s) detected between the uploaded candidate "
        f"and the published workbook &mdash; <b>{custom_view_count}</b> existing custom view(s) "
        f"reference this workbook.",
        styles["BodyText"],
    )
    return [banner, Spacer(1, 0.12 * inch), summary, Spacer(1, 0.25 * inch)]


_TONE_COLORS = {
    "danger": colors.HexColor("#dc3545"),
    "warning": colors.HexColor("#e8a100"),
    "info": colors.HexColor("#0dcaf0"),
    "success": colors.HexColor("#198754"),
}


def _plain_summary_section(plain_summary: dict, styles) -> list:
    """Renders the jargon-free "will this affect my custom views" summary as
    the first thing after the title - for a reader who never gets to the
    technical Findings/Diff tables below."""
    if not plain_summary:
        return []

    story = [Paragraph("Plain-Language Summary", styles["Heading2"])]

    color = _TONE_COLORS.get(plain_summary.get("tone"), colors.grey)
    headline = Table([[_text(plain_summary.get("headline") or "")]], colWidths=[6.8 * inch])
    headline.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), color),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 11),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.append(headline)
    story.append(Spacer(1, 0.12 * inch))

    reasons = plain_summary.get("reasons") or []
    if reasons:
        story.append(Paragraph("Why:", styles["BodyText"]))
        for r in reasons:
            story.append(Paragraph(f"&bull; {_text(r.get('text'))}", styles["SmallBody"]))
    else:
        story.append(
            Paragraph(
                "Nothing about this candidate file is different from what's currently "
                "published, so there's nothing that could change how a saved custom view "
                "looks or behaves.",
                styles["BodyText"],
            )
        )
    story.append(Spacer(1, 0.25 * inch))
    return story


def _findings_section(findings: list, styles) -> list:
    story = [Paragraph("Custom View Impact Findings", styles["Heading2"])]
    if not findings:
        story.append(
            Paragraph(
                "No structural changes that would affect saved custom view state were detected.",
                styles["BodyText"],
            )
        )
        story.append(Spacer(1, 0.25 * inch))
        return story

    rows = [["Severity", "Category", "Finding", "Detail"]]
    for f in findings:
        sheets = f.get("sheets") or []
        detail = f.get("detail") or ""
        if sheets:
            detail = f"{detail} (Sheets: {', '.join(str(s) for s in sheets)})"
        rows.append(
            [
                f.get("severity", ""),
                _cell(f.get("category", ""), styles["SmallBody"]),
                _cell(f.get("title", ""), styles["SmallBody"]),
                _cell(detail, styles["SmallBody"]),
            ]
        )

    table = Table(rows, colWidths=[0.7 * inch, 0.9 * inch, 2.0 * inch, 3.2 * inch], repeatRows=1)
    style_cmds = list(_HEADER_STYLE.getCommands())
    for i, f in enumerate(findings, start=1):
        tint = _ROW_TINTS.get(f.get("severity"))
        if tint:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), tint))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)
    story.append(Spacer(1, 0.25 * inch))
    return story


def _diff_item_detail(item: dict) -> str:
    parts = item.get("parts") or []
    if parts:
        return "; ".join(
            f"{p.get('attr')}: {p.get('before') or '(none)'} → {p.get('after') or '(none)'}"
            for p in parts
        )
    before = item.get("before")
    after = item.get("after")
    if before is not None or after is not None:
        return f"{before or '(none)'} → {after or '(none)'}"
    return ""


def _diff_section(groups: list, styles) -> list:
    story = [Paragraph("Structural Diff", styles["Heading2"])]
    if not groups:
        story.append(
            Paragraph(
                "No differences found &mdash; the candidate matches the published workbook.",
                styles["BodyText"],
            )
        )
        return story

    for group in groups:
        items = group.get("items") or []
        story.append(Paragraph(f"{_text(group.get('title'))} ({len(items)})", styles["Heading3"]))
        rows = [["Change", "Item", "Detail"]]
        for item in items:
            rows.append(
                [
                    _OP_LABELS.get(item.get("op"), item.get("op") or ""),
                    _cell(item.get("label"), styles["SmallBody"]),
                    _cell(_diff_item_detail(item), styles["SmallBody"]),
                ]
            )
        table = Table(rows, colWidths=[0.9 * inch, 2.5 * inch, 3.4 * inch], repeatRows=1)
        table.setStyle(_HEADER_STYLE)
        story.append(table)
        story.append(Spacer(1, 0.15 * inch))
    return story


def _custom_views_section(custom_views: list, styles) -> list:
    story = [
        Paragraph("Existing Custom Views — Owners to Notify Before Publishing", styles["Heading2"])
    ]
    if not custom_views:
        story.append(Paragraph("No custom views found for this workbook.", styles["BodyText"]))
        return story

    rows = [["Custom View", "Base View", "Owner Email", "Account #", "Shared?", "Impact"]]
    for cv in custom_views:
        owner = cv.get("owner_email") or cv.get("owner_name") or ""
        rows.append(
            [
                _cell(cv.get("name"), styles["SmallBody"]),
                _cell(cv.get("view_name") or "", styles["SmallBody"]),
                _cell(owner, styles["SmallBody"]),
                _text(cv.get("owner_account_number") or "—"),
                "Shared" if cv.get("shared") else "Private",
                _cell(cv.get("impact_status") or "", styles["SmallBody"]),
            ]
        )

    table = Table(
        rows,
        colWidths=[1.5 * inch, 1.2 * inch, 1.9 * inch, 0.8 * inch, 0.7 * inch, 1.3 * inch],
        repeatRows=1,
    )
    style_cmds = list(_HEADER_STYLE.getCommands())
    for i, cv in enumerate(custom_views, start=1):
        tint = _ROW_TINTS.get(cv.get("impact_severity"))
        if tint:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), tint))
    table.setStyle(TableStyle(style_cmds))
    story.append(table)
    story.append(Spacer(1, 0.1 * inch))
    story.append(
        Paragraph(
            "Rows highlighted in red/amber/grey reference views whose structure changed "
            "(High/Medium/Low impact respectively) &mdash; their owners should be notified "
            "before this workbook is published.",
            styles["SmallBody"],
        )
    )
    return story


def build_report_pdf(
    workbook_name: str,
    site: str,
    diff: dict,
    impact: dict,
    custom_views: list,
    plain_summary: dict = None,
) -> bytes:
    """Build the full Workbook Compare PDF report and return its bytes.

    `diff`, `impact`, and `custom_views` are plain dicts/lists (JSON-shaped),
    matching what the workbook_compare.html page already renders on screen.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        title=f"Workbook Compare Report - {workbook_name}",
    )
    styles = _styles()

    story = [
        Paragraph("Workbook Compare Report", styles["Title"]),
        Paragraph(_text(workbook_name), styles["Heading2"]),
        Paragraph(
            f"Site: {_text(site or '-')} &nbsp;|&nbsp; "
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            styles["SmallBody"],
        ),
        Spacer(1, 0.2 * inch),
    ]

    impact = impact or {}
    story.extend(_plain_summary_section(plain_summary or {}, styles))
    story.extend(
        _risk_banner(
            impact.get("risk_level", "None"),
            (diff or {}).get("total", 0),
            len(custom_views or []),
            styles,
        )
    )
    story.extend(_findings_section(impact.get("findings") or [], styles))
    story.extend(_diff_section((diff or {}).get("groups") or [], styles))
    story.append(PageBreak())
    story.extend(_custom_views_section(custom_views or [], styles))

    doc.build(story)
    return buf.getvalue()
