import React from 'react'

export default function App() {
  const guessedHost = typeof window !== 'undefined' ? window.location.hostname.replace('frontend', 'backend') : ''
  const backendBase = guessedHost ? `https://${guessedHost}` : ''

  return (
    <div style={{ fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif', padding: 24 }}>
      <span style={{ display: 'inline-block', background: '#0ea5a4', color: 'white', padding: '4px 10px', borderRadius: 999, fontSize: 12 }}>AccidentAlert</span>
      <h1>AI-Powered Accident Detection</h1>
      <p style={{ color: '#64748b' }}>Minimal React app scaffolded so the Vite build succeeds on Render.</p>

      <div style={{ border: '1px solid #e2e8f0', borderRadius: 12, padding: 16, marginTop: 12 }}>
        <h2>Backend API</h2>
        {backendBase ? (
          <ul>
            <li>Health: <a href={`${backendBase}/health`} target="_blank" rel="noreferrer">{backendBase}/health</a></li>
            <li>Docs: <a href={`${backendBase}/docs`} target="_blank" rel="noreferrer">{backendBase}/docs</a></li>
          </ul>
        ) : (
          <p>Backend URL could not be inferred from the hostname.</p>
        )}
      </div>
    </div>
  )
}
