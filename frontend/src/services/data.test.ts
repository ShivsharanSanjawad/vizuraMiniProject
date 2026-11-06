import { describe, it, expect } from 'vitest';
import { tasksOf, absoluteDeadline, parseTranscriptFile } from './data';
import type { MeetingDoc } from '../types';
describe('API data normalization',()=>{
 it('preserves explicit metadata and applies persisted overrides',()=>{
  const meeting:MeetingDoc={_id:'m',meeting_title:'Sync',tasks:[{task_id:'T1',description:'Ship',explicit_owner:'Priya',explicit_deadline:'Friday'}],task_status_overrides:[{task_id:'T1',status:'blocked'}]};
  expect(tasksOf(meeting)[0]).toMatchObject({owner:'Priya',deadline:'Friday',status:'blocked'});
 });
 it('does not invent dates from relative deadlines',()=>{expect(absoluteDeadline('Friday')).toBeNull();expect(absoluteDeadline('2026-11-04')).not.toBeNull();});
 it('accepts timestamped JSON without losing speaker labels',()=>{const input=parseTranscriptFile('[{"text":"Release","timestamp":"00:12","speaker":"Priya"}]','source.json');expect(Array.isArray(input.transcript)&&input.transcript[0].speaker).toBe('Priya');});
 it('rejects invalid files and empty transcripts',()=>{expect(()=>parseTranscriptFile('{}','empty.json')).toThrow();expect(()=>parseTranscriptFile('audio','file.wav')).toThrow();expect(()=>parseTranscriptFile('','file.txt')).toThrow();});
});
