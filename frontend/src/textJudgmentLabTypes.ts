import type {
  ConversationCore,
  ConversationStage,
  ConversationChoice,
} from './textJudgmentLabState'

export type Topic = 'missing_notification' | 'notification_settings'
export type Scope = 'all' | 'specific' | 'unknown'
export type Workaround = 'can_read' | 'cannot_read' | 'unknown'
export type QuestionId = 'start' | 'topic' | 'scope' | 'workaround' | 'urgency' | 'result'
export type ResultAnswer = 'done' | 'not_done' | 'not_tried' | 'cannot_check'
export type Impact = 'unassessed' | 'needs_review' | 'low' | 'high'
export type EndReason = 'resolved' | 'settings_completed' | 'unresolved' | 'interrupted'

export type Evidence<T> =
  { kind: 'known'; value: T } | { kind: 'unmentioned' } | { kind: 'needs_review' }

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
  contractVersion: 2
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
  inspection: JudgmentInspection
  details: {
    choices: Readonly<
      Record<
        'topic' | 'relevance' | 'change' | 'scope' | 'workaround' | 'result' | 'impact_evidence',
        ChoiceDetail
      >
    >
    score: ScoreDetail
    noul: { type: 'noul'; noul: number }
    jevElapsedMs: number
  }
}

export type JudgmentRequest = {
  contractVersion: 2
  consultationId: string
  requestId: string
  revision: number
  text: string
  context: {
    question: QuestionId
    confirmed: ConfirmedAnswers
    recentUserTexts: readonly string[]
    impact: Impact
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

export type ChoiceId =
  'topic' | 'relevance' | 'change' | 'scope' | 'workaround' | 'result' | 'impact_evidence'
export type JudgmentId = ChoiceId | 'impact' | 'urgency'
export type SentQuestion =
  | Readonly<{ type: 'choice'; instructions: string; criteria: Readonly<Record<string, string>> }>
  | Readonly<{ type: 'score'; instructions: string; criteria: readonly string[] }>
  | Readonly<{ type: 'noul'; instructions: string }>
export type AdoptionPolicySnapshot = Readonly<{
  version: 'text-judgment-adoption/1'
  choice: Readonly<{ minConfidence: 0.7; minProbability: 0.7; requireUniqueMaximum: true }>
  score: Readonly<{ requiredImpactEvidence: 'present'; minConfidence: 0.7; highFrom: 1.5 }>
  noul: Readonly<{ urgentFrom: 0.8; notUrgentThrough: 0.2 }>
}>
export type NormalizationReason =
  | 'eligible'
  | 'unmentioned'
  | 'unclear'
  | 'confidence_below_threshold'
  | 'probability_below_threshold'
  | 'maximum_not_unique'
  | 'impact_evidence_not_adopted'
  | 'impact_evidence_absent'
  | 'noul_between_thresholds'
export type PolicyCheck = Readonly<{
  rule:
    | NormalizationReason
    | 'score_high_boundary'
    | 'noul_urgent_boundary'
    | 'noul_not_urgent_boundary'
  actual: number | string | boolean
  operator: 'gte' | 'lte' | 'eq'
  expected: number | string | boolean
  passed: boolean
}>
export type NormalizationDecision = Readonly<{
  status: 'eligible' | 'unmentioned' | 'needs_review'
  reasons: readonly NormalizationReason[]
  checks: readonly PolicyCheck[]
}>
export type JudgmentInspection = Readonly<{
  questionVersion: 'text-judgment-questions/2'
  state: Readonly<{
    currentText: string
    questionId: QuestionId
    questionText: string
    confirmed: Readonly<ConfirmedAnswers>
    impact: Impact
    recentUserTexts: readonly string[]
  }>
  questions: Readonly<
    Record<ChoiceId, Extract<SentQuestion, { type: 'choice' }>> & {
      impact: Extract<SentQuestion, { type: 'score' }>
      urgency: Extract<SentQuestion, { type: 'noul' }>
    }
  >
  policy: AdoptionPolicySnapshot
  normalization: Readonly<Record<JudgmentId, NormalizationDecision>>
}>
export type ConfirmedChange = {
  [K in keyof ConfirmedAnswers]: Readonly<{ field: K; value: NonNullable<ConfirmedAnswers[K]> }>
}[keyof ConfirmedAnswers]
export type ApplicationReason =
  | 'newly_confirmed'
  | 'unmentioned'
  | 'conditions_not_met'
  | 'confirmed_preserved'
  | 'priority_rule'
  | 'not_used_here'
  | 'applied_no_action_change'
export type ApplicationDecision = Readonly<{
  judgmentId: JudgmentId
  disposition: 'applied' | 'not_applied' | 'not_used'
  reason: ApplicationReason
  priorityRule:
    'restart' | 'different_topic' | 'multiple_topics' | 'out_of_scope' | 'guard_needs_review' | null
}>
export type QuestionSnapshot = Readonly<{
  id: QuestionId
  prompt: string
  choices: readonly Readonly<{ id: string; label: string }>[]
  inputMode: 'free_and_choices' | 'choices_only'
}>
export type ConversationApplication = Readonly<{
  ruleVersion: 'text-judgment-conversation/1'
  newlyConfirmed: readonly ConfirmedChange[]
  preserved: Readonly<ConfirmedAnswers>
  impactChange: Readonly<{ before: Impact; after: Impact }> | null
  decisions: readonly ApplicationDecision[]
  skipped: readonly Readonly<{
    question: 'topic' | 'scope' | 'workaround' | 'urgency'
    reason:
      | 'answered_before'
      | 'answered_this_turn'
      | 'impact_low'
      | 'settings_consultation'
      | 'cannot_read_termination'
  }>[]
  needsConfirmation: readonly Readonly<{
    question: QuestionId
    reason:
      | 'unmentioned'
      | 'conditions_not_met'
      | 'unclear'
      | 'impact_unassessed'
      | 'missing_answer'
      | 'multiple_topics'
      | 'guard_needs_review'
    mode: 'free_and_choices' | 'choices_only'
  }>[]
  next: Readonly<ConversationStage>
  nextQuestion: QuestionSnapshot | null
  notice: ConversationCore['notice']
}>
export type ConversationTransition = Readonly<{
  core: ConversationCore
  application: ConversationApplication
}>
export type TurnOrigin = Readonly<{
  id: string
  text: string
  before: Readonly<ConversationCore>
  previousQuestion: QuestionSnapshot
}>
export type TurnRecord =
  | (TurnOrigin &
      Readonly<{ kind: 'pending'; source: 'text' | 'example'; request: JudgmentRequest }>)
  | (TurnOrigin &
      Readonly<{
        kind: 'judged'
        source: 'text' | 'example'
        request: JudgmentRequest
        judgment: JudgmentResponse
        application: ConversationApplication
        uiElapsedMs: number
      }>)
  | (TurnOrigin &
      Readonly<{
        kind: 'choice'
        source: 'choice'
        answer: ConversationChoice
        application: ConversationApplication
      }>)
  | (TurnOrigin &
      Readonly<{
        kind: 'failed'
        source: 'text' | 'example'
        failure: 'judgment_failed' | 'auth_expired' | 'access_unavailable'
      }>)
  | (TurnOrigin & Readonly<{ kind: 'interrupted'; source: 'text' | 'example' }>)
