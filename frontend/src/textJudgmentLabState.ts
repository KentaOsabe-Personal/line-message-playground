import type {
  ApplicationDecision,
  ApplicationReason,
  ConversationApplication,
  ConversationTransition,
  JudgmentId,
  QuestionSnapshot,
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
import { getQuestion, type LabGuideId } from './textJudgmentLabContent'

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

function advance(core: ConversationCore, record?: ApplicationBuilder): ConversationCore {
  if (core.stage.kind === 'ended' || core.stage.kind === 'guidance') return core
  if (core.topicPicker !== null) return { ...core, stage: { kind: 'question', question: 'topic' } }

  const { topic, scope, workaround, urgency } = core.confirmed
  if (topic === null) return { ...core, stage: { kind: 'question', question: 'topic' } }
  record?.skip('topic')
  if (scope === null) return { ...core, stage: { kind: 'question', question: 'scope' } }

  record?.skip('scope')
  if (topic === 'missing_notification') {
    if (workaround === 'cannot_read') {
      record?.skip('workaround')
      record?.skip('urgency', 'cannot_read_termination')
      return { ...core, stage: { kind: 'ended', outcome: 'unresolved' }, clarification: null }
    }
    if (core.impact !== 'low' && workaround === null) {
      return { ...core, stage: { kind: 'question', question: 'workaround' } }
    }
    record?.skip('workaround', workaround === null ? 'impact_low' : undefined)
  } else {
    record?.skip('workaround', 'settings_consultation')
  }

  if (urgency === null) return { ...core, stage: { kind: 'question', question: 'urgency' } }
  record?.skip('urgency')
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

// 画面と発言記録には同じ固定質問を使う。開始時の質問IDは、送信契約に合わせてstartとする。
export function conversationQuestion(core: ConversationCore): QuestionSnapshot | null {
  if (core.stage.kind === 'ended') return null
  const id = currentQuestionId(core)
  const contentId = id === 'start' || id === 'topic' ? 'topic'
    : id === 'scope' ? core.confirmed.topic === 'notification_settings' ? 'settings_scope' : 'missing_scope'
    : id === 'result' ? core.confirmed.topic === 'notification_settings' ? 'settings_result' : 'missing_result'
    : id
  const question = getQuestion(contentId)
  return {
    id, prompt: question.prompt, choices: question.choices.map(choice => ({ ...choice })),
    inputMode: core.clarification?.question === expectedAnswerQuestion(core) && core.clarification.mode === 'choices_only'
      ? 'choices_only' : 'free_and_choices',
  }
}

const JUDGMENT_IDS: readonly JudgmentId[] = ['topic', 'relevance', 'change', 'scope', 'workaround', 'result', 'impact_evidence', 'impact', 'urgency']

class ApplicationBuilder {
  readonly newlyConfirmed: ConversationApplication['newlyConfirmed'][number][] = []
  readonly decisions = new Map<JudgmentId, ApplicationDecision>()
  readonly skipped: ConversationApplication['skipped'][number][] = []
  confirmationReason: ConversationApplication['needsConfirmation'][number]['reason'] | null = null

  constructor(readonly before: ConversationCore) {}

  decision(judgmentId: JudgmentId, disposition: ApplicationDecision['disposition'], reason: ApplicationReason,
    priorityRule: ApplicationDecision['priorityRule'] = null) {
    this.decisions.set(judgmentId, { judgmentId, disposition, reason, priorityRule })
  }

  confirm<K extends keyof ConfirmedAnswers>(field: K, value: NonNullable<ConfirmedAnswers[K]>) {
    this.newlyConfirmed.push({ field, value } as ConversationApplication['newlyConfirmed'][number])
    this.decision(field === 'urgency' ? 'urgency' : field, 'applied', 'newly_confirmed')
  }

  skip(question: 'topic' | 'scope' | 'workaround' | 'urgency', reason?: ConversationApplication['skipped'][number]['reason']) {
    this.skipped.push({ question, reason: reason ?? (this.before.confirmed[question] === null ? 'answered_this_turn' : 'answered_before') })
  }

  finish(core: ConversationCore): ConversationTransition {
    const nextQuestion = conversationQuestion(core)
    const needsConfirmation: ConversationApplication['needsConfirmation'][number][] = []
    if (nextQuestion !== null && core.stage.kind !== 'guidance') {
      const question = core.stage.kind === 'start' ? 'topic' : nextQuestion.id
      needsConfirmation.push({ question,
        reason: this.confirmationReason ?? (question === 'workaround' && core.impact === 'unassessed' ? 'impact_unassessed' : 'missing_answer'),
        mode: nextQuestion.inputMode })
    } else if (nextQuestion !== null && core.clarification !== null) {
      needsConfirmation.push({ question: 'result', reason: this.confirmationReason ?? 'missing_answer', mode: nextQuestion.inputMode })
    }
    return { core, application: {
      ruleVersion: 'text-judgment-conversation/1',
      newlyConfirmed: this.newlyConfirmed,
      preserved: { ...this.before.confirmed },
      impactChange: core.impact === this.before.impact ? null : { before: this.before.impact, after: core.impact },
      decisions: [...this.decisions.values()], skipped: this.skipped, needsConfirmation,
      next: { ...core.stage }, nextQuestion, notice: core.notice,
    } }
  }
}

export function applyJudgment(core: ConversationCore, judgment: JudgmentResponse): ConversationTransition {
  const evidence = judgment.evidence
  const record = new ApplicationBuilder(core)
  for (const id of JUDGMENT_IDS) record.decision(id, 'not_used', 'not_used_here')
  if (core.stage.kind === 'ended') return record.finish(core)
  const submittedQuestion = expectedAnswerQuestion(core)
  const guard = (id: JudgmentId, rule: NonNullable<ApplicationDecision['priorityRule']>, next: ConversationCore) => {
    for (const candidate of JUDGMENT_IDS) record.decision(candidate, 'not_used', 'priority_rule', rule)
    record.decision(id, 'applied', 'priority_rule', rule)
    if (rule === 'multiple_topics') record.confirmationReason = 'multiple_topics'
    if (rule === 'guard_needs_review') record.confirmationReason = 'guard_needs_review'
    return record.finish(next)
  }
  if (evidence.change === 'restart') return guard('change', 'restart', nextRevision(core, { notice: 'restart_required' }))
  record.decision('change', 'applied', 'applied_no_action_change')
  if (evidence.topic.kind === 'known' && evidence.topic.value !== 'both' &&
      core.confirmed.topic !== null && evidence.topic.value !== core.confirmed.topic) {
    return guard('topic', 'different_topic', nextRevision(core, { notice: 'restart_required' }))
  }
  if (evidence.topic.kind === 'known' && evidence.topic.value === 'both') {
    return guard('topic', 'multiple_topics', nextRevision(core, {
      stage: { kind: 'question', question: 'topic' },
      topicPicker: core.topicPicker ?? { previousStage: core.stage, previousClarification: core.clarification },
      clarification: null, notice: null,
    }))
  }
  if (evidence.relevance === 'out_of_scope') return guard('relevance', 'out_of_scope', nextRevision(core, { notice: 'out_of_scope' }))
  if (evidence.relevance === 'needs_review' || evidence.change === 'needs_review') {
    return guard(evidence.relevance === 'needs_review' ? 'relevance' : 'change', 'guard_needs_review',
      nextRevision(clarify(core, submittedQuestion), { notice: null }))
  }
  record.decision('relevance', 'applied', 'applied_no_action_change')
  const notice = evidence.relevance === 'mixed' ? 'mixed_scope' : null
  if (core.topicPicker !== null && evidence.topic.kind === 'known' && evidence.topic.value !== 'both') {
    if (core.confirmed.topic !== null) {
      record.decision('topic', 'not_applied', 'confirmed_preserved')
      return record.finish(nextRevision(core, { stage: core.topicPicker.previousStage,
        clarification: core.topicPicker.previousClarification, topicPicker: null, notice }))
    }
    record.confirm('topic', evidence.topic.value)
    const selected = advance({ ...core, confirmed: { ...core.confirmed, topic: evidence.topic.value },
      topicPicker: null, clarification: null, notice }, record)
    return record.finish({ ...selected, revision: core.revision + 1 })
  }

  const confirmed = { ...core.confirmed }
  let impact = core.impact
  const adopt = <K extends keyof ConfirmedAnswers>(field: K, candidate: { kind: string; value?: NonNullable<ConfirmedAnswers[K]> }) => {
    if (confirmed[field] !== null) record.decision(field, 'not_applied', 'confirmed_preserved')
    else if (candidate.kind === 'known' && candidate.value !== undefined) {
      confirmed[field] = candidate.value
      record.confirm(field, candidate.value)
    } else record.decision(field, 'not_applied', candidate.kind === 'unmentioned' ? 'unmentioned' : 'conditions_not_met')
  }
  // 複数相談を表すbothは、先に優先規則で処理する。相談内容が一つに確定するまでは、他の回答を採用しない。
  if (evidence.topic.kind === 'known' && evidence.topic.value !== 'both') adopt('topic', { kind: evidence.topic.kind, value: evidence.topic.value })
  else adopt('topic', { kind: evidence.topic.kind })
  if (confirmed.topic !== null) {
    adopt('scope', evidence.scope)
    if (confirmed.topic === 'missing_notification') adopt('workaround', evidence.workaround)
    adopt('urgency', evidence.urgency)
    if (confirmed.topic === 'missing_notification' && core.stage.kind !== 'guidance' && core.topicPicker?.previousStage.kind !== 'guidance') {
      if (impact === 'unassessed' || impact === 'needs_review') {
        impact = evidence.impact
        const normalization = judgment.inspection.normalization.impact_evidence
        record.decision('impact_evidence', normalization.status === 'eligible' ? 'applied' : 'not_applied',
          normalization.status === 'eligible' ? 'applied_no_action_change' : normalization.status === 'unmentioned' ? 'unmentioned' : 'conditions_not_met')
        record.decision('impact', impact === 'needs_review' ? 'not_applied' : 'applied',
          impact === 'needs_review' ? 'conditions_not_met' : confirmed.workaround !== null ? 'applied_no_action_change' : 'newly_confirmed')
      } else record.decision('impact', 'not_applied', 'confirmed_preserved')
    }
  }
  let next: ConversationCore = { ...core, confirmed, impact, notice }
  if (core.stage.kind === 'guidance') {
    if (evidence.result.kind === 'known') {
      next = finishWithResult(next, evidence.result.value)
      record.decision('result', 'applied', evidence.result.value === 'not_tried' || evidence.result.value === 'cannot_check'
        ? 'applied_no_action_change' : 'newly_confirmed')
    } else record.decision('result', 'not_applied', evidence.result.kind === 'unmentioned' ? 'unmentioned' : 'conditions_not_met')
  } else next = advance(next, record)
  const nextQuestion = currentQuestionId(next)
  const submittedEvidence = evidenceForQuestion(evidence, submittedQuestion)
  if (next.stage.kind !== 'ended' && submittedQuestion === nextQuestion && submittedEvidence !== undefined && submittedEvidence.kind !== 'known') {
    next = clarify(next, submittedQuestion)
  } else if (submittedQuestion !== nextQuestion) next = { ...next, clarification: null }
  if (next.stage.kind !== 'ended' && (next.stage.kind !== 'guidance' || next.clarification !== null)) {
    const id = expectedAnswerQuestion(next)
    const candidate = evidenceForQuestion(evidence, id)
    if (candidate && candidate.kind !== 'known') {
      const normalization = judgment.inspection.normalization[id as JudgmentId]
      record.confirmationReason = candidate.kind === 'unmentioned' ? 'unmentioned'
        : normalization.reasons.includes('unclear') ? 'unclear' : 'conditions_not_met'
    }
  }
  return record.finish({ ...next, revision: core.revision + 1 })
}

export function selectConversationChoice(core: ConversationCore, choice: ConversationChoice): ConversationTransition | null {
  const question = conversationQuestion(core)
  if (question === null || choice.question !== expectedAnswerQuestion(core)) return null
  const value = choice.question === 'urgency' ? choice.value === true ? 'yes' : choice.value === false ? 'no' : '' : choice.value
  if (!question.choices.some(candidate => candidate.id === value)) return null
  const record = new ApplicationBuilder(core)
  const finish = (next: ConversationCore) => record.finish(next)
  if (choice.question === 'result') return finish(nextRevision(finishWithResult(core, choice.value), { notice: null }))
  if (choice.question === 'topic' && core.topicPicker !== null) {
    if (core.confirmed.topic !== null && choice.value !== core.confirmed.topic) {
      return finish(nextRevision(core, { notice: 'restart_required' }))
    }
    if (core.confirmed.topic !== null) {
      return finish(nextRevision(core, { stage: core.topicPicker.previousStage,
        clarification: core.topicPicker.previousClarification, topicPicker: null, notice: null }))
    }
  }
  const confirmed = { ...core.confirmed }
  if (choice.question === 'topic') {
    if (confirmed.topic !== null && confirmed.topic !== choice.value) return finish(nextRevision(core, { notice: 'restart_required' }))
    if (confirmed.topic === null) record.confirm('topic', choice.value)
    confirmed.topic = choice.value
  } else if (choice.question === 'scope' && confirmed.scope === null) {
    confirmed.scope = choice.value
    record.confirm('scope', choice.value)
  } else if (choice.question === 'workaround' && confirmed.workaround === null) {
    confirmed.workaround = choice.value
    record.confirm('workaround', choice.value)
  } else if (choice.question === 'urgency' && confirmed.urgency === null) {
    confirmed.urgency = choice.value
    record.confirm('urgency', choice.value)
  }
  // 選択肢での回答には、Jevの判定を採用した理由を記録しない。
  record.decisions.clear()
  const next = advance({ ...core, confirmed, clarification: null,
    topicPicker: choice.question === 'topic' ? null : core.topicPicker, notice: null }, record)
  return finish({ ...next, revision: core.revision + 1 })
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
