import { describe, expect, test } from 'vitest'

import { parseLabAccessResponse, parseJudgmentResponse } from '../src/textJudgmentLabDto'

import { v2Response as judgment, v2Request } from './textJudgmentLabV2Fixture'

describe('text judgment lab DTO', () => {
  // テストケース: accessと完全な判定応答をunknownから解析する。
  // 期待値: closed unionと有限な公開判定だけを会話へ渡す。
  test('parses exact access and judgment responses', () => {
    expect(parseLabAccessResponse({
      status: 'authorized', expiresAt: '2026-09-20T12:00:00Z', serverTime: '2026-09-20T11:55:00Z',
    })).toMatchObject({ ok: true, value: { status: 'authorized' } })

    expect(parseJudgmentResponse(judgment(), v2Request())).toEqual({ ok: true, value: judgment() })
  })

  // テストケース: 未知field、contractVersion、enum、必須field欠落を混入する。
  // 期待値: protocol_errorとして全体を拒否し部分結果を返さない。
  test('rejects unknown, incomplete, and future contract responses', () => {
    const future = { ...judgment(), contractVersion: 3 }
    const extra = { ...judgment(), rawResponse: {} }
    const unknown = judgment()
    Object.assign(unknown.evidence, { relevance: 'maybe' })
    const incomplete = judgment()
    delete (incomplete.details.choices as Record<string, unknown>).result

    for (const value of [future, extra, unknown, incomplete]) {
      expect(parseJudgmentResponse(value, v2Request())).toMatchObject({ ok: false, error: { code: 'protocol_error' } })
    }
  })

  // テストケース: NaN、Infinity、範囲外確率、候補不一致を混入する。
  // 期待値: 非有限値や意味の壊れた数値を会話へ入れない。
  test('rejects non-finite, out-of-range, and mismatched numeric details', () => {
    const nan = judgment()
    nan.details.score.score = Number.NaN
    const infinity = judgment()
    infinity.details.jevElapsedMs = Number.POSITIVE_INFINITY
    const probability = judgment()
    Object.assign(probability.details.score.probabilities, { '2': 1.1 })
    const choiceMismatch = judgment()
    choiceMismatch.details.choices.topic.choice = 'other'
    const nonMaximumChoice = judgment()
    nonMaximumChoice.details.choices.topic.probabilities = {
      missing_notification: 0.1,
      notification_settings: 0.9,
      both: 0,
      unmentioned: 0,
      unclear: 0,
    }

    for (const value of [nan, infinity, probability, choiceMismatch, nonMaximumChoice]) {
      expect(parseJudgmentResponse(value, v2Request()).ok).toBe(false)
    }
  })

  // テストケース: access応答にnaive日時や余分な本人情報を含める。
  // 期待値: token・profileを含み得る曖昧な応答を拒否する。
  test('rejects unsafe access response shapes', () => {
    expect(parseLabAccessResponse({
      status: 'authorized', expiresAt: '2026-09-20T12:00:00', serverTime: '2026-09-20T11:55:00Z',
    }).ok).toBe(false)
    expect(parseLabAccessResponse({
      status: 'authorized', expiresAt: '2026-09-20T12:00:00Z', serverTime: '2026-09-20T11:55:00Z', profile: {},
    }).ok).toBe(false)
  })
})

// テストケース: 完全なv2応答と、その判定で送信した要求を渡す。
// 期待値: 閲覧情報を受け入れ、送信要求との一致を確認する。
test('accepts v2 inspection correlated with its request', () => {
  const request = v2Request()
  expect(parseJudgmentResponse(judgment(request), request)).toEqual({ ok: true, value: judgment(request) })
})

// テストケース: 旧版の応答、閲覧情報の欠落、型・候補・理由・状態・比較値の不正を一条件ずつ設定する。
// 期待値: 応答全体をprotocol_errorとして拒否し、部分的な成功として扱わない。
test('rejects malformed inspection and v1 responses', () => {
  const mutations: ((value: ReturnType<typeof judgment>) => void)[] = [
    value => { Object.assign(value, { contractVersion: 1 }) },
    value => { Reflect.deleteProperty(value, 'inspection') },
    value => { Object.assign(value.inspection, { questionVersion: 'future' }) },
    value => { Reflect.deleteProperty(value.inspection.questions, 'topic') },
    value => { Object.assign(value.inspection.questions.topic, { type: 'score' }) },
    value => { Object.assign(value.inspection.questions.topic.criteria, { other: '不明' }) },
    value => { Object.assign(value.inspection.questions.impact, { criteria: ['低', '高'] }) },
    value => { Object.assign(value.inspection.questions.urgency, { criteria: ['no', 'yes'] }) },
    value => { Object.assign(value.inspection.policy, { version: 'future' }) },
    value => { Object.assign(value.inspection.policy.choice, { minConfidence: 0.6 }) },
    value => { Object.assign(value.inspection.normalization.topic, { reasons: ['unknown'] }) },
    value => { Object.assign(value.inspection.normalization.topic, { status: 'unknown' }) },
    value => { Object.assign(value.inspection.normalization.topic.checks[0], { actual: Infinity }) },
    value => { Object.assign(value.inspection.normalization.topic.checks[0], { passed: 'true' }) },
    value => { Object.assign(value.inspection.normalization.topic.checks[0], { operator: 'gt' }) },
    value => { Object.assign(value.inspection.normalization.topic.checks[0], { rule: 'future' }) },
    value => { Object.assign(value.inspection.state.confirmed, { urgency: 1 }) },
    value => { Object.assign(value.inspection.state, { currentText: '違う入力' }) },
    value => { Object.assign(value.inspection.state, { questionId: 'result' }) },
    value => { Object.assign(value.inspection.state, { impact: 'high' }) },
    value => { Object.assign(value.inspection.state, { recentUserTexts: ['別の文脈'] }) },
    value => { Object.assign(value.inspection.state.confirmed, { scope: 'all' }) },
    value => { Object.assign(value, { requestId: '11111111-1111-4111-8111-111111111111' }) },
    value => { Object.assign(value, { revision: value.revision + 1 }) },
  ]
  for (const mutate of mutations) {
    const value = structuredClone(judgment())
    mutate(value)
    expect(parseJudgmentResponse(value, v2Request()).ok).toBe(false)
  }
})

// テストケース: 応答を解析した後、元の閲覧情報のマップと配列を変更する。
// 期待値: 解析結果の文脈、理由、丸め前の比較値は変わらず、内部の値も変更できない。
test('copies and freezes inspection collections without rounding comparisons', () => {
  const input = judgment()
  const decision = input.inspection.normalization.topic
  Object.assign(decision.checks[0], { actual: 0.6999, passed: false })
  const parsed = parseJudgmentResponse(input, v2Request())
  expect(parsed.ok).toBe(true)
  if (!parsed.ok) return
  Object.assign(input.inspection.state.confirmed, { scope: 'specific' })
  Object.assign(decision.checks[0], { actual: 0.7 })
  expect(parsed.value.inspection.state.confirmed.scope).toBe(null)
  expect(parsed.value.inspection.normalization.topic.checks[0].actual).toBe(0.6999)
  expect(Object.isFrozen(parsed.value.inspection.questions)).toBe(true)
  expect(Object.isFrozen(parsed.value.inspection.normalization.topic.checks[0])).toBe(true)
})
