import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import { AppRouter } from '../src/App'
import type { AuthApiClient } from '../src/authApi'
import type { LinePlatformLiffAdapter } from '../src/liffClient'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const authApi: AuthApiClient = {
  bootstrap: vi.fn().mockResolvedValue({ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }),
  login: vi.fn(), logout: vi.fn().mockResolvedValue({ state: 'anonymous' }),
}
const liffAdapter: LinePlatformLiffAdapter = {
  initialize: vi.fn().mockResolvedValue('external_browser'),
  isLoggedIn: vi.fn().mockReturnValue(false),
  login: vi.fn(), reauthenticate: vi.fn(), logout: vi.fn(),
  getIdToken: vi.fn().mockReturnValue(null), getAccessToken: vi.fn().mockReturnValue(null),
}
const authProps = {
  config: { liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' } as const,
  authApi, liffAdapter,
}

describe('AppRouter', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.clearAllMocks()
  })

  async function renderAt(path: string) {
    await act(async () => root.render(
      <MemoryRouter initialEntries={[path]}><AppRouter authGateProps={authProps} /></MemoryRouter>,
    ))
  }

  // テストケース: `/`へ直接アクセスする。
  // 期待値: 履歴置換でdata-freeな`/liff`トップだけを表示する。
  test('replaces root with the data-free top route', async () => {
    await renderAt('/')
    expect(container.textContent).toContain('トップ')
    expect(container.textContent).not.toContain('チャネル管理画面')
    expect(container.textContent).not.toContain('LINEテスト配信画面')
  })

  // テストケース: 各定義済み保護URLへ直接アクセスする。
  // 期待値: URLに対応するroute elementだけをmountする。
  test.each([
    ['/liff/channels', 'チャネル管理画面'],
    ['/liff/account', 'アカウント管理画面'],
    ['/liff/rich-menus', 'リッチメニュー選択画面'],
    ['/liff/rich-menus/123e4567-e89b-42d3-a456-426614174000', 'リッチメニュー管理画面'],
    ['/liff/deliveries', 'LINEテスト配信画面'],
  ])('mounts only the route element for %s', async (path, expected) => {
    await renderAt(path)
    expect(container.textContent).toContain(expected)
  })

  // テストケース: 未定義URLへ直接アクセスする。
  // 期待値: 認証を開始せず404とトップへの導線を表示する。
  test('shows 404 without redirecting an unknown path', async () => {
    await renderAt('/unknown')
    expect(container.textContent).toContain('ページが見つかりません')
    expect(container.querySelector('a')?.getAttribute('href')).toBe('/liff')
    expect(authApi.bootstrap).not.toHaveBeenCalled()
  })

  // テストケース: canonical UUIDでないリッチメニューURLへアクセスする。
  // 期待値: AuthGateをmountせず404へ分類する。
  test('rejects a non-canonical rich-menu path before authentication', async () => {
    await renderAt('/liff/rich-menus/not-a-uuid')
    expect(container.textContent).toContain('ページが見つかりません')
    expect(container.textContent).not.toContain('LINEでログイン')
    expect(authApi.bootstrap).not.toHaveBeenCalled()
  })

  // テストケース: 全連携解除中に配信URLへアクセスする。
  // 期待値: 通常画面を除外し、アカウント回復contentだけを表示する。
  test('mounts only account recovery content while unlinking', async () => {
    const unlinkingApi: AuthApiClient = {
      ...authApi,
      bootstrap: vi.fn().mockResolvedValue({
        state: 'unlinking', stage: 'local_deletion_pending', retryAction: 'retry_local_delete',
      }),
    }
    await act(async () => root.render(
      <MemoryRouter initialEntries={['/liff/deliveries']}>
        <AppRouter authGateProps={{ ...authProps, authApi: unlinkingApi }} />
      </MemoryRouter>,
    ))
    expect(container.textContent).toContain('全連携解除を処理中です')
    expect(container.textContent).not.toContain('LINEテスト配信画面')
    expect(container.textContent).not.toContain('認証済みowner')
  })
})
