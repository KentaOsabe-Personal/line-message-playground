import {
  intentLabels,
  type JudgmentResponse,
  type LabAccessResponse,
  type Parsed,
} from './textJudgmentLabTypes'

const protocolError = (): Parsed<never> => ({
  ok: false,
  error: { code: 'protocol_error', message: '応答形式を確認できません。' },
})
const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)
const hasExactKeys = (v: Record<string, unknown>, keys: readonly string[]) =>
  Object.keys(v).length === keys.length && keys.every((k) => k in v)
const number = (v: unknown, max = 1): v is number =>
  typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= max
const isTimestamp = (v: unknown): v is string =>
  typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(v) && Number.isFinite(Date.parse(v))
function distribution(v: unknown, keys: string[]): boolean {
  return (
    isRecord(v) &&
    hasExactKeys(v, keys) &&
    Object.values(v).every((x) => number(x)) &&
    Math.abs(Object.values(v).reduce<number>((sum, x) => sum + (x as number), 0) - 1) <= 0.01
  )
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

export function parseJudgmentResponse(value: unknown): Parsed<JudgmentResponse> {
  if (
    !isRecord(value) ||
    value.contractVersion !== 3 ||
    typeof value.model !== 'string' ||
    !value.model.trim() ||
    !number(value.elapsedMs, Number.MAX_VALUE) ||
    !isRecord(value.answers)
  )
    return protocolError()
  const { intent, sentiment, urgency } = value.answers
  if (
    !isRecord(intent) ||
    intent.type !== 'choice' ||
    typeof intent.choice !== 'string' ||
    !Object.hasOwn(intentLabels, intent.choice) ||
    !number(intent.confidence) ||
    !distribution(intent.probabilities, Object.keys(intentLabels))
  )
    return protocolError()
  const probabilities = intent.probabilities as Record<string, number>
  if (probabilities[intent.choice] !== Math.max(...Object.values(probabilities)))
    return protocolError()
  if (
    !isRecord(sentiment) ||
    sentiment.type !== 'score' ||
    !number(sentiment.score, 2) ||
    !number(sentiment.confidence) ||
    !distribution(sentiment.probabilities, ['0', '1', '2']) ||
    !isRecord(sentiment.legend) ||
    !hasExactKeys(sentiment.legend, ['0', '1', '2']) ||
    !Object.values(sentiment.legend).every(
      (v) => typeof v === 'string' && v.length > 0 && v.length <= 200,
    )
  )
    return protocolError()
  if (!isRecord(urgency) || urgency.type !== 'noul' || !number(urgency.noul)) return protocolError()
  return { ok: true, value: structuredClone(value) as JudgmentResponse }
}
