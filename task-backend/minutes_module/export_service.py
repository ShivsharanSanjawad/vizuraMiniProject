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

def export(revision, kind):
    buffer = BytesIO()
    if kind == "docx":
        from docx import Document
        document = Document()
        for title, body in sections(revision):
            document.add_heading(title, level=1)
            document.add_paragraph(body)
        document.save(buffer)
    else:
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        styles = getSampleStyleSheet()
        story = []
        for title, body in sections(revision):
            story += [Paragraph(escape(title), styles["Heading1"]), Paragraph(escape(body).replace("\n", "<br/>"), styles["BodyText"]), Spacer(1,12)]
        SimpleDocTemplate(buffer, title=revision.minutes.title).build(story)
    return buffer.getvalue()
