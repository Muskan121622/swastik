import { NavLink, Route, Routes } from 'react-router-dom'
import ChatPage from './pages/ChatPage'
import HandoffsPage from './pages/HandoffsPage'

export default function App() {
  const tab = (active: boolean) =>
    `px-3.5 py-1.5 rounded-full text-sm font-medium transition-all ${
      active
        ? 'bg-gradient-to-r from-calm to-sea text-white shadow-glow'
        : 'text-slate-400 hover:text-white hover:bg-white/10'
    }`

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-white/10 bg-slate-950/50 backdrop-blur-xl sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-calm to-sea text-white grid place-items-center font-bold shadow-glow">
              S
            </div>
            <div>
              <p className="font-semibold leading-tight bg-gradient-to-r from-calm to-sea bg-clip-text text-transparent">
                SwasthiQ Front Desk
              </p>
              <p className="text-xs text-slate-400 leading-tight">
                safety-first clinic agent
              </p>
            </div>
          </div>
          <nav className="flex items-center gap-1 bg-white/5 border border-white/10 backdrop-blur-md p-1 rounded-full shadow-glass">
            <NavLink to="/" end className={({ isActive }) => tab(isActive)}>
              Caller Chat
            </NavLink>
            <NavLink to="/handoffs" className={({ isActive }) => tab(isActive)}>
              Handoff Queue
            </NavLink>
          </nav>
        </div>
      </header>

      <main className="flex-1 max-w-6xl w-full mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="/handoffs" element={<HandoffsPage />} />
        </Routes>
      </main>

      <footer className="text-center text-xs text-slate-500/80 py-4">
        Synthetic clinic · the model proposes; only validated deterministic code mutates state
      </footer>
    </div>
  )
}
