import { afterEach, describe, it, expect, vi } from 'vitest';
import { api, ApiError } from './api';
afterEach(() => vi.unstubAllGlobals());
describe('typed API integration', () => {
  it('submits a real processing payload to the existing endpoint', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ meeting_id: 'm-1', status: 'success' }), {
          headers: { 'content-type': 'application/json' },
        })
      );
    vi.stubGlobal('fetch', fetch);
    expect(
      (await api.process({ transcript_text: 'A real transcript.' })).meeting_id
    ).toBe('m-1');
    expect(fetch.mock.calls[0][0]).toContain('/api/meetings/process');
    expect(JSON.parse(fetch.mock.calls[0][1].body).transcript_text).toBe(
      'A real transcript.'
    );
  });
  it('preserves saved meeting IDs on partial failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({
              detail: {
                code: 'task_processing_partial_failure',
                meeting_id: 'saved',
              },
            }),
            { status: 502, headers: { 'content-type': 'application/json' } }
          )
        )
    );
    try {
      await api.process({ transcript_text: 'source' });
      throw new Error('Expected request to fail');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).detail.meeting_id).toBe('saved');
    }
  });
  it('sends optimistic concurrency with task updates', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response('{}', { headers: { 'content-type': 'application/json' } })
      );
    vi.stubGlobal('fetch', fetch);
    await api.status('meeting', 'T1', 'completed', 3);
    expect(fetch.mock.calls[0][1].method).toBe('PATCH');
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toMatchObject({
      expected_revision: 3,
      status: 'completed',
    });
  });
  it('shows connection errors without returning mock data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')));
    await expect(api.meetings()).rejects.toThrow('Cannot reach the backend');
  });
});

it('calls the persisted meeting deletion endpoint', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(
      new Response('{"status":"deleted","meeting_id":"m"}', {
        headers: { 'content-type': 'application/json' },
      })
    );
  vi.stubGlobal('fetch', fetch);
  await api.deleteMeeting('m');
  expect(fetch.mock.calls[0][0]).toContain('/api/meetings/m');
  expect(fetch.mock.calls[0][1].method).toBe('DELETE');
});

it('downloads the selected XLSX using its server filename and revokes the blob URL', async () => {
  vi.useFakeTimers();
  const create = vi.fn().mockReturnValue('blob:meeting');
  const revoke = vi.fn();
  const createDescriptor = Object.getOwnPropertyDescriptor(
    URL,
    'createObjectURL'
  );
  const revokeDescriptor = Object.getOwnPropertyDescriptor(
    URL,
    'revokeObjectURL'
  );
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: create,
  });
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    value: revoke,
  });
  let filename = '';
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, 'click')
    .mockImplementation(function (this: HTMLAnchorElement) {
      filename = this.download;
    });
  const fetch = vi
    .fn()
    .mockResolvedValue(
      new Response(new Uint8Array([80, 75, 3, 4]), {
        headers: {
          'content-type':
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          'content-disposition':
            'attachment; filename="minutes-selected-r3-approved.xlsx"',
        },
      })
    );
  vi.stubGlobal('fetch', fetch);
  try {
    await api.download('selected meeting', 'xlsx', false);
    expect(fetch.mock.calls[0][0]).toContain(
      '/selected%20meeting/export/xlsx?draft=false'
    );
    expect(filename).toBe('minutes-selected-r3-approved.xlsx');
    expect(create).toHaveBeenCalledOnce();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledWith('blob:meeting');
  } finally {
    click.mockRestore();
    vi.useRealTimers();
    if (createDescriptor)
      Object.defineProperty(URL, 'createObjectURL', createDescriptor);
    else Reflect.deleteProperty(URL, 'createObjectURL');
    if (revokeDescriptor)
      Object.defineProperty(URL, 'revokeObjectURL', revokeDescriptor);
    else Reflect.deleteProperty(URL, 'revokeObjectURL');
  }
});
