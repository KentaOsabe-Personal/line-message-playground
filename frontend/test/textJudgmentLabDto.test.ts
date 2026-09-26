import { describe, expect, test } from 'vitest'

import { parseLabAccessResponse, parseJudgmentResponse } from '../src/textJudgmentLabDto'

const choice = (selected: string, candidates: readonly string[]) => ({
  type: 'choice',
  choice: selected,
  probabilities: Object.fromEntries(candidates.map((candidate) => [candidate, candidate === selected ? 1 : 0])),
  confidence: 0.91,
})

const judgment = () => ({
  contractVersion: 1,
  consultationId: '11111111-1111-4111-8111-111111111111',
  requestId: 'abcdefab-cdef-4abc-8def-abcdefabcdef',
  revision: 2,
  model: 'jev-1.13.0',
  evidence: {
    topic: { kind: 'known', value: 'missing_notification' },
    relevance: 'in_scope',
    change: 'keep',
    scope: { kind: 'unmentioned' },
    workaround: { kind: 'needs_review' },
    result: { kind: 'unmentioned' },
    impact: 'high',
    urgency: { kind: 'known', value: true },
  },
  details: {
    choices: {
      topic: choice('missing_notification', ['missing_notification', 'notification_settings', 'both', 'unmentioned', 'unclear']),
      relevance: choice('in_scope', ['in_scope', 'mixed', 'out_of_scope', 'unclear']),
      change: choice('keep', ['keep', 'restart', 'unclear']),
      scope: choice('unmentioned', ['all', 'specific', 'unknown', 'unmentioned', 'unclear']),
      workaround: choice('unclear', ['can_read', 'cannot_read', 'unknown', 'unmentioned', 'unclear']),
      result: choice('unmentioned', ['done', 'not_done', 'not_tried', 'cannot_check', 'unmentioned', 'unclear']),
      impact_evidence: choice('present', ['present', 'absent', 'unclear']),
    },
    score: {
      type: 'score', score: 1.75,
      legend: { '0': '支障なし', '1': '不便だが別の操作で目的を達成できる', '2': '目的を達成できない' },
      probabilities: { '0': 0.05, '1': 0.2, '2': 0.75 }, confidence: 0.88,
    },
    noul: { type: 'noul', noul: 0.82 },
    jevElapsedMs: 321.4,
  },
})

describe('text judgment lab DTO', () => {
  // テストケース: accessと完全な判定応答をunknownから解析する。
  // 期待値: closed unionと有限な公開判定だけを会話へ渡す。
  test('parses exact access and judgment responses', () => {
    expect(parseLabAccessResponse({
      status: 'authorized', expiresAt: '2026-09-20T12:00:00Z', serverTime: '2026-09-20T11:55:00Z',
    })).toMatchObject({ ok: true, value: { status: 'authorized' } })

    expect(parseJudgmentResponse(judgment())).toEqual({ ok: true, value: judgment() })
  })

  // テストケース: 未知field、contractVersion、enum、必須field欠落を混入する。
  // 期待値: protocol_errorとして全体を拒否し部分結果を返さない。
  test('rejects unknown, incomplete, and future contract responses', () => {
    const future = { ...judgment(), contractVersion: 2 }
    const extra = { ...judgment(), rawResponse: {} }
    const unknown = judgment()
    unknown.evidence.relevance = 'maybe'
    const incomplete = judgment()
    delete (incomplete.details.choices as Record<string, unknown>).result

    for (const value of [future, extra, unknown, incomplete]) {
      expect(parseJudgmentResponse(value)).toMatchObject({ ok: false, error: { code: 'protocol_error' } })
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
    probability.details.score.probabilities['2'] = 1.1
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
      expect(parseJudgmentResponse(value).ok).toBe(false)
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
