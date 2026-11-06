from .schemas import GenerateRequest, Meeting

class TaskAdapter:
    async def get(self, meeting_id):
        from main import get_meeting_doc
        return await get_meeting_doc(meeting_id)

    def request(self, meeting_id, doc, supplied=None):
        if supplied and (supplied.transcript_text or supplied.transcript):
            request = supplied.model_copy(deep=True)
            request.meeting.id = meeting_id
            return request
        return GenerateRequest(meeting=Meeting(id=meeting_id, title=doc.get("meeting_title"),
            participants=doc.get("participants") or [], startedAt=doc.get("date")),
            transcript=doc.get("transcript"), transcript_text=None if doc.get("transcript") else doc.get("raw_transcript") or doc.get("transcript_text"))

    def tasks(self, doc):
        return {"factual":doc.get("tasks", []), "inferred":doc.get("enriched_tasks", []),
                "approved":doc.get("approved_action_items", [])}
