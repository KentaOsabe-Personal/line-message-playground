import { readFileSync } from 'node:fs'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, test, vi } from 'vitest'
import TextJudgmentDetails from '../src/TextJudgmentDetails'
import { createLabHttpClient } from '../src/textJudgmentLabApi'
import { parseJudgmentResponse } from '../src/textJudgmentLabDto'
import { getQuestion } from '../src/textJudgmentLabContent'
import type { JudgmentRequest, JudgmentResponse } from '../src/textJudgmentLabTypes'

type Case = {
  id: string
  request: JudgmentRequest
  response: JudgmentResponse
  sent: { model: string; state: unknown; questions: unknown }
}
const fixture = JSON.parse(
  readFileSync('test/fixtures/text-judgment-lab-contract-v2.json', 'utf8'),
) as {
  cases: Case[]
  errors: Record<string, { status: number; body: unknown; frontendCode: string }>
  access: { status: 'authorized'; expiresAt: string; serverTime: string }
}

describe('v2共有契約', () => {
  // テストケース: 2種類の相談の各質問について、共有テストデータの応答をHTTPクライアントと判定詳細の表示処理へ渡す。
  // 期待値: 要求はv2形式でcookieを付けずに送信する。判定の閲覧情報に含まれる文脈、質問、候補、段階は画面表示と一致する。
  test.each(fixture.cases)(
    '$idの文脈・質問・公開inspectionを照合する',
    async ({ request, response, sent }) => {
      const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(response)))
      const result = await createLabHttpClient(fetcher).judge('safe-test-token', request)
      expect(result).toEqual(response)
      const [, options] = fetcher.mock.calls[0] as [string, RequestInit]
      expect(options.credentials).toBe('omit')
      expect(options.cache).toBe('no-store')
      expect(JSON.parse(options.body as string)).toEqual(request)
      const question = request.context.question
      const topic = request.context.confirmed.topic
      const contentId =
        question === 'start'
          ? 'topic'
          : question === 'scope'
            ? topic === 'notification_settings'
              ? 'settings_scope'
              : 'missing_scope'
            : question === 'result'
              ? topic === 'notification_settings'
                ? 'settings_result'
                : 'missing_result'
              : question
      expect(result.inspection.state.questionText).toBe(getQuestion(contentId).prompt)
      expect(result.inspection.state).toEqual(sent.state)
      expect(result.inspection.questions).toEqual(sent.questions)
      expect(result.model).toBe(sent.model)
      expect(result.inspection.questionVersion).toBe('text-judgment-questions/2')
      expect(result.inspection.questions.urgency).not.toHaveProperty('criteria')
      for (const [id, detail] of Object.entries(result.details.choices)) {
        expect(Object.keys(detail.probabilities).sort()).toEqual(
          Object.keys(
            result.inspection.questions[id as keyof typeof result.details.choices].criteria,
          ).sort(),
        )
      }
      expect(result.inspection.questions.impact.criteria).toEqual(
        Object.values(result.details.score.legend),
      )
      const html = renderToStaticMarkup(<TextJudgmentDetails judgment={result} uiElapsedMs={100} />)
      expect(html).toContain('判定質問の設計')
      expect(html).toContain('急ぎの要望がある確率')
      expect(html).toContain('text-judgment-adoption/1')
    },
  )

  // テストケース: 共有テストデータの安全なエラー応答をHTTPクライアントへ渡す。
  // 期待値: 定義済みのエラー分類だけを返し、成功結果の一部を採用しない。
  test.each(Object.entries(fixture.errors))('%sの安全な失敗を維持する', async (_name, error) => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(error.body), { status: error.status }))
    await expect(
      createLabHttpClient(fetcher).judge('safe-test-token', fixture.cases[0].request),
    ).rejects.toMatchObject({ code: error.frontendCode })
  })

  // テストケース: 正常な応答に含まれる判定の閲覧情報、型、理由、数値、版、要求との対応関係を、一項目ずつ不正な値に変える。
  // 期待値: 不正な応答は全体を拒否し、結果の一部を会話へ渡さない。
  test.each([
    ['inspection欠損', ['inspection'], undefined],
    ['質問type', ['inspection', 'questions', 'topic', 'type'], 'noul'],
    ['理由', ['inspection', 'normalization', 'topic', 'reasons'], ['invented']],
    ['非有限数', ['details', 'score', 'score'], NaN],
    ['相談ID', ['consultationId'], 'different'],
    ['要求ID', ['requestId'], 'different'],
    ['revision', ['revision'], -1],
    ['本文相関', ['inspection', 'state', 'currentText'], '別の本文'],
    ['v1応答', ['contractVersion'], 1],
  ] as const)('%sを完全失敗にする', (_label, path, replacement) => {
    const value: unknown = structuredClone(fixture.cases[0].response)
    let target = value as Record<string, unknown>
    for (const key of path.slice(0, -1)) target = target[key] as Record<string, unknown>
    target[path[path.length - 1]] = replacement
    expect(parseJudgmentResponse(value, fixture.cases[0].request).ok).toBe(false)
  })

  // テストケース: 許可されていない項目を含む過大なJSONと、旧版の応答を受け取る。
  // 期待値: HTTPクライアントは契約に合わない応答を全体として拒否し、利用確認の応答は従来どおり受け取る。
  test('不正な過大応答と旧版応答を拒否しaccessを維持する', async () => {
    const { request, response } = fixture.cases[0]
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ ...response, padding: 'x'.repeat(256 * 1024) })),
      )
    await expect(
      createLabHttpClient(fetcher).judge('safe-test-token', request),
    ).rejects.toMatchObject({ code: 'protocol_error' })
    expect(parseJudgmentResponse({ ...response, contractVersion: 1 }, request).ok).toBe(false)
    const accessFetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(fixture.access)))
    expect(await createLabHttpClient(accessFetch).checkAccess('safe-test-token')).toEqual(
      fixture.access,
    )
  })
})
