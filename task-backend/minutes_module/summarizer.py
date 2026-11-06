import json
from .prompt_templates import MAP, REDUCE, SYSTEM
from .transcript_processor import chunks, estimate
from .verifier import verify, review_claims
from .llm_service import LLMError
from .schemas import Minutes

class Summarizer:
    def __init__(self, settings, llm):
        self.settings, self.llm = settings, llm

    async def generate(self, meeting, segments, record):
        overhead = estimate(SYSTEM + json.dumps(Minutes.model_json_schema())) + 1000
        budget = self.settings.context_tokens - self.settings.max_output_tokens - overhead
        if budget < 1000:
            raise LLMError("context_budget_too_small", 422)
        batches = chunks(segments, min(budget, self.settings.chunk_tokens, max(500, self.settings.max_output_tokens//2)))
        record["chunk_count"] = len(batches)
        partials = []
        retained = {field: [] for field in ("decisions", "unresolved_issues", "follow_up_items")}
        async def summarize_batch(batch):
            payload = {"meeting":meeting.model_dump(), "segments":[s.model_dump() for s in batch]}
            try:
                result = await self.llm.generate(MAP, payload, record)
            except LLMError as exc:
                if exc.code != "incomplete_or_refused_output" or exc.diagnostics.get("finish_reason") != "length" or len(batch) < 2:
                    raise
                middle = len(batch)//2
                record["chunk_count"] += 1
                return await summarize_batch(batch[:middle]) + await summarize_batch(batch[middle:])
            verify(result, batch)
            return [result]

        for batch in batches:
            partials.extend(await summarize_batch(batch))
        for result in partials:
            for field in retained:
                retained[field].extend(getattr(result, field))
            for topic in result.topics:
                retained["decisions"].extend(topic.decisions)
                retained["unresolved_issues"].extend(topic.unresolved_issues)
        while len(partials) > 1:
            groups, group, cost = [], [], 0
            for part in partials:
                n = estimate(part.model_dump_json())
                if n > budget:
                    return await self.complete(self.combine(partials), meeting, segments, retained, record)
                if group and cost+n > budget:
                    groups.append(group)
                    group, cost = [], 0
                group.append(part)
                cost += n
            if group:
                groups.append(group)
            if len(groups) >= len(partials):
                return await self.complete(self.combine(partials), meeting, segments, retained, record)
            next_level = []
            for group in groups:
                if len(group) == 1:
                    next_level.append(group[0])
                    continue
                try:
                    next_level.append(await self.llm.generate(REDUCE, [p.model_dump() for p in group], record))
                except LLMError as exc:
                    if exc.code != "incomplete_or_refused_output" or exc.diagnostics.get("finish_reason") != "length":
                        raise
                    next_level.append(self.combine(group))
            partials = next_level
        return await self.complete(partials[0], meeting, segments, retained, record)

    async def complete(self, result, meeting, segments, retained, record):
        result, report = self.finish(result, meeting, segments, retained)
        if self.settings.enable_verification:
            await review_claims(self.llm, result, segments, record, self.settings)
            report = verify(result, segments)
            for path in record.get("review_deferred_paths", []):
                report["flags"].append({"path":path,"reason":"semantic_review_exceeds_budget_requires_human_review"})
                report["requires_human_review"] = True
        return result, report

    @staticmethod
    def combine(partials):
        # Deterministic, lossless assembly of already validated summaries. No new claims.
        result = partials[0].model_copy(deep=True)
        for part in partials[1:]:
            if part.executive_summary.text != result.executive_summary.text:
                result.executive_summary = result.executive_summary.model_copy(update={
                    "text": result.executive_summary.text + "\n" + part.executive_summary.text,
                    "evidence": [*result.executive_summary.evidence, *part.executive_summary.evidence],
                    "assessment": "ambiguous",
                })
            result.topics.extend(part.topics)
            for field in ("decisions", "key_takeaways", "conclusions", "unresolved_issues", "follow_up_items", "highlights"):
                getattr(result, field).extend(getattr(part, field))
        return result

    @staticmethod
    def finish(result, meeting, segments, retained):
        for field, values in retained.items():
            getattr(result, field).extend(values)
        if meeting.title:
            result.title = meeting.title
        for field in ("decisions", "key_takeaways", "conclusions", "unresolved_issues", "follow_up_items", "highlights"):
            unique = {}
            for claim in getattr(result, field):
                key = claim.text.casefold().strip()
                if key not in unique:
                    unique[key] = claim
                else:
                    existing = unique[key]
                    refs = {(e.segment_id, e.timestamp, e.excerpt) for e in existing.evidence}
                    for evidence in claim.evidence:
                        identity = (evidence.segment_id, evidence.timestamp, evidence.excerpt)
                        if identity not in refs:
                            existing.evidence.append(evidence)
                            refs.add(identity)
                    if claim.assessment in {"potentially_unsupported", "conflicting"}:
                        existing.assessment = claim.assessment
            setattr(result, field, list(unique.values()))
        return result, verify(result, segments)
