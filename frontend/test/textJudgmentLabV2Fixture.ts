import fixture from './fixtures/text-judgment-lab-contract-v1.json'
import type {
  JudgmentId,
  JudgmentInspection,
  JudgmentRequest,
  JudgmentResponse,
  SentQuestion,
  NormalizationDecision,
} from '../src/textJudgmentLabTypes'

export const v2Request = (): JudgmentRequest =>
  ({
    ...structuredClone(fixture.request),
    contractVersion: 2,
  }) as JudgmentRequest

// タスク8の入出力検証に使うテストデータ。FrontendとBackendで共有するJSONはタスク12.2で作成する。
export function inspectionFor(request: JudgmentRequest): JudgmentInspection {
  const questions = Object.fromEntries(
    Object.entries(fixture.response.details.choices).map(([id, detail]) => [
      id,
      {
        type: 'choice',
        instructions: `${id}の判定`,
        criteria: Object.fromEntries(
          Object.keys(detail.probabilities).map((key) => [key, `${key}の条件`]),
        ),
      },
    ]),
  ) as Record<JudgmentId, SentQuestion>
  questions.impact = {
    type: 'score',
    instructions: '支障の判定',
    criteria: Object.values(fixture.response.details.score.legend),
  }
  questions.urgency = { type: 'noul', instructions: '急ぎの判定' }
  return {
    questionVersion: 'text-judgment-questions/2',
    state: {
      currentText: request.text,
      questionId: request.context.question,
      questionText: 'どちらについて相談しますか？',
      confirmed: { ...request.context.confirmed },
      impact: request.context.impact,
      recentUserTexts: [...request.context.recentUserTexts],
    },
    questions: questions as JudgmentInspection['questions'],
    policy: {
      version: 'text-judgment-adoption/1',
      choice: { minConfidence: 0.7, minProbability: 0.7, requireUniqueMaximum: true },
      score: { requiredImpactEvidence: 'present', minConfidence: 0.7, highFrom: 1.5 },
      noul: { urgentFrom: 0.8, notUrgentThrough: 0.2 },
    },
    normalization: (() => {
      const eligible: NormalizationDecision = {
        status: 'eligible',
        reasons: ['eligible'],
        checks: [
          {
            rule: 'confidence_below_threshold',
            actual: 0.91,
            operator: 'gte',
            expected: 0.7,
            passed: true,
          },
        ],
      }
      return {
        topic: eligible,
        relevance: eligible,
        change: eligible,
        scope: eligible,
        workaround: eligible,
        result: eligible,
        impact_evidence: eligible,
        impact: eligible,
        urgency: eligible,
      }
    })(),
  }
}

export function withInspection(
  response: Omit<JudgmentResponse, 'contractVersion' | 'inspection'> & { contractVersion?: number },
  request: JudgmentRequest = v2Request(),
): JudgmentResponse {
  return { ...response, contractVersion: 2, inspection: inspectionFor(request) }
}

export const v2Response = (request: JudgmentRequest = v2Request()): JudgmentResponse =>
  withInspection(
    {
      ...(structuredClone(fixture.response) as Omit<
        JudgmentResponse,
        'contractVersion' | 'inspection'
      >),
      consultationId: request.consultationId,
      requestId: request.requestId,
      revision: request.revision,
    },
    request,
  )
