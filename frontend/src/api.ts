import type { ConversationDetail, QueueHandoff, TurnResponse } from './types'

const BASE = import.meta.env.VITE_API_URL ?? ''

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try { detail = JSON.stringify(await res.json()) } catch { /* noop */ }
    throw new Error(detail)
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
