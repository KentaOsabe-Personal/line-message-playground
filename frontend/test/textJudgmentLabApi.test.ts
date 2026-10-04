import { describe, expect, test, vi } from 'vitest'
import { createLabHttpClient } from '../src/textJudgmentLabApi'
import { parseJudgmentResponse } from '../src/textJudgmentLabDto'
import fixture from './fixtures/text-judgment-lab-v3.json'

describe('自由文判定のHTTP境界', () => {
  // テストケース: メッセージを送信して共有fixtureの成功応答を受け取る。
  // 期待値: CookieなしのBearer通信で本文のみを送り、値を変更せず返す。
  test('uses the shared backend contract and explicit bearer', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(fixture)))
    const client = createLabHttpClient(fetcher)
    await expect(
      client.judge('test-token', { contractVersion: 3, text: '自由文' }),
    ).resolves.toEqual(fixture)
    expect(fetcher).toHaveBeenCalledWith(
      '/api/labs/text-judgment/judgments',
      expect.objectContaining({
        credentials: 'omit',
        cache: 'no-store',
        redirect: 'error',
        headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' },
        body: JSON.stringify({ contractVersion: 3, text: '自由文' }),
      }),
    )
  })

  // テストケース: 旧応答・欠損・範囲外・不整合の判定値を受け取る。
  // 期待値: UIへ不正データを渡さずprotocol_errorになる。
  test('rejects malformed judgment results', () => {
    const invalid = [
      null,
      { ...fixture, contractVersion: 2 },
      { ...fixture, elapsedMs: -1 },
      { ...fixture, answers: {} },
      { ...fixture, answers: { ...fixture.answers, urgency: { type: 'noul', noul: 1.2 } } },
      {
        ...fixture,
        answers: { ...fixture.answers, intent: { ...fixture.answers.intent, choice: 'other' } },
      },
      {
        ...fixture,
        answers: { ...fixture.answers, sentiment: { ...fixture.answers.sentiment, score: NaN } },
      },
    ]
    for (const value of invalid) expect(parseJudgmentResponse(value).ok).toBe(false)
  })

  // テストケース: APIから429や不正JSONが戻る。
  // 期待値: 意味のある失敗分類に変換し、自動再試行しない。
  test('maps failures without retrying', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response('{}', { status: 429 }))
      .mockResolvedValueOnce(new Response('not json'))
    const client = createLabHttpClient(fetcher)
    await expect(
      client.judge('test-token', { contractVersion: 3, text: '自由文' }),
    ).rejects.toMatchObject({ code: 'rate_limited' })
    await expect(
      client.judge('test-token', { contractVersion: 3, text: '自由文' }),
    ).rejects.toMatchObject({ code: 'protocol_error' })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
})
