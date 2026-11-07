from io import BytesIO
from xml.sax.saxutils import escape

def sections(revision):
    m = revision.minutes
    yield m.title, f"{revision.status.upper()} - Revision {revision.revision}"
    yield "Meeting", "Date: "+(revision.meeting.startedAt or "Unknown")+"\nParticipants: "+(", ".join(revision.meeting.participants) or "Unknown")
    def render(claim):
        refs = "; ".join(f"{e.segment_id} [{e.timestamp or 'no timestamp'}]: {e.excerpt}" for e in claim.evidence)
        return claim.text+f" [{claim.assessment}]"+("\nSources: "+refs if refs else "")
    yield "Executive summary", render(m.executive_summary)
    for topic in m.topics:
        yield topic.topic, "\n".join(render(c) for c in [topic.summary]+topic.key_points+topic.decisions+topic.unresolved_issues)
    for field in ("key_takeaways", "decisions", "conclusions", "unresolved_issues", "follow_up_items", "highlights"):
        yield field.replace("_", " ").title(), "\n\n".join(render(c) for c in getattr(m,field)) or "None recorded"
    approved = revision.task_records.get("approved", [])
    if approved:
        import json
        yield "Approved action items", "\n".join(json.dumps(t, ensure_ascii=False) for t in approved)

def saved_tasks(revision=None, meeting_doc=None):
    """Read current saved tasks, retaining explicit versus inferred details. No writes."""
    doc = meeting_doc or {}
    records = revision.task_records if revision else {}
    factual = doc.get("tasks") if "tasks" in doc else records.get("factual", [])
    enriched = doc.get("enriched_tasks") if "enriched_tasks" in doc else records.get("inferred", [])
    approved = doc.get("approved_action_items") or records.get("approved", [])
    rows = {}
    for group in (factual or [], enriched or [], approved or []):
        for task in group:
            if not isinstance(task, dict) or not task.get("description"):
                continue
            key = str(task.get("task_id") or task["description"])
            rows.setdefault(key, {}).update(task)
    overrides = {str(row.get("task_id")):row.get("status") for row in doc.get("task_status_overrides", [])}
    explicit = {str(row.get("task_id")):row for row in factual or [] if isinstance(row, dict)}
    result = []
    for key, row in rows.items():
        fact = explicit.get(key, row)
        owner = fact.get("explicit_owner") or row.get("owner") or row.get("explicit_owner")
        deadline = fact.get("explicit_deadline") or row.get("deadline") or row.get("explicit_deadline")
        result.append({**row, "task_id":str(row.get("task_id") or key), "owner":owner, "deadline":deadline,
            "status":overrides.get(key) or row.get("status"),
            "owner_inferred":bool(owner and not fact.get("explicit_owner") and row not in approved),
            "deadline_inferred":bool(deadline and not fact.get("explicit_deadline") and row not in approved)})
    return result


def report_data(revision, meeting_doc=None):
    from datetime import datetime
    doc = meeting_doc or {}
    title = doc.get("meeting_title") or revision.minutes.title
    started = doc.get("date") or revision.meeting.startedAt
    date, time = started or "Not recorded", "Not recorded"
    if started:
        try:
            parsed = datetime.fromisoformat(started.replace("Z", "+00:00"))
            date = parsed.strftime("%d %b %Y")
            if "T" in started or " " in started:
                time = parsed.strftime("%H:%M") + (" " + parsed.strftime("%z") if parsed.tzinfo else "")
        except (ValueError, TypeError, AttributeError):
            pass
    participants = doc.get("participants") if doc.get("participants") is not None else revision.meeting.participants
    return {"title":title, "date":date, "time":time, "participants":participants or [],
        "tasks":saved_tasks(revision, doc), "graph":doc.get("graph_analysis") or {}}


def concise_sections(revision, data):
    minutes = revision.minutes
    seen = set()
    def unique(values):
        result = []
        for value in values:
            text = value.text.strip() if hasattr(value, "text") else str(value).strip()
            identity = " ".join(text.casefold().split())
            if text and identity not in seen:
                seen.add(identity)
                if getattr(value, "assessment", None) in {"potentially_unsupported", "conflicting"}:
                    text = "For review: " + text
                result.append(text)
        return result
    summary = unique([minutes.executive_summary])
    if summary:
        yield "Summary", summary
    # Decisions take precedence over repeated topic points.
    decisions = unique([*minutes.decisions, *(claim for topic in minutes.topics for claim in topic.decisions)])
    discussions = unique([*(claim for topic in minutes.topics for claim in (topic.key_points or [topic.summary])), *minutes.key_takeaways, *minutes.conclusions])
    if discussions:
        yield "Key discussion points", discussions
    if decisions:
        yield "Decisions", decisions
    issues = unique([*minutes.unresolved_issues, *(claim for topic in minutes.topics for claim in topic.unresolved_issues),
        *(task["description"] for task in data["tasks"] if task.get("status")=="blocked"),
        *(row.get("description") for row in data["graph"].get("bottlenecks", []) if row.get("is_external") and row.get("description"))])
    if issues:
        yield "Unresolved issues and blockers", issues
    followups = unique(minutes.follow_up_items)
    if followups:
        yield "Follow-up items", followups


def task_cells(task):
    def value(field):
        text = task.get(field) or "Not recorded"
        return str(text) + (" (inferred)" if task.get(field+"_inferred") else "")
    return [str(task["description"]), value("owner"), value("deadline"), value("priority"), value("status")]


def export(revision, kind, meeting_doc=None):
    buffer = BytesIO()
    if kind == "docx":
        from docx import Document
        document = Document()
        for title, body in sections(revision):
            document.add_heading(title, level=1)
            document.add_paragraph(body)
        document.save(buffer)
        return buffer.getvalue()
    if kind not in {"pdf", "xlsx"}:
        raise ValueError("Unsupported export format")
    data = report_data(revision, meeting_doc)
    content = list(concise_sections(revision, data))
    if kind == "xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
        workbook = Workbook()
        details = workbook.active
        details.title = "Meeting Details"
        details.append(["Field", "Value"])
        for row in [("Title",data["title"]), ("Date",data["date"]), ("Time",data["time"]),
            ("Participants",", ".join(data["participants"]) or "Not recorded"), ("Minutes state",revision.status), ("Action items",len(data["tasks"]))]:
            details.append(row)
        minutes = workbook.create_sheet("Minutes and Decisions")
        minutes.append(["Section", "Details"])
        for heading, paragraphs in content:
            for text in paragraphs:
                minutes.append([heading, text])
        tasks = workbook.create_sheet("Action Items")
        tasks.append(["Task", "Owner", "Deadline", "Priority", "Status"])
        for task in data["tasks"]:
            tasks.append(task_cells(task))
        for sheet in workbook:
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            sheet.sheet_view.showGridLines = False
            for row in sheet:
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.value = ILLEGAL_CHARACTERS_RE.sub("", cell.value)
                        # Meeting text is untrusted data, never an Excel formula.
                        cell.data_type = "s"
                    cell.alignment = Alignment(vertical="top", wrap_text=True)
                    if cell.row == 1:
                        cell.font = Font(bold=True, color="FFFFFF")
                        cell.fill = PatternFill("solid", fgColor="454034")
                sheet.row_dimensions[row[0].row].height = 25 if row[0].row == 1 else 45
            for column in sheet.columns:
                sheet.column_dimensions[column[0].column_letter].width = 28
            sheet.column_dimensions["A"].width = 56 if sheet is tasks else 25
            sheet.column_dimensions["B"].width = 100 if sheet is minutes else 65 if sheet is details else 28
            import math
            for row in sheet.iter_rows(min_row=2):
                lines = max(sum(max(1, math.ceil(len(line)/max(10, sheet.column_dimensions[cell.column_letter].width))) for line in str(cell.value or "").split("\n")) for cell in row)
                sheet.row_dimensions[row[0].row].height = max(24, min(200, lines*15+9))
        workbook.save(buffer)
        return buffer.getvalue()

    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import reportlab
    from pathlib import Path
    font_dir = Path(reportlab.__file__).resolve().parent / "fonts"
    if "MeetingSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("MeetingSans", str(font_dir / "Vera.ttf")))
        pdfmetrics.registerFont(TTFont("MeetingSansBold", str(font_dir / "VeraBd.ttf")))
    styles = getSampleStyleSheet()
    body = ParagraphStyle("MeetingBody", parent=styles["BodyText"], fontName="MeetingSans", fontSize=9, leading=13, spaceAfter=5)
    heading = ParagraphStyle("MeetingHeading", parent=body, fontName="MeetingSansBold", fontSize=11, leading=15, spaceBefore=12, spaceAfter=7, keepWithNext=True)
    title_style = ParagraphStyle("MeetingTitle", parent=heading, fontSize=18, leading=22, spaceBefore=0, spaceAfter=10)
    cell_style = ParagraphStyle("MeetingCell", parent=body, fontSize=8, leading=11, spaceAfter=0)
    def paragraph(text, style=body):
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)
    story = [paragraph(data["title"], title_style)]
    if revision.status == "draft":
        story.append(paragraph("DRAFT - pending review", heading))
    story.append(paragraph("Date: " + str(data["date"]) + " | Time: " + str(data["time"])))
    story.append(paragraph("Participants: " + (", ".join(data["participants"]) or "Not recorded")))
    for name, paragraphs in content:
        if name in {"Unresolved issues and blockers", "Follow-up items"}:
            continue
        story.append(paragraph(name, heading))
        for text in paragraphs:
            story.append(paragraph(text))
    if data["tasks"]:
        story.append(paragraph("Action items", heading))
        rows = [[paragraph(value, cell_style) for value in ["Task", "Owner", "Deadline", "Priority", "Status"]]]
        rows.extend([[paragraph(value, cell_style) for value in task_cells(task)] for task in data["tasks"]])
        width = A4[0]-80
        table = LongTable(rows, colWidths=[width*f for f in (.40,.17,.17,.12,.14)], repeatRows=1, splitInRow=1, hAlign="LEFT")
        table.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eee8da")),
            ("VALIGN",(0,0),(-1,-1),"TOP"),("LINEBELOW",(0,0),(-1,0),.6,colors.HexColor("#8b713a")),
            ("LINEBELOW",(0,1),(-1,-1),.25,colors.HexColor("#d4ccbd")),
            ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
            ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7)]))
        story.append(table)
        if any(task["owner_inferred"] or task["deadline_inferred"] for task in data["tasks"]):
            story.extend([Spacer(1,6),paragraph("Fields marked inferred were not explicitly stated in factual task extraction.")])
    for name, paragraphs in content:
        if name in {"Unresolved issues and blockers", "Follow-up items"}:
            story.append(paragraph(name, heading))
            story.extend(paragraph(text) for text in paragraphs)
    def footer(canvas, document):
        canvas.setFont("MeetingSans",8)
        canvas.setFillColor(colors.HexColor("#706a5e"))
        canvas.drawRightString(A4[0]-40,24,f"Page {document.page}")
    SimpleDocTemplate(buffer, pagesize=A4, title=data["title"], leftMargin=40, rightMargin=40, topMargin=36, bottomMargin=40).build(story,onFirstPage=footer,onLaterPages=footer)
    return buffer.getvalue()
