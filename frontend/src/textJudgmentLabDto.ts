import type {
  ChoiceDetail,
  Evidence,
  JudgmentResponse,
  JudgmentRequest,
  JudgmentInspection,
  LabAccessResponse,
  Parsed,
  ResultAnswer,
  Scope,
  Topic,
  Workaround,
} from './textJudgmentLabTypes'

const protocolError = (): Parsed<never> => ({
  ok: false,
  error: { code: 'protocol_error', message: '応答形式を確認できません。' },
})

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const hasExactKeys = (value: Record<string, unknown>, keys: readonly string[]): boolean => {
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  return actual.length === expected.length && actual.every((key, index) => key === expected[index])
}

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const isUuid = (value: unknown): value is string =>
  typeof value === 'string' && uuidPattern.test(value)
const isFiniteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value)
const isProbability = (value: unknown): value is number =>
  isFiniteNumber(value) && value >= 0 && value <= 1
const timestampPattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/
const isTimestamp = (value: unknown): value is string =>
  typeof value === 'string' && timestampPattern.test(value) && Number.isFinite(Date.parse(value))

const inSet = <T extends string>(value: unknown, values: readonly T[]): value is T =>
  typeof value === 'string' && values.includes(value as T)

const parseEvidence = <T extends string | boolean>(
  value: unknown,
  isValue: (candidate: unknown) => candidate is T,
): Evidence<T> | null => {
  if (!isRecord(value) || typeof value.kind !== 'string') return null
  if (value.kind === 'known' && hasExactKeys(value, ['kind', 'value']) && isValue(value.value)) {
    return { kind: 'known', value: value.value }
  }
  if (value.kind === 'unmentioned' && hasExactKeys(value, ['kind'])) return { kind: 'unmentioned' }
  if (value.kind === 'needs_review' && hasExactKeys(value, ['kind']))
    return { kind: 'needs_review' }
  return null
}

const choiceCandidates = {
  topic: ['missing_notification', 'notification_settings', 'both', 'unmentioned', 'unclear'],
  relevance: ['in_scope', 'mixed', 'out_of_scope', 'unclear'],
  change: ['keep', 'restart', 'unclear'],
  scope: ['all', 'specific', 'unknown', 'unmentioned', 'unclear'],
  workaround: ['can_read', 'cannot_read', 'unknown', 'unmentioned', 'unclear'],
  result: ['done', 'not_done', 'not_tried', 'cannot_check', 'unmentioned', 'unclear'],
  impact_evidence: ['present', 'absent', 'unclear'],
} as const

const parseChoice = (value: unknown, candidates: readonly string[]): ChoiceDetail | null => {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['type', 'choice', 'probabilities', 'confidence']) ||
    value.type !== 'choice' ||
    !inSet(value.choice, candidates) ||
    !isProbability(value.confidence) ||
    !isRecord(value.probabilities) ||
    !hasExactKeys(value.probabilities, candidates)
  )
    return null
  const probabilities: Record<string, number> = {}
  for (const candidate of candidates) {
    const probability = value.probabilities[candidate]
    if (!isProbability(probability)) return null
    probabilities[candidate] = probability
  }
  const total = Object.values(probabilities).reduce((sum, probability) => sum + probability, 0)
  const maximum = Math.max(...Object.values(probabilities))
  if (Math.abs(total - 1) > 0.01 || probabilities[value.choice] !== maximum) return null
  return { type: 'choice', choice: value.choice, probabilities, confidence: value.confidence }
}

const parseScore = (value: unknown): JudgmentResponse['details']['score'] | null => {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['type', 'score', 'legend', 'probabilities', 'confidence']) ||
    value.type !== 'score' ||
    !isFiniteNumber(value.score) ||
    value.score < 0 ||
    value.score > 2 ||
    !isProbability(value.confidence) ||
    !isRecord(value.legend) ||
    !hasExactKeys(value.legend, ['0', '1', '2']) ||
    value.legend['0'] !== '支障なし' ||
    value.legend['1'] !== '不便だが別の操作で目的を達成できる' ||
    value.legend['2'] !== '目的を達成できない' ||
    !isRecord(value.probabilities) ||
    !hasExactKeys(value.probabilities, ['0', '1', '2'])
  )
    return null
  const probabilities = value.probabilities
  if (
    !isProbability(probabilities['0']) ||
    !isProbability(probabilities['1']) ||
    !isProbability(probabilities['2']) ||
    Math.abs(probabilities['0'] + probabilities['1'] + probabilities['2'] - 1) > 0.01
  )
    return null
  return {
    type: 'score',
    score: value.score,
    legend: { '0': value.legend['0'], '1': value.legend['1'], '2': value.legend['2'] },
    probabilities: { '0': probabilities['0'], '1': probabilities['1'], '2': probabilities['2'] },
    confidence: value.confidence,
  }
}

export function parseLabAccessResponse(value: unknown): Parsed<LabAccessResponse> {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['status', 'expiresAt', 'serverTime']) ||
    value.status !== 'authorized' ||
    !isTimestamp(value.expiresAt) ||
    !isTimestamp(value.serverTime)
  )
    return protocolError()
  return {
    ok: true,
    value: { status: 'authorized', expiresAt: value.expiresAt, serverTime: value.serverTime },
  }
}

const nonblank = (value: unknown): value is string =>
  typeof value === 'string' && value.trim().length > 0
const isText = (value: unknown): value is string => nonblank(value) && [...value].length <= 1000
const scalar = (value: unknown): boolean =>
  typeof value === 'string' || typeof value === 'boolean' || isFiniteNumber(value)
const reasons = [
  'eligible',
  'unmentioned',
  'unclear',
  'confidence_below_threshold',
  'probability_below_threshold',
  'maximum_not_unique',
  'impact_evidence_not_adopted',
  'impact_evidence_absent',
  'noul_between_thresholds',
] as const
const checkRules = [
  ...reasons,
  'score_high_boundary',
  'noul_urgent_boundary',
  'noul_not_urgent_boundary',
] as const
const judgmentIds = [...Object.keys(choiceCandidates), 'impact', 'urgency']

function isInspection(value: unknown): value is JudgmentInspection {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ['questionVersion', 'state', 'questions', 'policy', 'normalization']) ||
    value.questionVersion !== 'text-judgment-questions/2'
  )
    return false
  const state = value.state
  if (
    !isRecord(state) ||
    !hasExactKeys(state, [
      'currentText',
      'questionId',
      'questionText',
      'confirmed',
      'impact',
      'recentUserTexts',
    ]) ||
    !isText(state.currentText) ||
    !nonblank(state.questionText) ||
    !inSet(state.questionId, ['start', 'topic', 'scope', 'workaround', 'urgency', 'result']) ||
    !inSet(state.impact, ['unassessed', 'needs_review', 'low', 'high']) ||
    !Array.isArray(state.recentUserTexts) ||
    state.recentUserTexts.length > 2 ||
    !state.recentUserTexts.every(isText) ||
    !isRecord(state.confirmed) ||
    !hasExactKeys(state.confirmed, ['topic', 'scope', 'workaround', 'urgency'])
  )
    return false
  const confirmed = state.confirmed
  if (
    !(
      confirmed.topic === null ||
      inSet(confirmed.topic, ['missing_notification', 'notification_settings'])
    ) ||
    !(confirmed.scope === null || inSet(confirmed.scope, ['all', 'specific', 'unknown'])) ||
    !(
      confirmed.workaround === null ||
      inSet(confirmed.workaround, ['can_read', 'cannot_read', 'unknown'])
    ) ||
    !(confirmed.urgency === null || typeof confirmed.urgency === 'boolean')
  )
    return false
  if (
    confirmed.topic === null &&
    (confirmed.scope !== null ||
      confirmed.workaround !== null ||
      confirmed.urgency !== null ||
      state.impact !== 'unassessed')
  )
    return false
  if (confirmed.topic === 'notification_settings' && confirmed.workaround !== null) return false
  if (!['start', 'topic'].includes(state.questionId) && confirmed.topic === null) return false
  if (state.questionId === 'workaround' && confirmed.topic !== 'missing_notification') return false
  if (state.questionId === 'result' && confirmed.scope === null) return false

  if (!isRecord(value.questions) || !hasExactKeys(value.questions, judgmentIds)) return false
  for (const [id, candidates] of Object.entries(choiceCandidates)) {
    const question = value.questions[id]
    if (
      !isRecord(question) ||
      !hasExactKeys(question, ['type', 'instructions', 'criteria']) ||
      question.type !== 'choice' ||
      !nonblank(question.instructions) ||
      !isRecord(question.criteria) ||
      !hasExactKeys(question.criteria, candidates) ||
      !Object.values(question.criteria).every(nonblank)
    )
      return false
  }
  const impact = value.questions.impact
  const urgency = value.questions.urgency
  if (
    !isRecord(impact) ||
    !hasExactKeys(impact, ['type', 'instructions', 'criteria']) ||
    impact.type !== 'score' ||
    !nonblank(impact.instructions) ||
    !Array.isArray(impact.criteria) ||
    impact.criteria.length !== 3 ||
    impact.criteria[0] !== '支障なし' ||
    impact.criteria[1] !== '不便だが別の操作で目的を達成できる' ||
    impact.criteria[2] !== '目的を達成できない' ||
    !isRecord(urgency) ||
    !hasExactKeys(urgency, ['type', 'instructions']) ||
    urgency.type !== 'noul' ||
    !nonblank(urgency.instructions)
  )
    return false
  const policy = value.policy
  if (
    !isRecord(policy) ||
    !hasExactKeys(policy, ['version', 'choice', 'score', 'noul']) ||
    policy.version !== 'text-judgment-adoption/1' ||
    !isRecord(policy.choice) ||
    !hasExactKeys(policy.choice, ['minConfidence', 'minProbability', 'requireUniqueMaximum']) ||
    policy.choice.minConfidence !== 0.7 ||
    policy.choice.minProbability !== 0.7 ||
    policy.choice.requireUniqueMaximum !== true ||
    !isRecord(policy.score) ||
    !hasExactKeys(policy.score, ['requiredImpactEvidence', 'minConfidence', 'highFrom']) ||
    policy.score.requiredImpactEvidence !== 'present' ||
    policy.score.minConfidence !== 0.7 ||
    policy.score.highFrom !== 1.5 ||
    !isRecord(policy.noul) ||
    !hasExactKeys(policy.noul, ['urgentFrom', 'notUrgentThrough']) ||
    policy.noul.urgentFrom !== 0.8 ||
    policy.noul.notUrgentThrough !== 0.2
  )
    return false
  if (!isRecord(value.normalization) || !hasExactKeys(value.normalization, judgmentIds))
    return false
  for (const id of judgmentIds) {
    const decision = value.normalization[id]
    if (
      !isRecord(decision) ||
      !hasExactKeys(decision, ['status', 'reasons', 'checks']) ||
      !inSet(decision.status, ['eligible', 'unmentioned', 'needs_review']) ||
      !Array.isArray(decision.reasons) ||
      decision.reasons.length === 0 ||
      !decision.reasons.every((reason) => inSet(reason, reasons)) ||
      !Array.isArray(decision.checks)
    )
      return false
    for (const check of decision.checks) {
      if (
        !isRecord(check) ||
        !hasExactKeys(check, ['rule', 'actual', 'operator', 'expected', 'passed']) ||
        !inSet(check.rule, checkRules) ||
        !inSet(check.operator, ['gte', 'lte', 'eq']) ||
        !scalar(check.actual) ||
        !scalar(check.expected) ||
        typeof check.passed !== 'boolean'
      )
        return false
    }
  }
  return true
}

function matchesRequest(value: JudgmentInspection, request: JudgmentRequest): boolean {
  const state = value.state
  const context = request.context
  return (
    request.contractVersion === 2 &&
    state.currentText === request.text &&
    state.questionId === context.question &&
    state.impact === context.impact &&
    state.recentUserTexts.length === context.recentUserTexts.length &&
    state.recentUserTexts.every((text, index) => text === context.recentUserTexts[index]) &&
    state.confirmed.topic === context.confirmed.topic &&
    state.confirmed.scope === context.confirmed.scope &&
    state.confirmed.workaround === context.confirmed.workaround &&
    state.confirmed.urgency === context.confirmed.urgency
  )
}

// 検証済みのJSON値だけを複製し、変更できない状態にする。元の応答を後から変更しても、複製した値は変わらない。
function freezeValue<T>(value: T): T {
  if (typeof value === 'object' && value !== null) {
    for (const child of Object.values(value)) freezeValue(child)
    Object.freeze(value)
  }
  return value
}

export function parseJudgmentResponse(
  value: unknown,
  request: JudgmentRequest,
): Parsed<JudgmentResponse> {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      'contractVersion',
      'consultationId',
      'requestId',
      'revision',
      'model',
      'evidence',
      'details',
      'inspection',
    ]) ||
    value.contractVersion !== 2 ||
    !isUuid(value.consultationId) ||
    !isUuid(value.requestId) ||
    !Number.isSafeInteger(value.revision) ||
    (value.revision as number) < 0 ||
    typeof value.model !== 'string' ||
    value.model.length === 0 ||
    !isRecord(value.evidence) ||
    !hasExactKeys(value.evidence, [
      'topic',
      'relevance',
      'change',
      'scope',
      'workaround',
      'result',
      'impact',
      'urgency',
    ]) ||
    !isRecord(value.details) ||
    !hasExactKeys(value.details, ['choices', 'score', 'noul', 'jevElapsedMs'])
  )
    return protocolError()

  if (
    !isInspection(value.inspection) ||
    !matchesRequest(value.inspection, request) ||
    value.consultationId !== request.consultationId ||
    value.requestId !== request.requestId ||
    value.revision !== request.revision
  )
    return protocolError()

  const evidence = value.evidence
  const topic = parseEvidence<Topic | 'both'>(
    evidence.topic,
    (candidate): candidate is Topic | 'both' =>
      inSet(candidate, ['missing_notification', 'notification_settings', 'both']),
  )
  const scope = parseEvidence<Scope>(evidence.scope, (candidate): candidate is Scope =>
    inSet(candidate, ['all', 'specific', 'unknown']),
  )
  const workaround = parseEvidence<Workaround>(
    evidence.workaround,
    (candidate): candidate is Workaround =>
      inSet(candidate, ['can_read', 'cannot_read', 'unknown']),
  )
  const result = parseEvidence<ResultAnswer>(
    evidence.result,
    (candidate): candidate is ResultAnswer =>
      inSet(candidate, ['done', 'not_done', 'not_tried', 'cannot_check']),
  )
  const urgency = parseEvidence<boolean>(
    evidence.urgency,
    (candidate): candidate is boolean => typeof candidate === 'boolean',
  )
  if (
    topic === null ||
    scope === null ||
    workaround === null ||
    result === null ||
    urgency === null ||
    !inSet(evidence.relevance, ['in_scope', 'mixed', 'out_of_scope', 'needs_review']) ||
    !inSet(evidence.change, ['keep', 'restart', 'needs_review']) ||
    !inSet(evidence.impact, ['low', 'high', 'needs_review'])
  )
    return protocolError()

  const details = value.details
  if (
    !isRecord(details.choices) ||
    !hasExactKeys(details.choices, Object.keys(choiceCandidates)) ||
    !isRecord(details.noul) ||
    !hasExactKeys(details.noul, ['type', 'noul']) ||
    details.noul.type !== 'noul' ||
    !isProbability(details.noul.noul) ||
    !isFiniteNumber(details.jevElapsedMs) ||
    details.jevElapsedMs < 0
  )
    return protocolError()
  const choices: Record<string, ChoiceDetail> = {}
  for (const [id, candidates] of Object.entries(choiceCandidates)) {
    const parsed = parseChoice(details.choices[id], candidates)
    if (parsed === null) return protocolError()
    choices[id] = parsed
  }
  const score = parseScore(details.score)
  if (score === null) return protocolError()

  return {
    ok: true,
    value: {
      contractVersion: 2,
      consultationId: value.consultationId,
      requestId: value.requestId,
      revision: value.revision,
      model: value.model,
      evidence: {
        topic,
        relevance: evidence.relevance,
        change: evidence.change,
        scope,
        workaround,
        result,
        impact: evidence.impact,
        urgency,
      },
      inspection: freezeValue(structuredClone(value.inspection)),
      details: {
        choices: choices as JudgmentResponse['details']['choices'],
        score,
        noul: { type: 'noul', noul: details.noul.noul },
        jevElapsedMs: details.jevElapsedMs,
      },
    },
  }
}
