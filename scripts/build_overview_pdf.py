import os
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "Workbook_Compare_Developer_Overview.pdf")

ACCENT = colors.HexColor("#1F4E79")
LIGHT = colors.HexColor("#EAF1F8")
GRID = colors.HexColor("#B7C9DB")

styles = getSampleStyleSheet()
TITLE = ParagraphStyle("T", parent=styles["Title"], fontSize=24, leading=28, textColor=ACCENT, spaceAfter=6)
SUB = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=11, leading=15, textColor=colors.HexColor("#555555"), spaceAfter=14)
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=15, leading=19, textColor=ACCENT, spaceBefore=14, spaceAfter=6)
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=11.5, leading=15, textColor=colors.black, spaceBefore=8, spaceAfter=3)
BODY = ParagraphStyle("B", parent=styles["Normal"], fontSize=10, leading=14, spaceAfter=6)
BULLET = ParagraphStyle("BL", parent=BODY, leftIndent=14, bulletIndent=2, spaceAfter=3)
CELL = ParagraphStyle("C", parent=BODY, fontSize=8.8, leading=11.5, spaceAfter=0)
CELL_H = ParagraphStyle("CH", parent=CELL, textColor=colors.white, fontName="Helvetica-Bold")
NOTE = ParagraphStyle("N", parent=BODY, fontSize=9, leading=12.5, textColor=colors.HexColor("#444444"),
                      backColor=LIGHT, borderPadding=6, spaceBefore=4, spaceAfter=10)


def p(text, style=BODY):
    return Paragraph(text, style)


def bullets(items):
    return [Paragraph(i, BULLET, bulletText="\u2022") for i in items]


def table(rows, widths):
    data = [[Paragraph(c, CELL_H) for c in rows[0]]]
    data += [[Paragraph(c, CELL) for c in r] for r in rows[1:]]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#777777"))
    canvas.drawString(0.75 * inch, 0.5 * inch, "Workbook Compare: Developer Overview")
    canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


story = []

story += [
    p("Workbook Compare", TITLE),
    p("Developer overview: what the app does, why it exists, and how it is built", SUB),
    p("<b>Written for:</b> developers joining the project who need to understand its purpose, "
      "architecture and day-to-day operation.", NOTE),
]

# 1. Purpose
story += [p("1. Purpose", H1),
          p("Workbook Compare is an internal Flask web app that answers one question before a Tableau "
            "workbook change is published: <b>what will this change affect?</b>"),
          p("A developer uploads the proposed (candidate) version of a workbook. The app downloads the "
            "version currently live on Tableau Server (the published version), compares the two, and reports "
            "what changed. It then identifies the Custom Views built on that workbook, meaning the saved "
            "personal views that could break, along with their owners, so those owners can be notified."),
          p("The app is <b>strictly read-only</b> with respect to Tableau. It never publishes, overwrites or "
            "deletes workbooks or Custom Views on the server."),
          ]

# 2. Who / when
story += [p("2. Who uses it and when", H1)]
story += bullets([
    "<b>Workbook developers and reviewers</b>, before publishing a change to a workbook.",
    "<b>Site or content owners</b>, who need to know which Custom View owners to contact.",
    "<b>Anyone browsing Custom Views</b> across the site, using the read-only directory page.",
])

# 3. Workflow
story += [p("3. How a compare works", H1)]
story += bullets([
    "<b>Sign in.</b> The user enters the dashboard passcode. Only a hash of it is stored.",
    "<b>Pick a workbook.</b> The workbook list is fetched live from the Tableau site currently selected.",
    "<b>Upload the candidate.</b> A .twb or .twbx file. A .twbx is a zip archive, so the app extracts the .twb from it.",
    "<b>Download the published version.</b> The app pulls the workbook definition from Tableau and parses it.",
    "<b>Parse and diff.</b> Both files are parsed from XML into a normalized structure, then compared.",
    "<b>Classify impact.</b> The workbook is classified overall, then each Custom View is classified against the diff.",
    "<b>Resolve owners.</b> Owner emails are looked up in a local cache to add account numbers.",
    "<b>Review and export.</b> The results page shows the diff and impacts. A PDF report can be generated "
    "in memory and downloaded.",
])

# 4. Features
story += [p("4. What the diff covers", H1),
          p("The engine (<i>workbook_compare_engine.py</i>) extracts and compares the following from the workbook XML:"),
          ]
story += bullets([
    "Worksheets, dashboards and hidden sheets",
    "Fields, including calculations, groups, sets and computed sets",
    "Parameters and their values",
    "Filters on worksheets and data sources, and quick-filter card visibility",
    "Marks, encodings and sorts per worksheet",
    "Dashboard layout zones",
    "Data connections, including server, port and class",
    "Actions (filter, highlight, URL and parameter)",
])
story += [p("Some items are marked best-effort in the code. Computed sets, for example, depend on a class string "
            "that has not been verified against a real Tableau export. If that string differs, the item degrades "
            "to 'not detected' rather than failing.", NOTE)]

# 5. Architecture
story += [p("5. Architecture", H1),
          p("The app is a single Flask process served by Waitress. Code is organized as follows:")]
arch = [
    ["File / folder", "Responsibility"],
    ["app.py", "App factory. Creates the Flask app, registers blueprints, the health check and the site switcher. Starts the background account-number sync. Serves with Waitress, with a 10 GB request cap for large uploads."],
    ["config.py / config.yaml", "Non-secret settings: server URL, host, port, site list and default site. Environment variables override them."],
    ["db.py", "SQLite store under instance\\ (cache.db). Holds app_config (encrypted PAT, PAT name, passcode hash) and account_numbers."],
    ["crypto.py", "Encrypts and decrypts the PAT at rest."],
    ["auth.py", "Passcode hashing and verification, plus the login_required decorator."],
    ["site_context.py", "Tracks the currently selected Tableau site for the session."],
    ["tableau_client.py", "Wrapper around tableauserverclient. Signs in, lists workbooks and Custom Views, downloads definitions, retries transient failures."],
    ["workbook_compare_engine.py", "Parses .twb XML, computes the diff, and classifies custom-view impact. The core logic, and the largest module."],
    ["custom_view_state.py", "Filter and parameter state normalization and delta logic. Partly built; see Section 8."],
    ["workbook_compare_pdf.py", "Builds the PDF report in memory with reportlab. Sends nothing anywhere."],
    ["bigquery_sync.py", "Pulls the email to account-number map from BigQuery into the local cache."],
    ["routes/", "HTTP endpoints: setup.py, auth_routes.py, workbook_compare.py, custom_views.py."],
    ["templates/ and static/", "Jinja2 pages (setup, login, workbook compare, custom views) and CSS/JS."],
    ["tests/", "pytest suite for the engine, PDF, routes, Tableau client and custom-view state."],
    ["startup.ps1 / startup.sh", "Launch scripts. Set APP_HOST, APP_PORT and APP_DATA_DIR, then run app.py."],
]
story += [table(arch, [1.9 * inch, 4.6 * inch]), Spacer(1, 8)]

# 6. Routes
story += [p("6. Endpoints", H1)]
routes = [
    ["Route", "Method", "Purpose", "Auth"],
    ["/", "GET", "Redirects to setup, login or the compare page, depending on state", "None"],
    ["/setup", "GET, POST", "First-run setup: validates the PAT against the server, then stores it and the passcode hash", "None"],
    ["/login, /logout", "GET, POST", "Passcode login and session clear", "None"],
    ["/workbook-compare", "GET", "Workbook picker", "Passcode"],
    ["/workbook-compare", "POST", "Runs the compare: multipart upload with workbook_id and candidate_file", "Passcode"],
    ["/workbook-compare/export-pdf", "POST", "Generates and downloads the PDF report", "Passcode"],
    ["/custom-views", "GET", "Read-only site-wide Custom View directory", "Passcode"],
    ["/switch-site", "POST", "Changes the selected Tableau site", "None (see note)"],
    ["/healthz", "GET", "Returns {\"status\": \"ok\"}", "None"],
]
story += [table(routes, [1.55 * inch, 0.9 * inch, 3.25 * inch, 0.8 * inch]), Spacer(1, 4)]
story += [p("<b>Note:</b> /switch-site does not call login_required and the site switcher has no passcode check. "
            "Review this if the app is exposed beyond a trusted network.", NOTE)]

# 7. Data and security
story += [p("7. Data, credentials and security", H1)]
story += bullets([
    "<b>Tableau access</b> uses a Personal Access Token (PAT). The PAT secret is encrypted at rest. The PAT name is stored in plain text. "
    "The PAT needs access to every site in config.yaml.",
    "<b>Passcode</b> protects the local UI. It is a single shared passcode, stored only as a hash.",
    "<b>Nothing about workbooks or Custom Views is cached.</b> Everything is fetched live from Tableau on each request.",
    "<b>Account numbers</b> come from BigQuery and are cached in SQLite. They refresh once at startup, so a restart is needed to pick up new data.",
    "<b>Secrets stay out of the repo.</b> bigquery-credentials.json, app.log and the instance\\ folder are git-ignored. "
    "Do not commit them.",
    "<b>Network exposure.</b> config.yaml binds to 0.0.0.0 by default, so other machines on the network can reach the app.",
])

# 8. Known gaps
story += [p("8. Status and known gaps", H1)]
story += bullets([
    "<b>Live filter state (Custom View evidence)</b> is partly built. It is meant to capture the filter and parameter values "
    "a Custom View actually uses in the browser, via the Tableau Embedding API. The commit history shows that a "
    "previous 'Check Live State' feature was removed because the Embedding API v3 was not available. Treat this area as unfinished.",
    "<b>Impact classification</b> is rule-based and depends on how accurately the XML is parsed. Edge cases are expected.",
    "<b>Workbook and Custom View matching</b> uses the exact workbook name.",
    "<b>BigQuery sync</b> needs credentials. Without them, the app still runs but account numbers will be missing.",
    "<b>Documentation</b> is spread across IMPLEMENTATION_COMPLETE.md, LIVE_VERIFICATION_GUIDE.md, PAT_CONNECTION_GUIDE.md "
    "and PAT_QUICK_REFERENCE.md.",
])

# 9. Running locally
story += [p("9. Running it locally", H1)]
story += bullets([
    "Activate the virtual environment: <i>.\\.venv\\Scripts\\Activate.ps1</i>. Use the venv, not the system Python.",
    "Install dependencies if needed: <i>pip install -r requirements.txt</i>.",
    "Start the app: <i>.\\startup.ps1</i>. It listens on port 5001.",
    "Open http://localhost:5001. First run goes to /setup. Enter the PAT name and secret, then set a passcode.",
    "Run tests with pytest from the project folder.",
])
story += [p("To reset the passcode, delete the <i>passcode_hash</i> row in <i>app_config</i>. The PAT is kept, and "
            "/setup asks for the PAT and a new passcode. Stop the app before doing this.", NOTE)]

# 10. Glossary
story += [p("10. Glossary", H1)]
gloss = [
    ["Term", "Meaning"],
    ["Workbook (.twb / .twbx)", "Tableau workbook XML. A .twbx is a packaged archive that includes the .twb and any data."],
    ["Published version", "The workbook currently live on Tableau Server, downloaded for comparison."],
    ["Candidate", "The proposed new version uploaded for comparison."],
    ["Custom View", "A saved personal view of a workbook, owned by one user. Changing the workbook can affect it."],
    ["PAT", "Personal Access Token. The credential the app uses to call Tableau's REST API."],
    ["Site", "A Tableau Server site (tenant). The app works within one selected site at a time."],
]
story += [table(gloss, [1.9 * inch, 4.6 * inch])]

doc = SimpleDocTemplate(
    OUT, pagesize=letter,
    leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    topMargin=0.75 * inch, bottomMargin=0.8 * inch,
    title="Workbook Compare: Developer Overview",
    author="Rohit Nethikar",
)
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print("wrote", OUT)
