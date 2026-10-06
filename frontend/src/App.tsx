import { Component, ReactNode } from 'react'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import ChatPage from './pages/ChatPage'
import HandoffsPage from './pages/HandoffsPage'

/** A render bug must degrade to one broken panel, never to a blank dashboard
 *  in front of a caller who is waiting on an answer. */
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="rounded-2xl border border-rose-400/30 bg-rose-500/10 p-4 text-sm text-rose-100">
          <p className="font-semibold">This panel hit an error instead of showing data.</p>
          <p className="mt-1 text-xs text-rose-200/80">
            The conversation and every record are unaffected — the agent's writes happen in the
            API, not in this screen. Reload to continue; if it repeats, use the Handoff Queue tab.
          </p>
          <pre className="mt-2 max-h-24 overflow-auto text-[10px] text-rose-200/60">
            {String(this.state.error?.message)}
          </pre>
        </div>
      )
    }
    return this.props.children
  }
}

export default function App() {
  const { pathname } = useLocation()
  const tab = (active: boolean) =>
    `px-2.5 py-1.5 sm:px-3.5 rounded-full text-xs sm:text-sm font-medium whitespace-nowrap transition-all ${
      active
        ? 'bg-gradient-to-r from-calm to-sea text-white shadow-glow'
        : 'text-slate-400 hover:text-white hover:bg-white/10'
    }`

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-white/10 bg-slate-950/50 backdrop-blur-xl sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-3 sm:px-4 h-14 sm:h-16 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2.5 sm:gap-3 min-w-0">
            <div className="w-8 h-8 sm:w-9 sm:h-9 shrink-0 rounded-xl bg-gradient-to-br from-calm to-sea text-white grid place-items-center font-bold shadow-glow">
              S
            </div>
            {/* h-16 is fixed, so a wrapping brand or nav pill spills past the
                border on a 390px phone — keep both on one line instead */}
            <div className="min-w-0">
              <p className="font-semibold leading-tight truncate text-sm sm:text-base bg-gradient-to-r from-calm to-sea bg-clip-text text-transparent">
                SwasthiQ Front Desk
              </p>
              <p className="hidden sm:block text-xs text-slate-400 leading-tight">
                safety-first clinic agent
              </p>
            </div>
          </div>
          <nav className="flex items-center gap-1 shrink-0 bg-white/5 border border-white/10 backdrop-blur-md p-1 rounded-full shadow-glass">
            <NavLink to="/" end className={({ isActive }) => tab(isActive)}>
              <span className="sm:hidden">Chat</span>
              <span className="hidden sm:inline">Caller Chat</span>
            </NavLink>
            <NavLink to="/handoffs" className={({ isActive }) => tab(isActive)}>
              <span className="sm:hidden">Queue</span>
              <span className="hidden sm:inline">Handoff Queue</span>
            </NavLink>
          </nav>
        </div>
      </header>

      <main className="flex-1 max-w-6xl w-full mx-auto px-4 py-6">
        <ErrorBoundary key={pathname}>
          <Routes>
            <Route path="/" element={<ChatPage />} />
            <Route path="/handoffs" element={<HandoffsPage />} />
          </Routes>
        </ErrorBoundary>
      </main>

      <footer className="text-center text-xs text-slate-500/80 py-4">
        Synthetic clinic · the model proposes; only validated deterministic code mutates state
      </footer>
    </div>
  )
}
