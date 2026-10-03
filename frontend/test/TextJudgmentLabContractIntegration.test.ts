import { v2Request } from './textJudgmentLabV2Fixture'
import { describe, expect, test, vi } from 'vitest'

import fixture from './fixtures/text-judgment-lab-contract-v1.json'
import { createLabHttpClient } from '../src/textJudgmentLabApi'
import { parseJudgmentResponse } from '../src/textJudgmentLabDto'
import type { JudgmentRequest } from '../src/textJudgmentLabTypes'


describe('Frontend・Backend共有契約', () => {
  // テストケース: Backend共有fixtureの成功応答をFrontend DTO境界へ渡す。
  // 期待値: v1のテストデータを旧版の拒否を確認する資料として保持し、応答を会話に反映しない。
  test('rejects the shared v1 response', () => {
    expect(parseJudgmentResponse(fixture.response, v2Request()).ok).toBe(false)
  })

  // テストケース: Backend共有fixtureのerrorと将来contractVersionを受け取る。
  // 期待値: errorを固定Frontend分類へ縮約し、契約ずれは全体を拒否する。
  test('maps shared errors and rejects future contract versions', async () => {
    const unavailable = fixture.errors.judgmentUnavailable
    const timeout = fixture.errors.judgmentTimeout
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(unavailable.body), { status: unavailable.status }))
      .mockResolvedValueOnce(new Response(JSON.stringify(timeout.body), { status: timeout.status }))
    const client = createLabHttpClient(fetcher)

    await expect(client.judge('id-token', { ...fixture.request, contractVersion: 2 } as JudgmentRequest)).rejects.toMatchObject({ code: 'judgment_failed' })
    await expect(client.judge('id-token', { ...fixture.request, contractVersion: 2 } as JudgmentRequest)).rejects.toMatchObject({ code: 'judgment_failed' })
    expect(parseJudgmentResponse({ ...fixture.response, contractVersion: 2 }, v2Request()).ok).toBe(false)
  })
})
