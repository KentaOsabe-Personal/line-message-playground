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
      logout: vi.fn(),
      getIDToken: vi.fn().mockReturnValue('raw-id-token'),
      getAccessToken: vi.fn().mockReturnValue('raw-access-token'),
      permission: {
        query: vi.fn().mockResolvedValue({ state: 'granted' }),
        requestAll: vi.fn(),
      },
    }
    const adapter = createLinePlatformLiffAdapter(sdk)

    await expect(adapter.initialize('123-a')).resolves.toBe('liff_browser')
    expect(adapter.isLoggedIn()).toBe(true)
    expect(adapter.getIdToken()).toBe('raw-id-token')
    expect(adapter.getAccessToken()).toBe('raw-access-token')
    adapter.logout()
    expect(sdk.logout).toHaveBeenCalledTimes(1)
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
      logout: vi.fn(),
      getIDToken: vi.fn().mockReturnValue(null),
      getAccessToken: vi.fn().mockReturnValue(null),
      permission: {
        query: vi.fn().mockResolvedValue({ state: 'granted' }),
        requestAll: vi.fn(),
      },
    }
    const adapter = createLinePlatformLiffAdapter(sdk)

    await expect(adapter.initialize('123-a')).resolves.toBe('external_browser')
    adapter.login('https://example.com/liff')

    expect(sdk.init).toHaveBeenCalledWith({ liffId: '123-a' })
    expect(sdk.login).toHaveBeenCalledWith({ redirectUri: 'https://example.com/liff' })
  })

  // テストケース: access token失効後に外部browserとLIFF browserで再認証する。
  // 期待値: 外部browserはlogout後にloginし、LIFF browserは再初期化のためreloadする。
  test('restarts authentication using the supported flow for each browser context', () => {
    const externalSdk = {
      init: vi.fn(), isInClient: vi.fn().mockReturnValue(false), isLoggedIn: vi.fn().mockReturnValue(true),
      login: vi.fn(), logout: vi.fn(), getIDToken: vi.fn(), getAccessToken: vi.fn(),
      permission: { query: vi.fn().mockResolvedValue({ state: 'granted' as const }), requestAll: vi.fn() },
    }
    const external = createLinePlatformLiffAdapter(externalSdk, vi.fn())
    external.reauthenticate('https://example.com/liff')
    expect(externalSdk.logout).toHaveBeenCalledTimes(1)
    expect(externalSdk.login).toHaveBeenCalledWith({ redirectUri: 'https://example.com/liff' })

    const reload = vi.fn()
    const liffSdk = { ...externalSdk, isInClient: vi.fn().mockReturnValue(true), login: vi.fn(), logout: vi.fn() }
    const inClient = createLinePlatformLiffAdapter(liffSdk, reload)
    inClient.reauthenticate('https://example.com/liff')
    expect(reload).toHaveBeenCalledTimes(1)
    expect(liffSdk.login).not.toHaveBeenCalled()
  })

  // テストケース: LIFF SDK初期化が失敗し、ID/access tokenが欠落する。
  // 期待値: 初期化失敗を成功扱いせず、欠落tokenはnullのまま返してprofile等へfallbackしない。
  test('propagates initialization failure and keeps missing raw tokens anonymous', async () => {
    const failure = new Error('sdk-init-failed')
    const sdk = {
      init: vi.fn().mockRejectedValue(failure),
      isInClient: vi.fn().mockReturnValue(false),
      isLoggedIn: vi.fn().mockReturnValue(false),
      login: vi.fn(),
      logout: vi.fn(),
      getIDToken: vi.fn().mockReturnValue(null),
      getAccessToken: vi.fn().mockReturnValue(null),
      permission: {
        query: vi.fn().mockResolvedValue({ state: 'granted' }),
        requestAll: vi.fn(),
      },
    }
    const adapter = createLinePlatformLiffAdapter(sdk)

    await expect(adapter.initialize('123-a')).rejects.toBe(failure)
    expect(adapter.getIdToken()).toBeNull()
    expect(adapter.getAccessToken()).toBeNull()
    expect(adapter).not.toHaveProperty('getProfile')
  })

  // テストケース: Mini Appで未同意のprofile権限を要求する。
  // 期待値: prompt時だけ検証画面を開き、再確認でgrantedになった場合だけ成功する。
  test('requests and confirms Mini App profile permission', async () => {
    const query = vi.fn()
      .mockResolvedValueOnce({ state: 'prompt' })
      .mockResolvedValueOnce({ state: 'granted' })
    const requestAll = vi.fn().mockResolvedValue(undefined)
    const sdk = {
      init: vi.fn(), isInClient: vi.fn(), isLoggedIn: vi.fn(), login: vi.fn(), logout: vi.fn(),
      getIDToken: vi.fn(), getAccessToken: vi.fn(), permission: { query, requestAll },
    }

    const adapter = createLinePlatformLiffAdapter(sdk)

    await expect(adapter.ensureProfilePermission()).resolves.toBe(true)
    expect(query).toHaveBeenNthCalledWith(1, 'profile')
    expect(requestAll).toHaveBeenCalledTimes(1)
    expect(query).toHaveBeenNthCalledWith(2, 'profile')
  })

  // テストケース: profile scopeがMini App channelで利用できない。
  // 期待値: 検証画面を開かず権限不足を返す。
  test('rejects unavailable Mini App profile permission', async () => {
    const requestAll = vi.fn()
    const sdk = {
      init: vi.fn(), isInClient: vi.fn(), isLoggedIn: vi.fn(), login: vi.fn(), logout: vi.fn(),
      getIDToken: vi.fn(), getAccessToken: vi.fn(),
      permission: { query: vi.fn().mockResolvedValue({ state: 'unavailable' }), requestAll },
    }

    await expect(createLinePlatformLiffAdapter(sdk).ensureProfilePermission()).resolves.toBe(false)
    expect(requestAll).not.toHaveBeenCalled()
  })
})
