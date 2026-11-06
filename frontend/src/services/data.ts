import type { MeetingDoc, Task, TaskStatus, ProcessInput } from '../types';
export function tasksOf(meeting: MeetingDoc): Task[] {
  const source = meeting.enriched_tasks?.length ? meeting.enriched_tasks : meeting.tasks || [];
  const overrides = new Map(meeting.task_status_overrides?.map(t => [t.task_id, t.status]));
  return source.map(t => ({...t, owner: t.owner ?? t.explicit_owner, deadline: t.deadline ?? t.explicit_deadline, status: overrides.get(t.task_id) || t.status}));
}
export const taskStatus = (task: Task): TaskStatus | 'unknown' => task.status || 'unknown';
export function dateLabel(value?: string | null) { if (!value) return 'Not recorded'; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(undefined, {month: 'short', day: 'numeric', year: 'numeric'}); }
export function absoluteDeadline(value?: string | null): number | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}(?:T.*)?$/.test(value)) return null;
  const time = Date.parse(value); return Number.isNaN(time) ? null : time;
}
export function parseTranscriptFile(text: string, name: string): ProcessInput {
  if (!text.trim()) throw new Error('The transcript file is empty.');
  if (name.toLowerCase().endsWith('.txt')) return {transcript_text: text};
  if (!name.toLowerCase().endsWith('.json')) throw new Error('Choose a .txt or .json transcript. Audio transcription is unavailable.');
  let data: unknown;
  try { data = JSON.parse(text); } catch { throw new Error('The file is not valid JSON.'); }
  const input = Array.isArray(data) ? {transcript: data} : data;
  if (!input || typeof input !== 'object') throw new Error('JSON must contain transcript_text or a transcript array.');
  const value = input as ProcessInput;
  if (value.transcript_text && typeof value.transcript_text === 'string' && value.transcript_text.trim()) return value;
  if (typeof value.transcript === 'string' && value.transcript.trim()) return value;
  if (Array.isArray(value.transcript) && value.transcript.length && value.transcript.every(e => typeof e?.text === 'string' && e.text.trim())) return value;
  throw new Error('No valid transcript found in the JSON file.');
}
