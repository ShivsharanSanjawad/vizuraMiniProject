import { describe, it, expect } from 'vitest';
import {
  tasksOf,
  absoluteDeadline,
  parseTranscriptFile,
  sortMeetings,
} from './data';
import type { MeetingDoc } from '../types';
describe('API data normalization', () => {
  it('preserves explicit metadata and applies persisted overrides', () => {
    const meeting: MeetingDoc = {
      _id: 'm',
      meeting_title: 'Sync',
      tasks: [
        {
          task_id: 'T1',
          description: 'Ship',
          explicit_owner: 'Priya',
          explicit_deadline: 'Friday',
        },
      ],
      task_status_overrides: [{ task_id: 'T1', status: 'blocked' }],
    };
    expect(tasksOf(meeting)[0]).toMatchObject({
      owner: 'Priya',
      deadline: 'Friday',
      status: 'blocked',
    });
  });
  it('does not invent dates from relative deadlines', () => {
    expect(absoluteDeadline('Friday')).toBeNull();
    expect(absoluteDeadline('2026-11-04')).not.toBeNull();
  });
  it('accepts timestamped JSON without losing speaker labels', () => {
    const input = parseTranscriptFile(
      '[{"text":"Release","timestamp":"00:12","speaker":"Priya"}]',
      'source.json'
    );
    expect(Array.isArray(input.transcript) && input.transcript[0].speaker).toBe(
      'Priya'
    );
  });
  it('rejects invalid files and empty transcripts', () => {
    expect(() => parseTranscriptFile('{}', 'empty.json')).toThrow();
    expect(() => parseTranscriptFile('audio', 'file.wav')).toThrow();
    expect(() => parseTranscriptFile('', 'file.txt')).toThrow();
  });
});

it('sorts by actual meeting time, puts invalid dates last and does not mutate input', () => {
  const meetings: MeetingDoc[] = [
    {
      _id: 'old',
      meeting_title: 'Old',
      date: '2026-11-01T15:00:00Z',
      created_at: '2026-11-20T10:00:00Z',
    },
    { _id: 'missing', meeting_title: 'Missing' },
    { _id: 'bad', meeting_title: 'Bad', date: '2026-02-30' },
    { _id: 'new', meeting_title: 'New', date: '2026-11-04T09:00:00+05:30' },
  ];
  expect(sortMeetings(meetings).map((m) => m._id)).toEqual([
    'new',
    'old',
    'missing',
    'bad',
  ]);
  expect(meetings.map((m) => m._id)).toEqual(['old', 'missing', 'bad', 'new']);
});

it('uses actual time rather than just the calendar day', () => {
  const meetings: MeetingDoc[] = [
    { _id: 'a', meeting_title: 'A', date: '2026-11-04T08:00:00Z' },
    { _id: 'b', meeting_title: 'B', date: '2026-11-04T14:00:00+05:30' },
  ];
  expect(sortMeetings(meetings).map((m) => m._id)).toEqual(['b', 'a']);
});
