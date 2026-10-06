import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { ConversationDetail, QueueHandoff } from '../types'
import { Badge, EventChip, RichText } from '../components/bits'

const reasonTone: Record<string, string> = {
  EMERGENCY: 'bg-rose-500/15 text-rose-300 border-rose-400/25',
  CLINICAL: 'bg-amber-500/15 text-amber-300 border-amber-400/25',
  LLM_UNAVAILABLE: 'bg-slate-500/15 text-slate-300 border-slate-400/25',
  SYSTEM_ERROR: 'bg-orange-500/15 text-orange-300 border-orange-400/25',
  POLICY_BLOCK: 'bg-amber-500/15 text-amber-300 border-amber-400/25',
  USER_REQUEST: 'bg-sky-500/15 text-sky-300 border-sky-400/25',
}

const REASON_WORDS: Record<string, string> = {
  EMERGENCY: 'Emergency',
  CLINICAL: 'Medical question',
  LLM_UNAVAILABLE: 'Assistant offline',
  SYSTEM_ERROR: 'System fault',
  POLICY_BLOCK: 'Blocked by policy',
  USER_REQUEST: 'Human requested',
}

function ago(iso: string): string {
  const s = (Date.now() - new Date(iso + 'Z').getTime()) / 1000
  if (s < 60) return `${Math.max(1, s | 0)}s ago`
  if (s < 3600) return `${(s / 60) | 0}m ago`
  if (s < 86400) return `${(s / 3600) | 0}h ago`
  return `${(s / 86400) | 0}d ago`
}

export default function HandoffsPage() {
  const [queue, setQueue] = useState<QueueHandoff[]>([])
  const [showResolved, setShowResolved] = useState(false)
  const [selected, setSelected] = useState<QueueHandoff | null>(null)
  const [detail, setDetail] = useState<ConversationDetail | null>(null)
  const [loadError, setLoadError] = useState('')

  const refresh = useCallback(() => {
    api.handoffs(showResolved ? 'ALL' : 'OPEN')
      .then(setQueue)
      .catch((e) => setLoadError(String(e)))
  }, [showResolved])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [refresh])

  useEffect(() => {
    if (!selected) { setDetail(null); return }
    api.conversation(selected.conversation_id).then(setDetail).catch(() => setDetail(null))
  }, [selected])

  async function resolve(h: QueueHandoff) {
    // If the write fails, say where — a button that silently does nothing is
    // worse than an error line, and the selection must stay put.
    try {
      await api.resolve(h.id)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e))
      return
    }
    setSelected(null)
    refresh()
  }

  return (
    <div className="grid md:grid-cols-[320px_1fr] gap-4 items-start">
      {/* queue */}
      <section className="card">
        <div className="px-4 py-3 border-b border-white/10 flex items-center gap-3">
          <h2 className="font-semibold text-sm text-slate-100">Handoff queue</h2>
          <Badge tone="bg-rose-500/15 text-rose-300 border-rose-400/25">
            {queue.filter((h) => h.status === 'OPEN').length} open
          </Badge>
          <label className="ml-auto flex items-center gap-1.5 text-xs text-slate-400">
            <input type="checkbox" checked={showResolved}
                   onChange={(e) => setShowResolved(e.target.checked)} />
            show resolved
          </label>
        </div>
        {loadError && <p className="m-3 text-xs text-rose-300">{loadError}</p>}
        <ul className="divide-y divide-white/5 max-h-[65vh] overflow-y-auto">
          {queue.length === 0 && (
            <li className="p-6 text-center text-sm text-slate-500">
              Nothing needs a human right now. Trigger one from the chat —
              e.g. “I have severe chest pain”.
            </li>
          )}
          {queue.map((h) => (
            <li key={h.id}>
              <button
                onClick={() => setSelected(h)}
                className={`w-full text-left px-4 py-3 transition-colors hover:bg-white/5 ${
                  selected?.id === h.id ? 'bg-white/10' : ''
                } ${h.status === 'RESOLVED' ? 'opacity-55' : ''}`}
              >
                <div className="flex items-center gap-2">
                  <span aria-hidden>
                    {h.status === 'RESOLVED' ? '✓' : h.reason === 'EMERGENCY' ? '🔴' : '🟡'}
                  </span>
                  <Badge tone={reasonTone[h.reason] ?? reasonTone.USER_REQUEST}>{REASON_WORDS[h.reason] ?? h.reason}</Badge>
                  {/* a closed handoff must look closed in the list, not just
                      in the detail pane you have to click into */}
                  {h.status === 'RESOLVED' && (
                    <span className="rounded-full border border-emerald-400/25 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-emerald-300">
                      resolved
                    </span>
                  )}
                  <span className="ml-auto shrink-0 text-[11px] text-slate-500">{ago(h.created_at)}</span>
                </div>
                <p className="mt-1.5 text-xs text-slate-400 line-clamp-2">{h.summary}</p>
                <p className="mt-1 text-[11px] text-slate-500">
                  {h.confirmed_patient ? `Patient: ${h.confirmed_patient}` : 'Patient not identified'}
                  {' · '}conv {h.conversation_id.slice(0, 8)}…
                </p>
              </button>
            </li>
          ))}
        </ul>
      </section>

      {/* detail */}
      <section className="card min-h-[50vh]">
        {!selected && (
          <div className="h-full grid place-items-center text-sm text-slate-500 p-8">
            Select a handoff to review the full transcript and audit trail.
          </div>
        )}
        {selected && detail && (
          <div>
            <div className="px-4 py-3 border-b border-white/10 flex items-center gap-3">
              <h2 className="font-semibold text-sm text-slate-100">Conversation detail</h2>
              <Badge tone={reasonTone[selected.reason] ?? ''}>{REASON_WORDS[selected.reason] ?? selected.reason}</Badge>
              <Badge tone="bg-slate-500/15 text-slate-300 border-slate-400/25">
                {detail.status === 'ESCALATED' ? 'waiting for a human' : detail.status === 'CLOSED' ? 'closed' : 'open'}
              </Badge>
              {selected.status === 'OPEN' && (
                <button onClick={() => resolve(selected)}
                        className="ml-auto rounded-lg bg-gradient-to-br from-emerald-400 to-teal-500 text-slate-950 text-xs font-semibold px-3 py-1.5 shadow-glow transition hover:brightness-110">
                  Mark resolved
                </button>
              )}
              {selected.status === 'RESOLVED' && (
                <span className="ml-auto text-xs text-emerald-400 font-medium">✓ Resolved</span>
              )}
            </div>

            <div className="grid md:grid-cols-[1fr_260px]">
              <div className="p-4 space-y-3 max-h-[60vh] overflow-y-auto">
                {detail.messages.map((m) => (
                  <div key={m.id} className={m.role === 'user' ? 'text-right' : ''}>
                    <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-0.5">
                      {m.role === 'user' ? 'Caller' : 'Agent'}
                    </p>
                    <span className={`inline-block text-sm rounded-xl px-3 py-2 max-w-[90%] text-left shadow-sm ${
                      m.role === 'user'
                        ? 'bg-gradient-to-br from-calm to-sea text-white'
                        : 'bg-white/10 backdrop-blur border border-white/10 text-slate-100'
                    }`}>
                      {m.role === 'user' ? m.content : <RichText text={m.content} />}
                    </span>
                  </div>
                ))}

                <div className="pt-3 border-t border-white/10">
                  <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-2">
                    Full tool audit trail
                  </p>
                  <div className="space-y-1.5">
                    {detail.events.map((ev) => <EventChip key={ev.id} ev={ev} />)}
                    {detail.events.length === 0 &&
                      <p className="text-xs text-slate-500">No tool ran — the safety gate escalated before the agent loop.</p>}
                  </div>
                </div>
              </div>

              <aside className="border-l border-white/10 p-4 space-y-4">
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Outcome</p>
                  <p className="text-sm font-semibold text-slate-100">
                    {selected.reason === 'EMERGENCY' ? '⛔ Emergency handoff' :
                     selected.reason === 'CLINICAL' ? '⚠️ Clinical question' :
                     selected.reason === 'LLM_UNAVAILABLE' ? '🔌 Model unavailable (fail-closed)' :
                     selected.reason === 'SYSTEM_ERROR' ? '🛠️ System fault (fail-closed)' :
                     '🤝 Human requested'}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Patient</p>
                  <p className="text-sm text-slate-200">
                    {detail.confirmed_patient?.name ?? <span className="text-slate-500">never confirmed</span>}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Active appointments</p>
                  {detail.active_appointments.length ? detail.active_appointments.map((a) => (
                    <p key={a.appointment_id} className="text-sm text-slate-200">#{a.appointment_id} · slot {a.slot_id}</p>
                  )) : <p className="text-sm text-slate-500">none</p>}
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Handoff note</p>
                  <p className="text-xs text-slate-400 leading-relaxed">{selected.summary}</p>
                </div>
              </aside>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
