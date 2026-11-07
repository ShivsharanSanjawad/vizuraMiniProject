import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { Send, MessageSquare } from 'lucide-react';
import { api, errorMessage } from '../services/api';
import type { QueryAnswer } from '../types';
import {
  PageTitle,
  useLoad,
  Loading,
  ErrorNotice,
  Claims,
  Empty,
} from '../components/ui';
export default function Assistant() {
  const meetings = useLoad(api.meetings);
  const [meetingId, setMeetingId] = useState('');
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [messages, setMessages] = useState<
    { question: string; answer: QueryAnswer }[]
  >([]);
  async function ask(e: FormEvent) {
    e.preventDefault();
    if (!question.trim() || !meetingId) return;
    setBusy(true);
    setError('');
    const sent = question.trim();
    try {
      const answer = await api.query(meetingId, sent);
      setMessages((m) => [...m, { question: sent, answer }]);
      setQuestion('');
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        title="A second look at the conversation"
        description="Ask a focused question. Follow the answer back to its source."
      />
      <section className="panel assistant-panel">
        <div className="assistant-settings">
          <label>
            Meeting context
            <select
              value={meetingId}
              disabled={busy}
              onChange={(e) => {
                setMeetingId(e.target.value);
                setMessages([]);
                setError('');
              }}
            >
              <option value="">Select a meeting</option>
              {meetings.value?.map((m) => (
                <option key={m._id} value={m._id}>
                  {m.meeting_title}
                </option>
              ))}
            </select>
          </label>
          <p className="muted small">
            Basic answers use this meeting's saved minutes and tasks; other
            questions use grounded retrieval. Inferred details are labeled.
            Cross-meeting search is unavailable.
          </p>
        </div>
        {meetings.loading ? (
          <Loading />
        ) : meetings.error ? (
          <ErrorNotice message={meetings.error} retry={meetings.reload} />
        ) : (
          <>
            <div className="conversation" aria-live="polite">
              {!messages.length && (
                <Empty title="Find clarity in the details">
                  <MessageSquare size={24} />
                  <p>
                    Select a meeting, then ask about its decisions, deadlines or
                    unresolved issues.
                  </p>
                  <div className="suggestions">
                    {[
                      'What are the action items?',
                      'What deadlines were discussed?',
                      'What issues remain unresolved?',
                    ].map((q) => (
                      <button
                        key={q}
                        disabled={!meetingId || busy}
                        onClick={() => setQuestion(q)}
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                </Empty>
              )}
              {messages.map((m, i) => (
                <article className="exchange" key={i}>
                  <div className="question">
                    <span className="eyebrow">YOUR QUESTION</span>
                    <p>{m.question}</p>
                  </div>
                  <div className="answer">
                    <span className="eyebrow">FROM THE MEETING RECORD</span>
                    {m.answer.insufficient_evidence &&
                    !m.answer.claims.length ? (
                      <p>
                        This information was not found in the selected meeting's
                        saved data.
                      </p>
                    ) : (
                      <Claims items={m.answer.claims} />
                    )}
                    <Link
                      to={
                        '/meetings/' +
                        m.answer.meeting_id +
                        '?tab=' +
                        (m.answer.claims.some((c) =>
                          c.evidence.some((e) =>
                            e.segment_id.startsWith('task:')
                          )
                        )
                          ? 'tasks'
                          : m.answer.retrieval.startsWith('saved')
                            ? 'minutes'
                            : 'transcript')
                      }
                    >
                      Open source meeting ?
                    </Link>
                  </div>
                </article>
              ))}
              {busy && (
                <Loading text="Retrieving excerpts and preparing an answer?" />
              )}
            </div>
            {error && <ErrorNotice message={error} />}
            <form className="question-form" onSubmit={ask}>
              <label className="sr-only" htmlFor="question">
                Ask about the selected meeting
              </label>
              <textarea
                id="question"
                value={question}
                maxLength={2000}
                minLength={3}
                required
                rows={2}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="What would you like to understand about this meeting?"
                disabled={busy}
              />
              <button
                className="primary"
                disabled={busy || !meetingId || question.trim().length < 3}
                type="submit"
              >
                <Send size={17} />
                <span>Ask</span>
              </button>
            </form>
          </>
        )}
      </section>
    </>
  );
}
