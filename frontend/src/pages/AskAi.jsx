import React, { useEffect, useRef, useState } from 'react'
import Topbar from '../components/layout/Topbar'
import ConfidenceBadge from '../components/common/ConfidenceBadge'
import { Spinner } from '../components/common/States'
import { useFilters } from '../context/FilterContext'
import { createSession, askQuestion, listSessions, getSession, deleteSession } from '../api/client'
import './AskAi.css'

const SUGGESTIONS = [
  'Which category will generate the highest revenue next quarter?',
  'Why is this segment forecasted to decline?',
  'Compare the top two categories by revenue.',
  'Which regions are underperforming?',
  'What are the top growth opportunities?',
]

export default function AskAi() {
  const { categoryId, regionId } = useFilters()
  const [sessions, setSessions] = useState([])
  const [sessionId, setSessionId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [sessionLoading, setSessionLoading] = useState(false)
  const scrollRef = useRef(null)

  const refreshSessions = () => listSessions().then(setSessions).catch(() => {})

  useEffect(() => {
    refreshSessions()
  }, [])

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, sending])

  const startNewSession = async () => {
    setSessionLoading(true)
    try {
      const res = await createSession({ category_id: categoryId || undefined, region_id: regionId || undefined })
      setSessionId(res.sessionId)
      setMessages([])
      refreshSessions()
    } finally {
      setSessionLoading(false)
    }
  }

  const openSession = async (id) => {
    setSessionLoading(true)
    try {
      const res = await getSession(id)
      setSessionId(id)
      setMessages(res.messages || [])
    } finally {
      setSessionLoading(false)
    }
  }

  const removeSession = async (id, e) => {
    e.stopPropagation()
    await deleteSession(id)
    if (id === sessionId) {
      setSessionId(null)
      setMessages([])
    }
    refreshSessions()
  }

  const send = async (questionText) => {
    const question = (questionText ?? input).trim()
    if (!question || sending) return

    let activeSession = sessionId
    if (!activeSession) {
      const res = await createSession({ category_id: categoryId || undefined, region_id: regionId || undefined })
      activeSession = res.sessionId
      setSessionId(activeSession)
      refreshSessions()
    }

    setMessages((prev) => [...prev, { role: 'user', content: question, created_at: new Date().toISOString() }])
    setInput('')
    setSending(true)
    try {
      const res = await askQuestion({
        session_id: activeSession,
        question,
        category_id: categoryId || undefined,
        region_id: regionId || undefined,
      })
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: res.answer, confidence: res.confidence, created_at: new Date().toISOString() },
      ])
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Error: ${err.message}`, created_at: new Date().toISOString(), isError: true },
      ])
    } finally {
      setSending(false)
    }
  }

  return (
    <>
      <Topbar title="Ask AI" description="Conversational analytics — evidence-based answers grounded in your live revenue data." />

      <div className="ask-ai">
        <aside className="ask-ai__sessions">
          <button className="btn btn--primary" style={{ width: '100%', justifyContent: 'center' }} onClick={startNewSession} disabled={sessionLoading}>
            <i className="ti ti-plus" aria-hidden="true" />
            New conversation
          </button>

          <div className="ask-ai__session-list">
            {sessions.length === 0 && <p className="ask-ai__empty-hint">No conversations yet</p>}
            {sessions.map((s) => (
              <div
                key={s.sessionId}
                className={`ask-ai__session-item${s.sessionId === sessionId ? ' ask-ai__session-item--active' : ''}`}
                onClick={() => openSession(s.sessionId)}
              >
                <div className="ask-ai__session-title">{s.title}</div>
                <div className="ask-ai__session-meta">{s.messageCount} messages</div>
                <button className="ask-ai__session-delete" onClick={(e) => removeSession(s.sessionId, e)} aria-label="Delete session">
                  <i className="ti ti-x" aria-hidden="true" />
                </button>
              </div>
            ))}
          </div>
        </aside>

        <div className="ask-ai__chat">
          <div className="ask-ai__messages" ref={scrollRef}>
            {messages.length === 0 && !sessionLoading && (
              <div className="ask-ai__welcome">
                <i className="ti ti-message-2" aria-hidden="true" />
                <h3>Ask a question about your revenue data</h3>
                <div className="ask-ai__suggestions">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} className="ask-ai__suggestion" onClick={() => send(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {sessionLoading && <Spinner label="Loading conversation…" />}

            {messages.map((m, i) => (
              <div key={i} className={`ask-ai__message ask-ai__message--${m.role}`}>
                <div className="ask-ai__message-bubble">
                  <p>{m.content}</p>
                  {m.role === 'assistant' && m.confidence !== undefined && !m.isError && (
                    <div style={{ marginTop: 8 }}>
                      <ConfidenceBadge confidence={m.confidence} />
                    </div>
                  )}
                </div>
              </div>
            ))}

            {sending && (
              <div className="ask-ai__message ask-ai__message--assistant">
                <div className="ask-ai__message-bubble">
                  <Spinner label="Thinking…" />
                </div>
              </div>
            )}
          </div>

          <form
            className="ask-ai__input-row"
            onSubmit={(e) => {
              e.preventDefault()
              send()
            }}
          >
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about revenue, forecasts, categories, or regions…"
              disabled={sending}
            />
            <button type="submit" className="btn btn--accent" disabled={sending || !input.trim()}>
              <i className="ti ti-send" aria-hidden="true" />
              Send
            </button>
          </form>
        </div>
      </div>
    </>
  )
}
