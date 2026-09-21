export type Topic = 'missing_notification' | 'notification_settings'
export type Scope = 'all' | 'specific' | 'unknown'
export type Workaround = 'can_read' | 'cannot_read' | 'unknown'
export type QuestionId = 'start' | 'topic' | 'scope' | 'workaround' | 'urgency' | 'result'
export type ResultAnswer = 'done' | 'not_done' | 'not_tried' | 'cannot_check'
export type Impact = 'unassessed' | 'needs_review' | 'low' | 'high'
export type EndReason = 'resolved' | 'settings_completed' | 'unresolved' | 'interrupted'

export type Evidence<T> =
  | { kind: 'known'; value: T }
  | { kind: 'unmentioned' }
  | { kind: 'needs_review' }

export type ConfirmedAnswers = {
  topic: Topic | null
  scope: Scope | null
  workaround: Workaround | null
  urgency: boolean | null
}

export type ChoiceDetail = {
  type: 'choice'
  choice: string
  probabilities: Readonly<Record<string, number>>
  confidence: number
}

export type ScoreDetail = {
  type: 'score'
  score: number
  legend: Readonly<Record<'0' | '1' | '2', string>>
  probabilities: Readonly<Record<'0' | '1' | '2', number>>
  confidence: number
}

export type JudgmentResponse = {
  contractVersion: 1
  consultationId: string
  requestId: string
  revision: number
  model: string
  evidence: {
    topic: Evidence<Topic | 'both'>
    relevance: 'in_scope' | 'mixed' | 'out_of_scope' | 'needs_review'
    change: 'keep' | 'restart' | 'needs_review'
    scope: Evidence<Scope>
    workaround: Evidence<Workaround>
    result: Evidence<ResultAnswer>
    impact: 'low' | 'high' | 'needs_review'
    urgency: Evidence<boolean>
  }
  details: {
    choices: Readonly<Record<'topic' | 'relevance' | 'change' | 'scope' | 'workaround' | 'result' | 'impact_evidence', ChoiceDetail>>
    score: ScoreDetail
    noul: { type: 'noul'; noul: number }
    jevElapsedMs: number
  }
}

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

export type LabQuestion = {
  id: QuestionId
  prompt: string
  choices: readonly { id: string; label: string }[]
  inputMode: 'free_and_choices' | 'choices_only'
}

export type ConversationMessage =
  | { id: string; role: 'assistant'; text: string }
  | { id: string; role: 'user'; text: string; judgment?: JudgmentResponse }

export type Conversation = {
  consultationId: string
  revision: number
  stage: 'input' | 'question' | 'guide' | 'ended'
  messages: readonly ConversationMessage[]
  confirmed: ConfirmedAnswers
  currentQuestion: LabQuestion | null
  endReason: EndReason | null
}

export type LabProtocolError = {
  code: 'protocol_error'
  message: string
}

export type Parsed<T> = { ok: true; value: T } | { ok: false; error: LabProtocolError }
