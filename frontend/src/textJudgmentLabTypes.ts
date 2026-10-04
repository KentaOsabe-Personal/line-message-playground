export const intentLabels = {
  question: '質問',
  request: '依頼',
  report: '報告',
  other: 'その他',
} as const
export type Intent = keyof typeof intentLabels
export type JudgmentRequest = { contractVersion: 3; text: string }
export type JudgmentResponse = {
  contractVersion: 3
  model: string
  elapsedMs: number
  answers: {
    intent: {
      type: 'choice'
      choice: Intent
      probabilities: Record<Intent, number>
      confidence: number
    }
    sentiment: {
      type: 'score'
      score: number
      probabilities: Record<'0' | '1' | '2', number>
      legend: Record<'0' | '1' | '2', string>
      confidence: number
    }
    urgency: { type: 'noul'; noul: number }
  }
}
export type Parsed<T> =
  { ok: true; value: T } | { ok: false; error: { code: 'protocol_error'; message: string } }

export type LabAccessState =
  | { kind: 'initializing' }
  | { kind: 'authorized'; expiresAt: string; remainingMs: number }
  | { kind: 'reauthentication_required' }
  | { kind: 'denied'; reason: 'wrong_channel' | 'not_allowed' }
  | { kind: 'unavailable' }

export type LabAccessResponse = {
  status: 'authorized'
  expiresAt: string
  serverTime: string
}
