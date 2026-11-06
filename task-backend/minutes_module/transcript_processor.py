import re
from .schemas import Segment, GenerateRequest

def estimate(text: str) -> int:
    return max(1, len(text.encode("utf-8")) // 3 + 1)

def normalize(request: GenerateRequest, max_chars: int) -> list[Segment]:
    raw = request.transcript_text or request.transcript
    if isinstance(raw, str):
        entries = [{"text": line} for line in raw.splitlines() if line.strip()]
    elif raw:
        entries = [e.model_dump() for e in raw]
    else:
        entries = []
    if sum(len(e["text"]) for e in entries) > max_chars:
        raise ValueError("Transcript exceeds configured character limit")
    result, offset = [], 0
    for entry in entries:
        text = entry["text"].strip()
        if not text:
            continue
        timestamp = entry.get("timestamp") or entry.get("at")
        if timestamp and not re.fullmatch(r"(?:\d+:)?[0-5]?\d:[0-5]\d(?:\.\d+)?", timestamp):
            timestamp = None
        for start in range(0, len(text), 700):
            result.append(Segment(segment_id=f"seg-{len(result)+1:06d}", text=text[start:start+700], timestamp=timestamp, speaker=entry.get("speaker"), source=entry.get("source"), offset=offset+start))
        offset += len(entry["text"]) + 1
    if not result:
        raise ValueError("Transcript is empty")
    return result

def chunks(segments: list[Segment], budget: int) -> list[list[Segment]]:
    batches, current, size = [], [], 0
    for segment in segments:
        cost = estimate(segment.model_dump_json())
        if cost > budget:
            raise ValueError("Context budget too small for a source segment")
        if current and size + cost > budget:
            batches.append(current)
            # Keep overlap small relative to the chunk budget; never duplicate a
            # large source segment merely for context. IDs/times/speakers stay intact.
            overlap = current[-1:] if estimate(current[-1].model_dump_json()) <= max(100, budget//8) else []
            current = overlap if overlap and estimate(overlap[0].model_dump_json()) + cost <= budget else []
            size = sum(estimate(s.model_dump_json()) for s in current)
        current.append(segment)
        size += cost
    if current:
        batches.append(current)
    return batches
