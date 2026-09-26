import type {
  ChoiceDetail,
  Evidence,
  JudgmentResponse,
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
const isUuid = (value: unknown): value is string => typeof value === 'string' && uuidPattern.test(value)
const isFiniteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const isProbability = (value: unknown): value is number => isFiniteNumber(value) && value >= 0 && value <= 1
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
  if (value.kind === 'needs_review' && hasExactKeys(value, ['kind'])) return { kind: 'needs_review' }
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
  if (!isRecord(value) || !hasExactKeys(value, ['type', 'choice', 'probabilities', 'confidence']) ||
    value.type !== 'choice' || !inSet(value.choice, candidates) || !isProbability(value.confidence) ||
    !isRecord(value.probabilities) || !hasExactKeys(value.probabilities, candidates)) return null
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
  if (!isRecord(value) || !hasExactKeys(value, ['type', 'score', 'legend', 'probabilities', 'confidence']) ||
    value.type !== 'score' || !isFiniteNumber(value.score) || value.score < 0 || value.score > 2 ||
    !isProbability(value.confidence) || !isRecord(value.legend) || !hasExactKeys(value.legend, ['0', '1', '2']) ||
    value.legend['0'] !== '支障なし' || value.legend['1'] !== '不便だが別の操作で目的を達成できる' ||
    value.legend['2'] !== '目的を達成できない' || !isRecord(value.probabilities) ||
    !hasExactKeys(value.probabilities, ['0', '1', '2'])) return null
  const probabilities = value.probabilities
  if (!isProbability(probabilities['0']) || !isProbability(probabilities['1']) ||
    !isProbability(probabilities['2']) || Math.abs(probabilities['0'] + probabilities['1'] + probabilities['2'] - 1) > 0.01) return null
  return {
    type: 'score', score: value.score, legend: { '0': value.legend['0'], '1': value.legend['1'], '2': value.legend['2'] },
    probabilities: { '0': probabilities['0'], '1': probabilities['1'], '2': probabilities['2'] },
    confidence: value.confidence,
  }
}

export function parseLabAccessResponse(value: unknown): Parsed<LabAccessResponse> {
  if (!isRecord(value) || !hasExactKeys(value, ['status', 'expiresAt', 'serverTime']) ||
    value.status !== 'authorized' || !isTimestamp(value.expiresAt) || !isTimestamp(value.serverTime)) return protocolError()
  return { ok: true, value: { status: 'authorized', expiresAt: value.expiresAt, serverTime: value.serverTime } }
}

export function parseJudgmentResponse(value: unknown): Parsed<JudgmentResponse> {
  if (!isRecord(value) || !hasExactKeys(value, [
    'contractVersion', 'consultationId', 'requestId', 'revision', 'model', 'evidence', 'details',
  ]) || value.contractVersion !== 1 || !isUuid(value.consultationId) || !isUuid(value.requestId) ||
    !Number.isSafeInteger(value.revision) || (value.revision as number) < 0 ||
    typeof value.model !== 'string' || value.model.length === 0 || !isRecord(value.evidence) ||
    !hasExactKeys(value.evidence, ['topic', 'relevance', 'change', 'scope', 'workaround', 'result', 'impact', 'urgency']) ||
    !isRecord(value.details) || !hasExactKeys(value.details, ['choices', 'score', 'noul', 'jevElapsedMs'])) return protocolError()

  const evidence = value.evidence
  const topic = parseEvidence<Topic | 'both'>(evidence.topic, (candidate): candidate is Topic | 'both' =>
    inSet(candidate, ['missing_notification', 'notification_settings', 'both']))
  const scope = parseEvidence<Scope>(evidence.scope, (candidate): candidate is Scope => inSet(candidate, ['all', 'specific', 'unknown']))
  const workaround = parseEvidence<Workaround>(evidence.workaround, (candidate): candidate is Workaround =>
    inSet(candidate, ['can_read', 'cannot_read', 'unknown']))
  const result = parseEvidence<ResultAnswer>(evidence.result, (candidate): candidate is ResultAnswer =>
    inSet(candidate, ['done', 'not_done', 'not_tried', 'cannot_check']))
  const urgency = parseEvidence<boolean>(evidence.urgency, (candidate): candidate is boolean => typeof candidate === 'boolean')
  if (topic === null || scope === null || workaround === null || result === null || urgency === null ||
    !inSet(evidence.relevance, ['in_scope', 'mixed', 'out_of_scope', 'needs_review']) ||
    !inSet(evidence.change, ['keep', 'restart', 'needs_review']) ||
    !inSet(evidence.impact, ['low', 'high', 'needs_review'])) return protocolError()

  const details = value.details
  if (!isRecord(details.choices) || !hasExactKeys(details.choices, Object.keys(choiceCandidates)) ||
    !isRecord(details.noul) || !hasExactKeys(details.noul, ['type', 'noul']) || details.noul.type !== 'noul' ||
    !isProbability(details.noul.noul) || !isFiniteNumber(details.jevElapsedMs) || details.jevElapsedMs < 0) return protocolError()
  const choices: Record<string, ChoiceDetail> = {}
  for (const [id, candidates] of Object.entries(choiceCandidates)) {
    const parsed = parseChoice(details.choices[id], candidates)
    if (parsed === null) return protocolError()
    choices[id] = parsed
  }
  const score = parseScore(details.score)
  if (score === null) return protocolError()

  return { ok: true, value: {
    contractVersion: 1, consultationId: value.consultationId, requestId: value.requestId,
    revision: value.revision as number, model: value.model,
    evidence: {
      topic, relevance: evidence.relevance, change: evidence.change, scope, workaround, result,
      impact: evidence.impact, urgency,
    },
    details: {
      choices: choices as JudgmentResponse['details']['choices'], score,
      noul: { type: 'noul', noul: details.noul.noul }, jevElapsedMs: details.jevElapsedMs,
    },
  } }
}
