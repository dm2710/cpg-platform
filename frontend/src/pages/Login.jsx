import React, { useState } from 'react'
import { useAuth } from '../context/AuthContext'

export default function Login() {
  const { login } = useAuth()
  const [email, setEmail]       = useState('admin@example.com')
  const [password, setPassword] = useState('')
  const [error, setError]       = useState(null)
  const [loading, setLoading]   = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      await login(email, password)
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center',
      justifyContent: 'center', background: 'var(--bg-canvas)',
    }}>
      <div style={{
        width: 360, padding: 36, borderRadius: 14,
        background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)',
        boxShadow: '0 4px 24px rgba(0,0,0,0.12)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 28 }}>
          <div style={{
            width: 42, height: 34, borderRadius: 9, background: 'var(--accent)',
            color: 'var(--ink-950)', fontWeight: 700, fontSize: 12,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            letterSpacing: '0.04em',
          }}>CPG</div>
          <div>
            <div style={{ fontWeight: 600, fontSize: 15, color: 'var(--text-primary)' }}>CPG Platform</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Revenue intelligence</div>
          </div>
        </div>

        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ display: 'block', fontSize: 13, color: 'var(--text-secondary)', marginBottom: 6 }}>
              Email
            </label>
            <input
              type="email" value={email} onChange={(e) => setEmail(e.target.value)}
              required autoFocus
              style={{
                width: '100%', padding: '9px 12px', borderRadius: 7, boxSizing: 'border-box',
                border: '1px solid var(--border-subtle)', background: 'var(--bg-canvas)',
                color: 'var(--text-primary)', fontSize: 14, fontFamily: 'var(--font-ui)',
              }}
            />
          </div>

          <div style={{ marginBottom: 24 }}>
            <label style={{ display: 'block', fontSize: 13, color: 'var(--text-secondary)', marginBottom: 6 }}>
              Password
            </label>
            <input
              type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              required
              style={{
                width: '100%', padding: '9px 12px', borderRadius: 7, boxSizing: 'border-box',
                border: '1px solid var(--border-subtle)', background: 'var(--bg-canvas)',
                color: 'var(--text-primary)', fontSize: 14, fontFamily: 'var(--font-ui)',
              }}
            />
          </div>

          {error && (
            <div style={{
              marginBottom: 16, padding: '10px 12px', borderRadius: 7,
              background: 'rgba(239,68,68,0.1)', color: 'var(--negative)',
              fontSize: 13, border: '1px solid rgba(239,68,68,0.2)',
            }}>
              {error}
            </div>
          )}

          <button
            type="submit" disabled={loading}
            className="btn btn--accent"
            style={{ width: '100%', justifyContent: 'center', padding: '10px 0' }}
          >
            {loading ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
