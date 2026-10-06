import type { ConversationDetail, QueueHandoff, TurnResponse } from './types'

const BASE = import.meta.env.VITE_API_URL ?? ''

/** Error carrying the API's machine-readable code (e.g. CONV_NOT_FOUND) so
 *  callers can recover from specific failures, not just show the message. */
export class ApiError extends Error {
  code?: string
  status?: number
  constructor(message: string, opts?: { code?: string; status?: number }) {
    super(message)
    this.name = 'ApiError'
    this.code = opts?.code
    this.status = opts?.status
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(BASE + path, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    // No connection at all (API stopped, deploy asleep, offline). Tell the
    // receptionist what to do; "TypeError: Failed to fetch" is not an answer.
    throw new Error('Cannot reach the front-desk service. Check that the API is running, then try again.')
  }
  if (!res.ok) {
    // The API fails with a machine-readable {detail:{code,message}} body; show
    // the human half of that rather than a dumped JSON blob, and keep the code
    // so callers can react to specific failures (e.g. a lost conversation).
    let msg = `The service answered with HTTP ${res.status}.`
    let code: string | undefined
    try {
      const body = await res.json()
      const d = body?.detail
      code = d && typeof d === 'object' ? d.code : undefined
      const text = d && typeof d === 'object' ? (d.message ?? d.code) : d
      if (typeof text === 'string' && text.trim()) msg = text
    } catch { /* body wasn't JSON — keep the generic line */ }
    throw new ApiError(msg, { code, status: res.status })
  }
  return res.json() as Promise<T>
}

export const api = {
  newConversation: () =>
    req<{ conversation_id: string }>('/api/conversations', { method: 'POST', body: '{}' }),

  sendMessage: (cid: string, content: string) =>
    req<TurnResponse>(`/api/conversations/${cid}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),

  conversation: (cid: string) =>
    req<ConversationDetail>(`/api/conversations/${cid}`),

  handoffs: (status = 'OPEN') =>
    req<QueueHandoff[]>(`/api/handoffs?status=${status}`),

  resolve: (id: number) =>
    req<{ id: number; status: string }>(`/api/handoffs/${id}/resolve`, { method: 'POST' }),

  openSlots: () => req<{ date: string; open_slots: number }[]>('/api/slots/open'),
}
