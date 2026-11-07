# Meeting minutes module

The integrated application setup is documented in [the project README](../README.md). It supersedes the original local-fallback setup instructions.

- Start `combined_app:app` to include original task routes, minutes routes and workspace additions.
- Use the single root `.venv` and install `task-backend/requirements.txt` once.
- Atlas is required through MONGODB_URL; there is no local JSON fallback.
- Provider configuration and SDK client are shared across modules.
- Minutes revisions remain immutable, with compare-and-swap edits/approval and source evidence checks.
- Monitoring records persist to Atlas across restarts.
- Existing routes remain under /api/v1/minutes, including history, PDF, DOCX and Excel (.xlsx).
- MINUTES_ENABLE_VERIFICATION controls the additional LLM review; deterministic source checks always run.
- Pricing configuration is optional; unknown costs remain null.

The new frontend connects all review actions to the backend. See the project README for routes, examples, test commands, manual checks and limitations. This integration delivery was code-only; build, tests and live integrations were not run.


## Optimization diagnosis and comparison 

This section supersedes earlier descriptions of per-chunk minutes rewriting and fixed task batches. Existing files only were updated. No frontend, route path, request schema, response schema, database collection, stored review history, graph engine or export implementation was replaced.

### Traced flow and baseline

MeetingDetail's Generate Minutes button calls api.generate(id) once -> POST /api/v1/minutes/generate/{id} -> adapter reads the saved meeting -> normalization -> chunk summaries -> optional review -> consolidation -> deterministic verification -> revision persistence -> GET refresh/display. Regenerate uses its separate existing endpoint. NewMeeting explicitly calls POST /api/meetings/process first (factual extraction, enrichment, local graph), then generates minutes using the returned ID. React effects load GET data; they do not automatically generate minutes. Multiple callers/tabs were nevertheless able to duplicate work, and stale revision detection occurred only after paid work.

Configured provider at inspection: Groq, default model openai/gpt-oss-20b, OpenAI-compatible AsyncOpenAI (installed openai SDK 3.45.0) with SDK retries disabled. Code defaults in use before changes: max output 4096, ceiling 16384, up to 2 length retries, 3 transient retries, 2000-token approximate source chunks, verification enabled. There was no central rate/concurrency scheduler, cache or in-flight generation guard. Every 429 followed the transient retry path, including quota exhaustion.

Before: minutes calls = C summaries + C full-minutes review calls (if enabled) + R consolidation calls; failures could trigger additional output retries, transient retries and recursive splits. New uploads added 1 factual extraction + ceil(T/8) enrichment calls, repeating the entire transcript for each batch. Task and assistant calls used the same service but were not represented as minutes-generation jobs in the monitoring ledger.

For minutes_sample_request.json, baseline source text was approximately 111 tokens and one chunk. The initial summary request was approximately 1712 input tokens. Exact total input/output usage for a real meeting cannot be known without its model responses. No live database logs, provider quota dashboard or paid provider calls were accessed for this optimization. Previously supplied finish_reason=length is a confirmed output-cap failure; other live error responses have not been supplied and should not be guessed.

### Offline before/after comparison

A controlled serialization comparison used the same existing illustrative minutes output, source transcript and byte-based token estimator. This is not a real provider benchmark or calibrated tokenizer count.

| Measure | Before | After |
|---|---:|---:|
| First minutes generation SDK calls | 2 | 2 |
| Approximate serialized input tokens | 3838 | 2450 |
| Approximate output tokens | 528 | 270 |
| Identical replay SDK calls | 2 again | 0 |

This fixture reduced estimated input by about 36% and output by about 49%. Results depend on actual source/output size; there is no claimed universal savings percentage. Preserving optional factual review means a first short meeting still uses two calls. Compact verification now deduplicates repeated claims and returns findings, not another full minutes document. Long meetings summarize all categories together per bounded chunk, consolidate hierarchically and review deduplicated final claims in bounded groups. Deterministic evidence checks, duplicate removal and graph cycle analysis remain local. Missing dates/owners/deadlines remain unknown rather than being invented.

Task enrichment batches now adapt to the configured output budget, up to 16 target tasks, retaining the full task catalog for cross-task dependencies. At the default 4096 output budget, 9-16 tasks use one enrichment request rather than two, absent retries. Factual and inferred task operations remain separate to preserve their different contracts and evidence rules; they are not folded into an enormous request. The whole transcript is still provided for contextual inference to preserve existing inference behavior; cached retries avoid redoing identical successful requests. Oversized inputs fail clearly rather than silently omitting transcript context.

### Scheduler and account configuration

The shared service schedules task extraction, enrichment, minutes, review and assistant calls through a rolling 60-second RPM/TPM admission window and bounded concurrency. It reserves estimated input plus requested output before dispatch and reconciles provider-reported usage after success. Failed attempts retain conservative reservations because their usage is unknown. Output growth is capped by remaining per-request context and configured TPM capacity. Queue waits are inside the existing overall generation deadline.

Put optional settings in the SAME task-backend/.env:

```dotenv
MINUTES_REQUESTS_PER_MINUTE=20
MINUTES_TOKENS_PER_MINUTE=8000
MINUTES_MAX_CONCURRENCY=1
MINUTES_RETRY_JITTER=0.25
MINUTES_CACHE_TTL_SECONDS=900
MINUTES_CACHE_ENTRIES=128
```

These are project defaults, not discovered account entitlements. Groq's published base table lists 30 RPM / 8000 TPM for openai/gpt-oss-20b, but actual organization/model limits can differ: use https://console.groq.com/settings/limits and set equal or lower effective values. Account-wide traffic from other applications is outside this process's scheduler. Separate input/output token-rate quotas, if enabled by the provider, are not individually modeled. Daily/billing quota exhaustion is classified and returned without repeated retries.

Scheduling, LRU output cache and in-flight job coordination are PROCESS-LOCAL. Run the normal single-worker development server. Multiple workers/replicas need a shared limiter/queue or divided limits; this change intentionally does not add Redis or a new database schema. The configured upper bounds are estimates, not a guarantee against externally shared provider limits.

### Retry and error behavior

Transient request/token rate limits, timeouts and eligible server failures get bounded exponential backoff with jitter. Numeric or HTTP-date Retry-After is respected. Existing maximum retry/call/deadline bounds remain. Authentication failures, unavailable models, context-window errors and exhausted daily/billing quotas are not repeatedly retried. JSON-mode capability rejection is remembered for the service so each chunk does not repeat the same unsupported-parameter attempt. Truncated or schema-invalid JSON is never accepted as valid minutes.

Diagnostic categories include provider_request_rate_limit, provider_token_rate_limit, provider_rate_limit, provider_quota_exhausted, provider_context_limit, provider_authentication and provider_model_unavailable. An exhausted retry response retains provider_transient_failure plus its category in detail. Only safe categories/statuses are logged, never raw provider messages, API keys or transcript bodies.

### Caching and idempotency

Validated model responses use a bounded, TTL-based in-memory LRU cache keyed by canonical input/transcript content hash, provider/model, JSON schema, actual system/instruction prompt, explicit prompt version and generation settings. Errors are not cached. Simultaneous identical calls join the same in-flight task. Cached payloads are parsed/copied so one caller cannot mutate another result.

Minutes-generation jobs additionally fingerprint normalized source segments, meeting metadata, linked task records, provider/model, prompts and generation settings. Repeating Generate Minutes returns the current matching saved revision, including human edits/approval, without creating another revision or making model calls. Concurrent identical jobs share one computation and one revision write. Explicit Regenerate bypasses reuse and appends a new draft, preserving all earlier approvals. Failed jobs are removed from in-flight maps so a later retry can proceed.

Persistent reuse uses the existing generation metadata field; no collection or migration was added. Old records lacking a fingerprint remain readable and can generate normally. Reuse works across restarts for matching current revisions, while the per-call LRU cache resets on restart. Existing monitoring records describe generation jobs; cached HTTP replays do not create fake additional LLM jobs. Structured generation_reused/generation_joined/llm_cache_hit log events explain reuse. Actual call counters increase only on dispatch; scheduler wait time and cache hits are distinct from measured token usage.

### Verification

The offline suite covers schema/API compatibility, review/approval/revision/export behavior, duplicate suppression, saved-revision reuse, explicit regeneration, quota classification, independent RPM/TPM limits, concurrency, jitter/Retry-After, cache isolation/settings invalidation, and source-reference preservation. No paid calls or Atlas state were used. Run from task-backend with the existing root .venv: `python -B -m pytest -q -p no:cacheprovider`.




## Targeted meeting workspace improvements

The existing PDF handler now produces a concise report: saved title/date/time/participants, summary, deduplicated discussion points and decisions, a wrapped action-item table, and available unresolved issues/blockers. Evidence dumps, review internals and empty sections are omitted from the PDF. DOCX remains available with its existing detailed evidence rendering. Export content uses the selected minutes revision plus the selected meeting's current saved task statuses; inferred owners/deadlines are labeled instead of presented as explicit assignments.

Excel was not previously implemented. It now uses the same export handler through the single additional format route below. The workbook has Meeting Details, Minutes and Decisions, and Action Items sheets with headers, wrapped cells and frozen header rows. Untrusted strings are stored as text, not formulas. openpyxl is the added runtime dependency; pypdf is test-only for reopening and checking PDFs. Existing approval defaults remain: latest approved revision unless draft=true is explicitly requested. Filenames are sanitized, format-specific MIME types are returned, and Content-Disposition is exposed through existing CORS for browser downloads.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | /api/v1/minutes/{meeting_id}/export/pdf | Concise PDF for the selected meeting; existing path |
| GET | /api/v1/minutes/{meeting_id}/export/xlsx | Excel workbook for the selected meeting; new format in the existing export family |
| GET | /api/v1/minutes/{meeting_id}/export/docx | Existing detailed DOCX export |
| POST | /api/meetings/{meeting_id}/query | Existing assistant path; basic queries now use saved selected-meeting data before any LLM call |

The assistant deterministically answers basic summary, decision, action-item, owner, deadline and unresolved-issue questions from saved minutes, tasks and source excerpts. It prefers an approved minutes revision when one exists, labels inferred details and reports absent information. Less direct questions retain the existing configured-provider fallback, now with saved minutes/task records available as clearly identified evidence. All reads remain scoped to the supplied meeting ID; cross-meeting retrieval is not added. Existing query request/response fields and database schemas are unchanged.

The Meetings screen sorts valid meeting dates/times newest first. Saved minutes metadata can supply a missing meeting date. Undated/invalid-date records follow dated meetings; creation time is used only to order that undated group, with stable ordering otherwise. Search, status filters, links and deletion retain their existing behavior.


Targeted workspace update verification: **89 backend tests and 18 frontend tests passed; the TypeScript/Vite production build passed.** Tests reopened generated PDF/XLSX files and verified contents, long task-table wrapping, formula safety, selected-meeting assistant isolation, sorting/filter/navigation behavior and browser download-handler logic. Browser handling used the frontend test environment; a manual live-browser/Atlas workflow was not performed for this update.
