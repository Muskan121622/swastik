import type { ConversationDetail, QueueHandoff, TurnResponse } from './types'

const BASE = import.meta.env.VITE_API_URL ?? ''

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
    // the human half of that rather than a dumped JSON blob.
    let msg = `The service answered with HTTP ${res.status}.`
    try {
      const body = await res.json()
      const d = body?.detail
      const text = d && typeof d === 'object' ? (d.message ?? d.code) : d
      if (typeof text === 'string' && text.trim()) msg = text
    } catch { /* body wasn't JSON — keep the generic line */ }
    throw new Error(msg)
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
