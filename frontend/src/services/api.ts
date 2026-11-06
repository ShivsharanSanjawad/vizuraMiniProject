import type { MeetingDoc, ProcessInput, ProcessResult, Revision, Minutes, Stats, Health, TaskStatus, QueryAnswer } from '../types';
const base = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const minutes = '/api/v1/minutes';
export class ApiError extends Error {
  constructor(public status: number, message: string, public detail: Record<string, unknown> = {}) { super(message); }
}
const messages: Record<string, string> = {
  meeting_delete_failed: 'Atlas could not confirm deletion. Refresh the meeting list before retrying.',
  incomplete_or_refused_output: 'The model stopped before returning complete minutes, or refused the request. Check the diagnostic finish_reason; a length limit may need a larger output budget.',
  task_processing_partial_failure: 'Factual tasks were saved, but enrichment did not finish. Open the saved meeting to continue.',
  task_processing_failed: 'Task extraction did not finish. Check the backend provider configuration and try again.',
  database_read_failed: 'Atlas could not read the meeting data. Check the database connection.',
  database_write_failed: 'Atlas did not confirm the write. Check the database before retrying.',
  storage_unavailable: 'Minutes could not be saved to Atlas. No successful save was confirmed.',
  minutes_storage_read_failed: 'Minutes could not be read from Atlas.',
  monitoring_storage_unavailable: 'Monitoring records could not be read from Atlas.',
  provider_authentication: 'The LLM provider rejected its API key. Check backend credentials.',
  provider_invalid_request: 'The selected model does not accept this request. Check provider/model compatibility.',
  invalid_structured_output: 'The provider returned invalid minutes data. No draft was saved.',
  meeting_has_no_transcript: 'This meeting has no saved transcript to search.',
  invalid_transcript_or_configuration: 'The transcript is empty, too large, or the generation configuration is invalid.',

  provider_not_configured: 'No LLM API key is configured on the backend.', database_unavailable: 'MongoDB Atlas is unavailable. Check the backend connection.',
  minutes_not_found: 'Minutes have not been generated yet.', revision_conflict: 'Someone changed these minutes. Reload before saving your edits.',
  task_revision_conflict: 'Task statuses changed. Refresh and try again.', generation_timeout: 'Generation reached its time limit. Check saved results before retrying.',
  provider_transient_failure: 'The LLM provider is temporarily unavailable or rate limited. Try again later.', no_exportable_revision: 'Approve minutes first, or explicitly export a draft.',
  review_flags_require_acknowledgement: 'Review and acknowledge the factual verification flags before approving.',
  monitoring_persistence_failed: 'Minutes may have been saved, but monitoring could not be persisted. Refresh this meeting before retrying.',
};
async function request<T>(path: string, options: RequestInit = {}, timeout = 30000): Promise<T> {
  const controller = new AbortController(); const timer = window.setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(base + path, { ...options, signal: controller.signal, headers: { 'Content-Type': 'application/json', ...options.headers } });
    if (!response.ok) {
      const data = await response.json().catch(() => ({})); const detail = data.detail;
      const object = detail && typeof detail === 'object' && !Array.isArray(detail) ? detail as Record<string, unknown> : {};
      const code = String(object.code || '');
      const message = messages[code] || (Array.isArray(detail) ? 'Please check the request fields.' : typeof detail === 'string' ? detail : code ? code.replaceAll('_', ' ') : `Request failed (${response.status}).`);
      const diagnostic = [code && `code: ${code}`, object.upstream_status && `provider HTTP ${object.upstream_status}`, object.parameter && `parameter: ${object.parameter}`, object.finish_reason && `finish: ${object.finish_reason}`, object.request_id && `request: ${object.request_id}`].filter(Boolean).join('; ');
      throw new ApiError(response.status, message + (diagnostic ? ` (${diagnostic})` : ''), object);
    }
    if (response.headers.get('content-type')?.includes('application/json')) return await response.json() as T;
    return await response.blob() as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === 'AbortError') throw new ApiError(408, 'Request timed out. It may still be running on the backend; refresh before submitting again.');
    throw new ApiError(0, 'Cannot reach the backend. Start the API server and check its Atlas connection.');
  } finally { window.clearTimeout(timer); }
}
const post = <T,>(path: string, body?: unknown) => request<T>(path, {method: 'POST', body: body === undefined ? undefined : JSON.stringify(body)}, 710000);
const id = encodeURIComponent;
export const api = {
  deleteMeeting: (meetingId: string) => request<{status: string; meeting_id: string}>('/api/meetings/' + id(meetingId), {method: 'DELETE'}),
  health: () => request<Health>(minutes + '/health'),
  async meetings(): Promise<MeetingDoc[]> {
    const all: MeetingDoc[] = []; let offset = 0;
    while (true) { const page = await request<{meetings: MeetingDoc[]}>(`/api/meetings?offset=${offset}&limit=100`); all.push(...page.meetings); if (page.meetings.length < 100) return all; offset += 100; }
  },
  meeting: (meetingId: string) => request<MeetingDoc>('/api/meetings/' + id(meetingId)),
  process: (input: ProcessInput) => post<ProcessResult>('/api/meetings/process', input),
  infer: (meetingId: string) => post('/api/v1/infer-context/' + id(meetingId)),
  async minutesList(): Promise<Revision[]> { const all: Revision[] = []; let offset = 0; while (true) { const page = await request<{items: Revision[]; total: number}>(`${minutes}?offset=${offset}&limit=100`); all.push(...page.items); if (all.length >= page.total || !page.items.length) return all; offset += 100; } },
  minutes: (meetingId: string) => request<Revision>(minutes + '/' + id(meetingId)),
  history: (meetingId: string) => request<Revision[]>(minutes + '/' + id(meetingId) + '/history'),
  generate: (meetingId: string) => post<Revision>(minutes + '/generate/' + id(meetingId)),
  regenerate: (meetingId: string) => post<Revision>(minutes + '/' + id(meetingId) + '/regenerate'),
  edit: (meetingId: string, revision: number, content: Minutes, reviewer: string) => request<Revision>(minutes + '/' + id(meetingId), {method: 'PUT', body: JSON.stringify({expected_revision: revision, minutes: content, reviewer})}),
  approve: (meetingId: string, revision: number, reviewer: string, acknowledged: boolean) => post<Revision>(minutes + '/' + id(meetingId) + '/approve', {expected_revision: revision, reviewer, acknowledge_review_flags: acknowledged}),
  status: (meetingId: string, taskId: string, status: TaskStatus, revision: number) => request('/api/meetings/' + id(meetingId) + '/tasks/' + id(taskId) + '/status', {method: 'PATCH', body: JSON.stringify({status, expected_revision: revision})}),
  query: (meetingId: string, question: string) => post<QueryAnswer>('/api/meetings/' + id(meetingId) + '/query', {question}),
  stats: () => request<Stats>(minutes + '/monitoring/stats'),
  async download(meetingId: string, format: 'pdf' | 'docx', draft: boolean) {
    const blob = await request<Blob>(`${minutes}/${id(meetingId)}/export/${format}?draft=${draft}`);
    const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `minutes-${draft ? 'latest' : 'approved'}.${format}`; document.body.appendChild(a); a.click(); a.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};
export const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Something went wrong. Please try again.';
