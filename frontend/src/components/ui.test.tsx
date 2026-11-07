import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  render,
  screen,
  cleanup,
  fireEvent,
  waitFor,
  within,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Claims, MeetingTable, ErrorNotice, Loading } from './ui';
import Meetings from '../pages/Meetings';
import Assistant from '../pages/Assistant';
import { api } from '../services/api';
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
describe('accessible meeting states', () => {
  it('renders source excerpts and exact timestamp', () => {
    render(
      <Claims
        items={[
          {
            text: 'Ship Friday',
            assessment: 'ambiguous',
            evidence: [
              {
                segment_id: 'seg-1',
                timestamp: '00:12',
                excerpt: 'We might ship Friday.',
              },
            ],
          },
        ]}
      />
    );
    expect(screen.getByText('We might ship Friday.')).toBeInTheDocument();
    expect(screen.getByText(/00:12/)).toBeInTheDocument();
  });
  it('shows an empty library rather than invented meetings', () => {
    render(
      <MemoryRouter>
        <MeetingTable meetings={[]} />
      </MemoryRouter>
    );
    expect(
      screen.getByText('A clear record starts with a meeting')
    ).toBeInTheDocument();
  });
  it('uses accessible loading and failure messages', () => {
    render(
      <>
        <Loading />
        <ErrorNotice message="Atlas unavailable" />
      </>
    );
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Atlas unavailable');
  });
});

it('offers the optional meeting delete action', () => {
  render(
    <MemoryRouter>
      <MeetingTable
        meetings={[{ _id: 'm', meeting_title: 'A meeting' }]}
        onDelete={() => {}}
      />
    </MemoryRouter>
  );
  expect(
    screen.getByRole('button', { name: 'Delete A meeting' })
  ).toBeInTheDocument();
});

it('shows newest meetings first while preserving search, status filtering and links', async () => {
  vi.spyOn(api, 'meetings').mockResolvedValue([
    {
      _id: 'older',
      meeting_title: 'Older meeting',
      date: '2026-10-01',
      status: 'completed',
    },
    {
      _id: 'newer',
      meeting_title: 'Newest meeting',
      date: '2026-11-04',
      status: 'factual_extracted',
    },
  ]);
  vi.spyOn(api, 'minutesList').mockResolvedValue([]);
  render(
    <MemoryRouter>
      <Meetings />
    </MemoryRouter>
  );
  await screen.findByText('Newest meeting');
  expect(
    within(screen.getAllByRole('row')[1]).getByText('Newest meeting')
  ).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Newest meeting' })).toHaveAttribute(
    'href',
    '/meetings/newer'
  );
  fireEvent.change(screen.getByRole('textbox', { name: 'Search meetings' }), {
    target: { value: 'Older' },
  });
  expect(screen.queryByText('Newest meeting')).not.toBeInTheDocument();
  expect(screen.getByText('Older meeting')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: 'Search meetings' }), {
    target: { value: '' },
  });
  fireEvent.change(
    screen.getByRole('combobox', { name: 'Filter processing status' }),
    { target: { value: 'completed' } }
  );
  expect(screen.queryByText('Newest meeting')).not.toBeInTheDocument();
  expect(screen.getByText('Older meeting')).toBeInTheDocument();
});

it('assistant sends the selected meeting ID and clears answers when selection changes', async () => {
  vi.spyOn(api, 'meetings').mockResolvedValue([
    { _id: 'a', meeting_title: 'Meeting A' },
    { _id: 'b', meeting_title: 'Meeting B' },
  ]);
  const query = vi
    .spyOn(api, 'query')
    .mockImplementation(async (id) => ({
      meeting_id: id,
      claims: [
        { text: 'Answer for ' + id, evidence: [], assessment: 'supported' },
      ],
      insufficient_evidence: false,
      retrieval: 'saved meeting data',
      cross_meeting: false,
    }));
  render(
    <MemoryRouter>
      <Assistant />
    </MemoryRouter>
  );
  await screen.findByRole('option', { name: 'Meeting A' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Meeting context' }), {
    target: { value: 'a' },
  });
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Ask about the selected meeting' }),
    { target: { value: 'What decisions were made?' } }
  );
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  await screen.findByText('Answer for a');
  expect(query).toHaveBeenCalledWith('a', 'What decisions were made?');
  await waitFor(() =>
    expect(
      screen.getByRole('combobox', { name: 'Meeting context' })
    ).not.toBeDisabled()
  );
  fireEvent.change(screen.getByRole('combobox', { name: 'Meeting context' }), {
    target: { value: 'b' },
  });
  expect(screen.queryByText('Answer for a')).not.toBeInTheDocument();
  fireEvent.change(
    screen.getByRole('textbox', { name: 'Ask about the selected meeting' }),
    { target: { value: 'What decisions were made?' } }
  );
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  await screen.findByText('Answer for b');
  expect(query).toHaveBeenLastCalledWith('b', 'What decisions were made?');
});
