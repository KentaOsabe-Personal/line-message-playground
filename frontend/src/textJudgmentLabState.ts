import type {
  ConfirmedAnswers,
  EndReason,
  Impact,
  JudgmentResponse,
  QuestionId,
  ResultAnswer,
  Scope,
  Topic,
  Workaround,
} from './textJudgmentLabTypes'
import type { LabGuideId } from './textJudgmentLabContent'

export type ConversationStage =
  | { kind: 'start' }
  | { kind: 'question'; question: Exclude<QuestionId, 'start' | 'result'> }
  | { kind: 'guidance'; guideId: LabGuideId; presentation: 'summary_first' | 'details_open' }
  | { kind: 'ended'; outcome: EndReason }

export type ConversationNotice = 'out_of_scope' | 'mixed_scope' | 'restart_required' | null

export type ConversationCore = {
  consultationId: string
  revision: number
  stage: ConversationStage
  confirmed: ConfirmedAnswers
  impact: Impact
  clarification: { question: QuestionId; mode: 'open' | 'choices_only' } | null
  topicPicker: { previousStage: ConversationStage; previousClarification: ConversationCore['clarification'] } | null
  notice: ConversationNotice
}

export type JudgmentEvidence = JudgmentResponse['evidence']

export type ConversationChoice =
  | { question: 'topic'; value: Topic }
  | { question: 'scope'; value: Scope }
  | { question: 'workaround'; value: Workaround }
  | { question: 'urgency'; value: boolean }
  | { question: 'result'; value: ResultAnswer }

const EMPTY_CONFIRMED: ConfirmedAnswers = {
  topic: null,
  scope: null,
  workaround: null,
  urgency: null,
}

function nextRevision(core: ConversationCore, changes: Partial<ConversationCore>): ConversationCore {
  return { ...core, ...changes, revision: core.revision + 1 }
}

function guideId(topic: Topic, scope: Scope): LabGuideId {
  const suffix = scope === 'specific' ? 'specific' : 'all'
  return topic === 'missing_notification' ? `missing_${suffix}` : `settings_${suffix}`
}

function advance(core: ConversationCore): ConversationCore {
  if (core.stage.kind === 'ended' || core.stage.kind === 'guidance') return core
  if (core.topicPicker !== null) return { ...core, stage: { kind: 'question', question: 'topic' } }

  const { topic, scope, workaround, urgency } = core.confirmed
  if (topic === null) return { ...core, stage: { kind: 'question', question: 'topic' } }
  if (scope === null) return { ...core, stage: { kind: 'question', question: 'scope' } }

  if (topic === 'missing_notification') {
    if (workaround === 'cannot_read') {
      return { ...core, stage: { kind: 'ended', outcome: 'unresolved' }, clarification: null }
    }
    if (core.impact !== 'low' && workaround === null) {
      return { ...core, stage: { kind: 'question', question: 'workaround' } }
    }
  }

  if (urgency === null) return { ...core, stage: { kind: 'question', question: 'urgency' } }
  return {
    ...core,
    stage: {
      kind: 'guidance',
      guideId: guideId(topic, scope),
      presentation: urgency ? 'summary_first' : 'details_open',
    },
    clarification: null,
  }
}

function expectedAnswerQuestion(core: ConversationCore): QuestionId {
  return core.stage.kind === 'start' ? 'topic' : currentQuestionId(core)
}

function evidenceForQuestion(evidence: JudgmentEvidence, question: QuestionId) {
  if (question === 'topic') return evidence.topic
  if (question === 'scope') return evidence.scope
  if (question === 'workaround') return evidence.workaround
  if (question === 'urgency') return evidence.urgency
  if (question === 'result') return evidence.result
  return undefined
}

function clarify(core: ConversationCore, question: QuestionId): ConversationCore {
  const mode = core.clarification?.question === question ? 'choices_only' : 'open'
  const stage = question === 'topic' || question === 'scope' || question === 'workaround' || question === 'urgency'
    ? { kind: 'question' as const, question }
    : core.stage
  return { ...core, stage, clarification: { question, mode } }
}

function finishWithResult(core: ConversationCore, result: ResultAnswer): ConversationCore {
  if (core.stage.kind !== 'guidance' || core.confirmed.topic === null) return core
  if (result === 'not_tried' || result === 'cannot_check') return { ...core, clarification: null }
  const outcome: EndReason = result === 'not_done'
    ? 'unresolved'
    : core.confirmed.topic === 'missing_notification' ? 'resolved' : 'settings_completed'
  return { ...core, stage: { kind: 'ended', outcome }, clarification: null }
}

export function createConversationCore(consultationId: string): ConversationCore {
  return {
    consultationId,
    revision: 0,
    stage: { kind: 'start' },
    confirmed: { ...EMPTY_CONFIRMED },
    impact: 'unassessed',
    clarification: null,
    topicPicker: null,
    notice: null,
  }
}

export function applyJudgment(core: ConversationCore, evidence: JudgmentEvidence): ConversationCore {
  if (core.stage.kind === 'ended') return core
  const submittedQuestion = expectedAnswerQuestion(core)

  if (evidence.change === 'restart') {
    return nextRevision(core, { notice: 'restart_required' })
  }
  if (
    evidence.topic.kind === 'known' &&
    evidence.topic.value !== 'both' &&
    core.confirmed.topic !== null &&
    evidence.topic.value !== core.confirmed.topic
  ) {
    return nextRevision(core, { notice: 'restart_required' })
  }
  if (evidence.topic.kind === 'known' && evidence.topic.value === 'both') {
    return nextRevision(core, {
      stage: { kind: 'question', question: 'topic' },
      topicPicker: core.topicPicker ?? { previousStage: core.stage, previousClarification: core.clarification },
      clarification: null,
      notice: null,
    })
  }
  if (evidence.relevance === 'out_of_scope') {
    return nextRevision(core, { notice: 'out_of_scope' })
  }
  if (evidence.relevance === 'needs_review' || evidence.change === 'needs_review') {
    return nextRevision(clarify(core, submittedQuestion), { notice: null })
  }
  const notice = evidence.relevance === 'mixed' ? 'mixed_scope' : null
  if (
    core.topicPicker !== null &&
    evidence.topic.kind === 'known' &&
    evidence.topic.value !== 'both'
  ) {
    if (core.confirmed.topic !== null) {
      return nextRevision(core, {
        stage: core.topicPicker.previousStage,
        clarification: core.topicPicker.previousClarification,
        topicPicker: null,
        notice,
      })
    }
    const selected = advance({
      ...core,
      confirmed: { ...core.confirmed, topic: evidence.topic.value },
      topicPicker: null,
      clarification: null,
      notice,
    })
    return { ...selected, revision: core.revision + 1 }
  }

  let confirmed = { ...core.confirmed }
  let impact = core.impact
  const topicCandidate = evidence.topic.kind === 'known' ? evidence.topic.value : null
  if (confirmed.topic === null && topicCandidate !== null && topicCandidate !== 'both') confirmed.topic = topicCandidate

  if (confirmed.topic !== null) {
    if (confirmed.scope === null && evidence.scope.kind === 'known') confirmed.scope = evidence.scope.value
    if (
      confirmed.topic === 'missing_notification' &&
      confirmed.workaround === null &&
      evidence.workaround.kind === 'known'
    ) confirmed.workaround = evidence.workaround.value
    if (confirmed.urgency === null && evidence.urgency.kind === 'known') confirmed.urgency = evidence.urgency.value
    if (
      core.stage.kind !== 'guidance' &&
      core.topicPicker?.previousStage.kind !== 'guidance' &&
      (impact === 'unassessed' || impact === 'needs_review')
    ) impact = evidence.impact
  }

  let next: ConversationCore = {
    ...core,
    confirmed,
    impact,
    notice,
  }
  if (core.stage.kind === 'guidance' && evidence.result.kind === 'known') {
    next = finishWithResult(next, evidence.result.value)
  } else {
    next = advance(next)
  }

  const nextQuestion = currentQuestionId(next)
  const submittedEvidence = evidenceForQuestion(evidence, submittedQuestion)
  if (
    next.stage.kind !== 'ended' &&
    submittedQuestion === nextQuestion &&
    submittedEvidence !== undefined &&
    submittedEvidence.kind !== 'known'
  ) {
    next = clarify(next, submittedQuestion)
  } else if (submittedQuestion !== nextQuestion) {
    next = { ...next, clarification: null }
  }
  return { ...next, revision: core.revision + 1 }
}

export function selectConversationChoice(core: ConversationCore, choice: ConversationChoice): ConversationCore {
  const selectsInitialTopic = core.stage.kind === 'start' && choice.question === 'topic'
  if (core.stage.kind === 'ended' || (!selectsInitialTopic && choice.question !== currentQuestionId(core))) return core

  if (choice.question === 'result') {
    return nextRevision(finishWithResult(core, choice.value), { notice: null })
  }

  if (choice.question === 'topic' && core.topicPicker !== null) {
    if (core.confirmed.topic !== null && choice.value !== core.confirmed.topic) {
      return nextRevision(core, { notice: 'restart_required' })
    }
    if (core.confirmed.topic !== null) {
      return nextRevision(core, {
        stage: core.topicPicker.previousStage,
        clarification: core.topicPicker.previousClarification,
        topicPicker: null,
        notice: null,
      })
    }
  }

  const confirmed = { ...core.confirmed }
  if (choice.question === 'topic') {
    if (confirmed.topic !== null && confirmed.topic !== choice.value) {
      return nextRevision(core, { notice: 'restart_required' })
    }
    confirmed.topic = choice.value
  } else if (choice.question === 'scope' && confirmed.scope === null) {
    confirmed.scope = choice.value
  } else if (choice.question === 'workaround' && confirmed.workaround === null) {
    confirmed.workaround = choice.value
  } else if (choice.question === 'urgency' && confirmed.urgency === null) {
    confirmed.urgency = choice.value
  }

  const next = advance({
    ...core,
    confirmed,
    clarification: null,
    topicPicker: choice.question === 'topic' ? null : core.topicPicker,
    notice: null,
  })
  return { ...next, revision: core.revision + 1 }
}

export function interruptConversation(core: ConversationCore): ConversationCore {
  if (core.stage.kind === 'ended') return core
  return nextRevision(core, {
    stage: { kind: 'ended', outcome: 'interrupted' },
    clarification: null,
    topicPicker: null,
    notice: null,
  })
}

export function restartConversation(_core: ConversationCore, consultationId: string): ConversationCore {
  return createConversationCore(consultationId)
}

export function currentQuestionId(core: ConversationCore): QuestionId {
  if (core.stage.kind === 'start') return 'start'
  if (core.stage.kind === 'question') return core.stage.question
  return 'result'
}
