import { afterEach, describe, expect, test, vi } from 'vitest'

import { createProtectedHttpClient, ProtectedHttpClientError } from '../src/httpApi'

describe('ProtectedHttpClient', () => {
  afterEach(() => {
    document.cookie = 'csrftoken=; Max-Age=0; path=/'
    vi.restoreAllMocks()
  })

  // テストケース: CSRF cookieがある状態でunsafe JSON要求を送る。
  // 期待値: same-origin credentialとCSRF headerを全unsafe要求へ付与する。
  test('sends unsafe requests with same-origin credentials and CSRF header', async () => {
    document.cookie = 'csrftoken=csrf-value; path=/'
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'))
    const client = createProtectedHttpClient()

    await client.request({ path: '/api/account/session/line/', method: 'POST', body: { idToken: 'write-only' } })

    expect(fetchMock).toHaveBeenCalledWith('/api/account/session/line/', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': 'csrf-value' },
      body: JSON.stringify({ idToken: 'write-only' }),
    })
  })

  // テストケース: CSRF cookieなしでunsafe要求を開始する。
  // 期待値: networkへ送信せず、入力値を含まない安全なclient errorで拒否する。
  test('rejects unsafe requests before fetch when CSRF is unavailable', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    const client = createProtectedHttpClient()

    await expect(client.request({ path: '/api/account/session/', method: 'DELETE' }))
      .rejects.toEqual(new ProtectedHttpClientError('csrf_missing'))
    expect(fetchMock).not.toHaveBeenCalled()
  })

  // テストケース: 保護要求が401を返す。
  // 期待値: tokenを再送せず、session失効をcontrollerへ1回通知する。
  test('notifies session invalidation without retrying a 401 response', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 401 }))
    const onSessionInvalid = vi.fn()
    const client = createProtectedHttpClient({ onSessionInvalid })

    const response = await client.request({ path: '/api/account/session/', method: 'GET' })

    expect(response.status).toBe(401)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(onSessionInvalid).toHaveBeenCalledTimes(1)
  })
})
