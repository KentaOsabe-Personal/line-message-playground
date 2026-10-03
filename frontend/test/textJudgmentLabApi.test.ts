import { describe, expect, test, vi } from 'vitest'

import { createLabHttpClient, LabHttpError } from '../src/textJudgmentLabApi'
import type { JudgmentRequest } from '../src/textJudgmentLabTypes'


const request: JudgmentRequest = {
  contractVersion: 2,
  consultationId: '12345678-1234-4234-8234-123456789012',
  requestId: '22345678-1234-4234-8234-123456789012',
  revision: 0,
  text: '通知が届きません',
  context: {
    question: 'start',
    confirmed: { topic: null, scope: null, workaround: null, urgency: null },
    recentUserTexts: [],
    impact: 'unassessed',
  },
}


describe('text judgment lab HTTP client', () => {
  // テストケース: accessとjudgmentをBearer tokenで呼ぶ
  // 期待値: canonical相対path、credentials omit、no-storeを使い、検証済みDTOだけを返す
  test('uses isolated bearer requests without cookies or caching', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        status: 'authorized', expiresAt: '2026-09-21T01:00:00Z', serverTime: '2026-09-21T00:00:00Z',
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ broken: true }), { status: 200 }))
    const client = createLabHttpClient(fetcher)

    await expect(client.checkAccess('id-token')).resolves.toMatchObject({ status: 'authorized' })
    await expect(client.judge('id-token', request)).rejects.toMatchObject({ code: 'protocol_error' })

    expect(fetcher.mock.calls[0]).toEqual(['/api/labs/text-judgment/access', {
      method: 'POST', credentials: 'omit', cache: 'no-store', redirect: 'error',
      headers: { Authorization: 'Bearer id-token', 'Content-Type': 'application/json' },
      body: '{}', signal: undefined,
    }])
    expect(fetcher.mock.calls[1][0]).toBe('/api/labs/text-judgment/judgments')
    expect(fetcher.mock.calls[1][1]).toMatchObject({
      credentials: 'omit', cache: 'no-store', body: JSON.stringify(request),
    })
  })

  // テストケース: 認証・権限・制限・依存障害・通信失敗が発生する
  // 期待値: 応答本文やtokenを漏らさず会話・認証用の固定失敗へ変換する
  test.each([
    [401, 'authentication_required', 'reauthentication_required'],
    [403, 'wrong_channel', 'wrong_channel'],
    [403, 'not_allowed', 'not_allowed'],
    [429, 'rate_limited', 'rate_limited'],
    [502, 'judge_unavailable', 'judgment_failed'],
    [503, 'configuration_unavailable', 'access_unavailable'],
    [504, 'judge_timeout', 'judgment_failed'],
  ])('maps HTTP %s %s to %s', async (status, backendCode, expected) => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: backendCode, message: 'secret-canary' },
    }), { status }))
    const client = createLabHttpClient(fetcher)

    await expect(client.checkAccess('id-token')).rejects.toMatchObject({ code: expected })
    try {
      await client.checkAccess('id-token')
    } catch (error) {
      expect(String(error)).not.toContain('secret-canary')
      expect(String(error)).not.toContain('id-token')
    }
  })

  test('maps network failures without automatic retry', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('token-canary'))
    const client = createLabHttpClient(fetcher)
    await expect(client.checkAccess('id-token')).rejects.toEqual(new LabHttpError('access_unavailable'))
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  test('maps judgment network failures separately from access outages', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('network'))
    const client = createLabHttpClient(fetcher)
    await expect(client.judge('id-token', request)).rejects.toMatchObject({ code: 'judgment_failed' })
  })
})

// テストケース: 専用HTTPクライアントに、完全なv2応答と送信要求に一致しない応答を返す。
// 期待値: 完全な成功応答だけを返し、送信要求との不一致はprotocol_errorとして扱う。
test('passes the sent request to the v2 success parser', async () => {
  const { v2Response } = await import('./textJudgmentLabV2Fixture')
  const success = v2Response(request)
  const mismatch = structuredClone(success)
  Object.assign(mismatch.inspection.state, { currentText: '別の入力' })
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(success)))
    .mockResolvedValueOnce(new Response(JSON.stringify(mismatch)))
  const client = createLabHttpClient(fetcher)
  await expect(client.judge('id-token', request)).resolves.toEqual(success)
  await expect(client.judge('id-token', request)).rejects.toMatchObject({ code: 'protocol_error' })
})

// テストケース: 送信開始後、呼び出し元で要求の本文と文脈を変更する。
// 期待値: 送信時に保存した要求の内容と応答を照合する。
test('retains the sent request when the caller mutates its input', async () => {
  const { v2Response } = await import('./textJudgmentLabV2Fixture')
  const input = structuredClone(request)
  const success = v2Response(input)
  let finish!: (response: Response) => void
  const fetcher = vi.fn((_url: RequestInfo | URL, _init?: RequestInit) => new Promise<Response>(resolve => { finish = resolve }))
  const pending = createLabHttpClient(fetcher).judge('id-token', input)
  input.text = '変更した入力'
  input.context.confirmed.scope = 'all'
  finish(new Response(JSON.stringify(success)))
  await expect(pending).resolves.toEqual(success)
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual(request)
})
