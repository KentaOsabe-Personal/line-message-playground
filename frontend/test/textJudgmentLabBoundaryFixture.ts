import fixture from './fixtures/text-judgment-lab-boundaries-v2.json'
import { getQuestion } from '../src/textJudgmentLabContent'
import { parseJudgmentResponse } from '../src/textJudgmentLabDto'
import type { JudgmentRequest, JudgmentResponse } from '../src/textJudgmentLabTypes'

export const boundaryFixture = fixture
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
export function mergePatch(target: Record<string, unknown>, patch: Record<string, unknown>) {
  for (const [key, value] of Object.entries(patch)) {
    const current = target[key]
    if (object(value) && object(current) && !('kind' in value)) mergePatch(current, value)
    else target[key] = structuredClone(value)
  }
}

// 固定応答をBackendの正規化結果と照合する。この応答は、実APIの文章理解や判定精度を示すものではない。
export function boundaryResponse(
  request: JudgmentRequest,
  id?: string,
  patch: Record<string, unknown> = {},
): JudgmentResponse {
  const value = structuredClone(fixture.response)
  if (id !== undefined) {
    const scenario = fixture.cases.find((item) => item.id === id)
    if (!scenario) throw new Error(`Unknown boundary: ${id}`)
    mergePatch(value, scenario.responsePatch)
  }
  mergePatch(value, patch)
  value.consultationId = request.consultationId
  value.requestId = request.requestId
  value.revision = request.revision
  const question = request.context.question
  const settings = request.context.confirmed.topic === 'notification_settings'
  const contentId =
    question === 'start' || question === 'topic'
      ? 'topic'
      : question === 'scope'
        ? settings
          ? 'settings_scope'
          : 'missing_scope'
        : question === 'result'
          ? settings
            ? 'settings_result'
            : 'missing_result'
          : question
  mergePatch(value.inspection.state, {
    currentText: request.text,
    questionId: question,
    questionText: getQuestion(contentId).prompt,
    confirmed: request.context.confirmed,
    impact: request.context.impact,
    recentUserTexts: request.context.recentUserTexts,
  })
  const parsed = parseJudgmentResponse(value, request)
  if (!parsed.ok) throw new Error('Invalid boundary fixture')
  return parsed.value
}
