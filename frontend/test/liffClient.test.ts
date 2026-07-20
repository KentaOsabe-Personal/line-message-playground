import { describe, expect, test, vi } from 'vitest'

import { createLinePlatformLiffAdapter } from '../src/liffClient'

describe('LinePlatformLiffAdapter', () => {
  // テストケース: LIFF browserでSDKを初期化し、raw tokenだけを取得する。
  // 期待値: browser種別とraw tokenを返し、decoded tokenやprofile APIを公開しない。
  test('isolates initialization, context and raw tokens', async () => {
    const sdk = {
      init: vi.fn().mockResolvedValue(undefined),
      isInClient: vi.fn().mockReturnValue(true),
      isLoggedIn: vi.fn().mockReturnValue(true),
      login: vi.fn(),
      getIDToken: vi.fn().mockReturnValue('raw-id-token'),
      getAccessToken: vi.fn().mockReturnValue('raw-access-token'),
    }
    const adapter = createLinePlatformLiffAdapter(sdk)

    await expect(adapter.initialize('123-a')).resolves.toBe('liff_browser')
    expect(adapter.isLoggedIn()).toBe(true)
    expect(adapter.getIdToken()).toBe('raw-id-token')
    expect(adapter.getAccessToken()).toBe('raw-access-token')
    expect(adapter).not.toHaveProperty('getDecodedIDToken')
    expect(adapter).not.toHaveProperty('getProfile')
  })

  // テストケース: 外部browserで明示loginを開始する。
  // 期待値: 自動login設定を使わず、検証済みredirect URIだけをSDKへ渡す。
  test('starts explicit external-browser login with the redirect URI', async () => {
    const sdk = {
      init: vi.fn().mockResolvedValue(undefined),
      isInClient: vi.fn().mockReturnValue(false),
      isLoggedIn: vi.fn().mockReturnValue(false),
      login: vi.fn(),
      getIDToken: vi.fn().mockReturnValue(null),
      getAccessToken: vi.fn().mockReturnValue(null),
    }
    const adapter = createLinePlatformLiffAdapter(sdk)

    await expect(adapter.initialize('123-a')).resolves.toBe('external_browser')
    adapter.login('https://example.com/liff')

    expect(sdk.init).toHaveBeenCalledWith({ liffId: '123-a' })
    expect(sdk.login).toHaveBeenCalledWith({ redirectUri: 'https://example.com/liff' })
  })
})
