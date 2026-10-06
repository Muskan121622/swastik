export interface ToolEnvelope {
  ok: boolean
  status: string
  data: Record<string, unknown>
  error: { code: string; message: string } | null
}

export interface ToolEvent {
  id?: number
  tool: string
  status: 'SUCCESS' | 'ERROR' | 'BLOCKED'
  arguments: Record<string, unknown>
  result: ToolEnvelope
  /** 'agent' = model-proposed, 'deterministic' = router-fired without the model,
   *  'safety_gate' / 'fail_closed' = executed by code on a hard stop */
  origin?: string
  created_at?: string
}

export interface Message {
  id: number
  role: 'user' | 'agent' | 'system'
  content: string
  created_at: string
}

export interface HandoffSummary {
  id: number
  reason: string
  summary: string
  status: string
}

export interface TurnResponse {
  reply: string
  conversation_status: 'OPEN' | 'ESCALATED' | 'CLOSED'
  safety_label: string
  /** true when the backend replaced the model's text with a deterministic fallback */
  reply_substituted?: boolean
  events: ToolEvent[]
  handoff: HandoffSummary | null
}

export interface ConversationDetail {
  id: string
  status: string
  confirmed_patient: { id: number; name: string } | null
  active_appointments: { appointment_id: number; slot_id: number; doctor_id: number; status: string }[]
  messages: Message[]
  events: ToolEvent[]
  handoffs: HandoffSummary[]
}

export interface QueueHandoff {
  id: number
  conversation_id: string
  reason: string
  summary: string
  status: string
  conversation_status: string
  confirmed_patient: string | null
  created_at: string
  resolved_at: string | null
}
