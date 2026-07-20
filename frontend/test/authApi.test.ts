import { describe, expect, test, vi } from 'vitest'

import { AuthApiError, createAuthApiClient } from '../src/authApi'
import { parseSessionStatus } from '../src/authDto'
import type { ProtectedHttpClient } from '../src/httpApi'

const jsonResponse = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status,
  headers: { 'Content-Type': 'application/json' },
})

describe('session DTO and AuthApiClient', () => {
  // テストケース: anonymous・authenticated・unlinkingの正確なsession unionを検証する。
  // 期待値: 表示名と連携状態だけを受理し、未知fieldや矛盾したretry actionを拒否する。
  test('strictly parses the session response union', () => {
    expect(parseSessionStatus({ state: 'anonymous' })).toEqual({ ok: true, value: { state: 'anonymous' } })
    expect(parseSessionStatus({ state: 'authenticated', profile: { displayName: 'Owner', linked: true } })).toEqual({
      ok: true,
      value: { state: 'authenticated', profile: { displayName: 'Owner', linked: true } },
    })
    expect(parseSessionStatus({ state: 'anonymous', userId: 'secret' }).ok).toBe(false)
    expect(parseSessionStatus({ state: 'unlinking', stage: 'local_deletion_pending', retryAction: 'reauthenticate' }).ok).toBe(false)
  })

  // テストケース: bootstrap・raw ID token login・logoutを共通HTTP境界から呼ぶ。
  // 期待値: 正確なpath/method/bodyを使い、strict DTOを返す。
  test('calls all session operations through the protected HTTP client', async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ state: 'anonymous' }))
      .mockResolvedValueOnce(jsonResponse({ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }))
      .mockResolvedValueOnce(jsonResponse({ state: 'anonymous' }))
    const client = createAuthApiClient({ request } as ProtectedHttpClient)

    await expect(client.bootstrap()).resolves.toEqual({ state: 'anonymous' })
    await expect(client.login('raw-id-token')).resolves.toMatchObject({ state: 'authenticated' })
    await expect(client.logout()).resolves.toEqual({ state: 'anonymous' })
    expect(request.mock.calls).toEqual([
      [{ path: '/api/account/session/', method: 'GET' }],
      [{ path: '/api/account/session/line/', method: 'POST', body: { idToken: 'raw-id-token' } }],
      [{ path: '/api/account/session/', method: 'DELETE' }],
    ])
  })

  // テストケース: 成功statusに未知fieldを含む応答とsafe error応答を受ける。
  // 期待値: 前者をprotocol error、後者を安全なAPI errorとして拒否する。
  test('fails closed on ambiguous success and maps safe API errors', async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ state: 'authenticated', profile: { displayName: 'Owner', linked: true, sub: 'secret' } }))
      .mockResolvedValueOnce(jsonResponse({ error: { code: 'invalid_identity', summary: '本人確認に失敗しました。' } }, 401))
    const client = createAuthApiClient({ request } as ProtectedHttpClient)

    await expect(client.bootstrap()).rejects.toMatchObject({ error: { code: 'protocol_error' } })
    await expect(client.login('raw-id-token')).rejects.toEqual(new AuthApiError({ code: 'invalid_identity', summary: '本人確認に失敗しました。' }, 401))
  })
})
