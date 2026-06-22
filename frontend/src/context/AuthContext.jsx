import React, { createContext, useContext, useState, useEffect } from 'react'
import client from '../api/client'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem('cpg_token'))
  const [user, setUser]   = useState(null)

  // Attach/remove Authorization header whenever the token changes
  useEffect(() => {
    if (token) {
      client.defaults.headers.common['Authorization'] = `Bearer ${token}`
      localStorage.setItem('cpg_token', token)
    } else {
      delete client.defaults.headers.common['Authorization']
      localStorage.removeItem('cpg_token')
    }
  }, [token])

  const login = async (email, password) => {
    const res = await client.post('/auth/login', { email, password })
    setToken(res.data.access_token)
    setUser({ userId: res.data.user_id, role: res.data.role })
    return res.data
  }

  const logout = () => {
    setToken(null)
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ token, user, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
