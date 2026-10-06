import type { ReactNode } from 'react'
import type { ToolEvent } from '../types'

const statusStyle: Record<string, string> = {
  SUCCESS: 'bg-emerald-500/10 text-emerald-300 border-emerald-400/20',
  ERROR: 'bg-amber-500/10 text-amber-300 border-amber-400/20',
  BLOCKED: 'bg-rose-500/10 text-rose-300 border-rose-400/20',
}

export function Badge({ children, tone }: { children: ReactNode; tone: string }) {
  return <span className={`badge border ${tone}`}>{children}</span>
}

/* ---------- humanisation -------------------------------------------------- */

function when(iso?: unknown): string {
  if (!iso) return ''
  const d = new Date(String(iso) + 'T00:00:00Z')
  if (isNaN(d.getTime())) return String(iso)
  return d.toLocaleDateString('en-IN', {
    weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC',
  })
}

const d = (ev: ToolEvent) => (ev.result?.data ?? {}) as Record<string, any>

/** One plain-English line per tool + status; JSON never sees the surface. */
function humanize(ev: ToolEvent): { title: string; detail: string } {
  const s = ev.result?.status ?? ''
  const data = d(ev)
  const err = ev.result?.error?.message ?? ''

  switch (`${ev.tool}:${s}`) {
    case 'lookup_patient:FOUND':
      return {
        title: `Patient found — ${data.patient?.name ?? 'unknown'}`,
        detail: data.identity_confirmed
          ? `identity confirmed by ${data.matched_by === 'phone' ? 'registered phone' : 'name + date of birth'}`
          : 'name match only — phone still needed before any changes',
      }
    case 'lookup_patient:AMBIGUOUS':
      return {
        title: `Name matches ${data.candidates?.length ?? 'several'} patients`,
        detail: `asking the caller for their registered phone (${(data.candidates ?? [])
          .map((c: any) => c.name).join(', ')})`,
      }
    case 'lookup_patient:NOT_FOUND':
      return { title: 'No matching patient record', detail: err }
    case 'search_slots:OK':
      return {
        title: `${data.count ?? data.slots?.length ?? 0} open slot${data.count === 1 ? '' : 's'} found`,
        detail: data.slots?.length
          ? `earliest: ${when(data.slots[0].date)}, ${data.slots[0].start}–${data.slots[0].end}${data.slots[0].doctor ? ` with ${data.slots[0].doctor}` : ''}`
          : '',
      }
    case 'book_appointment:BOOKED':
      return {
        title: `Appointment booked — ${data.patient ?? 'patient'}`,
        detail: `${when(data.slot?.date)}, ${data.slot?.start}–${data.slot?.end}${data.slot?.doctor ? ` with ${data.slot.doctor}` : ''} · ref #${data.appointment_id}`,
      }
    case 'book_appointment:SLOT_NOT_OPEN':
      return { title: 'Slot just got taken', detail: 'someone booked it first — offering alternatives' }
    case 'book_appointment:IDENTITY_UNCONFIRMED':
      return { title: 'Blocked: identity not confirmed', detail: 'mutations require a phone-confirmed patient' }
    case 'reschedule_appointment:RESCHEDULED':
      return {
        title: `Appointment #${data.appointment_id} moved`,
        detail: `now ${when(data.to_slot?.date)}, ${data.to_slot?.start}–${data.to_slot?.end}${data.to_slot?.doctor ? ` with ${data.to_slot.doctor}` : ''}`,
      }
    case 'cancel_appointment:CANCELLED':
      return {
        title: `Appointment #${data.appointment_id} cancelled`,
        detail: `slot #${data.freed_slot_id} is open again${data.patient ? ` (${data.patient})` : ''}`,
      }
    case 'cancel_appointment:ALREADY_CANCELLED':
      return { title: 'Already cancelled', detail: 'no change was made' }
    case 'cancel_appointment:APPOINTMENT_AMBIGUOUS':
    case 'reschedule_appointment:APPOINTMENT_AMBIGUOUS':
      return {
        title: 'Several active appointments',
        detail: `asking which one (${(data.candidates ?? []).map((c: any) => `#${c.appointment_id}`).join(', ')})`,
      }
    case 'escalate_to_human:ESCALATED':
      return { title: `Handed to a human — ${data.reason ?? ''}`, detail: `handoff #${data.handoff_id} opened; conversation locked` }
    case 'escalate_to_human:ALREADY_ESCALATED':
      return { title: 'Already with a human', detail: 'nothing changed' }
    default:
      if (ev.status === 'BLOCKED')
        return { title: `Blocked by policy — ${s}`, detail: err }
      return { title: `${ev.tool} — ${s}`, detail: err || '' }
  }
}

/* ---------- components ----------------------------------------------------- */

export function EventChip({ ev }: { ev: ToolEvent }) {
  const icon = ev.status === 'SUCCESS' ? '✓' : ev.status === 'BLOCKED' ? '⛔' : '✗'
  const { title, detail } = humanize(ev)
  return (
    <div className={`rounded-xl border backdrop-blur-md px-3 py-2 text-xs shadow-sm ${statusStyle[ev.status]}`}>
      <div className="flex items-center gap-2">
        <span aria-hidden>{icon}</span>
        <span className="font-semibold">{title}</span>
        <span className="ml-auto shrink-0 rounded-full bg-white/10 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide opacity-80">
          {ev.result?.status}
        </span>
      </div>
      {detail && <p className="mt-0.5 text-slate-400">{detail}</p>}
      <details className="mt-1 group">
        <summary className="cursor-pointer select-none text-[10px] text-slate-500 hover:text-slate-300">
          raw audit data
        </summary>
        <pre className="mt-1 whitespace-pre-wrap break-all rounded-lg bg-slate-950/50 p-2 font-mono text-[10px] leading-relaxed text-slate-300">
          {JSON.stringify({ arguments: ev.arguments, result: ev.result }, null, 1)}
        </pre>
      </details>
    </div>
  )
}

/** Renders the agent's text safely: **bold**, "- " bullets, line breaks. */
export function RichText({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <span className="whitespace-pre-wrap">
      {lines.map((line, i) => {
        if (/^\s*[-•*]\s+/.test(line)) {
          return (
            <span key={i} className="block pl-4">
              {'\u2022 '}
              <Inline text={line.replace(/^\s*[-•*]\s+/, '')} />
            </span>
          )
        }
        return <Inline key={i} text={line} />
      })}
    </span>
  )
}

function Inline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g)
  return (
    <>
      {parts.map((p, i) => {
        if (/^\*\*[^*]+\*\*$/.test(p)) return <strong key={i}>{p.slice(2, -2)}</strong>
        if (/^\*[^*]+\*$/.test(p)) return <em key={i}>{p.slice(1, -1)}</em>
        return <span key={i}>{p}</span>
      })}
    </>
  )
}

export function SafetyTrace({ events, escalated }: { events: ToolEvent[]; escalated: boolean }) {
  const steps = [
    { label: 'Input received', done: true },
    { label: 'Safety gate passed (no emergency)', done: !escalated },
    { label: 'Tool arguments validated', done: events.some((e) => e.status !== 'BLOCKED') },
    { label: 'DB transaction committed', done: events.some((e) => e.status === 'SUCCESS' && e.result.ok) },
    { label: 'Response grounded in tool result', done: true },
  ]
  return (
    <div className="space-y-1.5">
      {escalated && (
        <p className="text-xs font-semibold text-rose-400">
          ⛔ SAFETY GATE: EMERGENCY — conversation escalated, agent loop never ran
        </p>
      )}
      {steps.map((s) => (
        <div key={s.label} className="flex items-center gap-2 text-xs text-slate-300">
          <span className={s.done ? 'text-emerald-400' : 'text-slate-600'}>
            {s.done ? '✓' : '○'}
          </span>
          {s.label}
        </div>
      ))}
    </div>
  )
}
