import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import { AppRouter } from '../src/App'
import PageFrame from '../src/PageFrame'
import type { AuthApiClient } from '../src/authApi'
import type { LinePlatformLiffAdapter } from '../src/liffClient'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

const authApi: AuthApiClient = {
  bootstrap: vi
    .fn()
    .mockResolvedValue({ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }),
  login: vi.fn(),
  logout: vi.fn().mockResolvedValue({ state: 'anonymous' }),
}

const liffAdapter: LinePlatformLiffAdapter = {
  initialize: vi.fn().mockResolvedValue('external_browser'),
  ensureProfilePermission: vi.fn().mockResolvedValue(true),
  isLoggedIn: vi.fn().mockReturnValue(false),
  login: vi.fn(),
  reauthenticate: vi.fn(),
  logout: vi.fn(),
  getIdToken: vi.fn().mockReturnValue(null),
  getAccessToken: vi.fn().mockReturnValue(null),
}

const authGateProps = {
  config: {
    liffId: '123-a',
    liffUrl: 'https://liff.line.me/123-a',
    endpointUrl: 'https://example.com/liff',
    redirectUri: 'https://example.com/liff',
  } as const,
  authApi,
  liffAdapter,
}

describe('共通application UI', () => {
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

  // テストケース: 同一routeのPageFrameで読込み状態から失敗状態へ更新する。
  // 期待値: titleと単一h1を維持し、状態更新では入力focusを奪わずroleだけを切り替える。
  test('keeps title, heading, and focus stable across page status updates', async () => {
    await act(async () =>
      root.render(
        <PageFrame
          title="チャネル管理 | LINE Message Playground"
          heading="チャネル管理"
          routeFocusKey="/liff/channels"
          status={{ kind: 'loading', message: '読み込み中です' }}
        >
          <input aria-label="編集中" />
        </PageFrame>,
      ),
    )

    expect(document.title).toBe('チャネル管理 | LINE Message Playground')
    expect(container.querySelectorAll('h1')).toHaveLength(1)
    expect(document.activeElement).toBe(container.querySelector('main'))
    expect(container.querySelector('[role="status"]')?.textContent).toBe('読み込み中です')

    const input = container.querySelector('input') as HTMLInputElement
    input.focus()
    await act(async () =>
      root.render(
        <PageFrame
          title="チャネル管理 | LINE Message Playground"
          heading="チャネル管理"
          routeFocusKey="/liff/channels"
          status={{ kind: 'error', message: '取得できませんでした' }}
        >
          <input aria-label="編集中" />
        </PageFrame>,
      ),
    )

    expect(document.activeElement).toBe(input)
    expect(container.querySelector('[role="alert"]')?.textContent).toBe('取得できませんでした')
  })

  // テストケース: 認証済みownerが認証入口を表示する。
  // 期待値: 中間トップを挟まず、共通header付きのチャネル管理へ直行する。
  test('renders authenticated navigation and redirects to channels', async () => {
    await act(async () =>
      root.render(
        <MemoryRouter initialEntries={['/liff']}>
          <AppRouter authGateProps={authGateProps} />
        </MemoryRouter>,
      ),
    )

    const navLabels = [...container.querySelectorAll('nav a')].map((link) => link.textContent)
    expect(navLabels).toEqual([
      'チャネル管理',
      'アカウント管理',
      'リッチメニュー管理',
      'メッセージ配信',
    ])
    expect(
      container.querySelector('a.application-brand[href="/liff/channels"]')?.textContent,
    ).toContain('LINE Message Playground')
    expect(container.textContent).toContain('Owner')
    expect(container.textContent).toContain('ログアウト')
    expect(container.querySelector('nav [aria-current="page"]')?.textContent).toBe('チャネル管理')

    expect(container.querySelectorAll('[data-home-card]')).toHaveLength(0)
    expect(container.querySelector('h1')?.textContent).toBe('チャネル管理')
    expect(authApi.bootstrap).toHaveBeenCalledTimes(1)
  })

  // テストケース: 共通navigationから機能画面へ移動し、narrow menuでEscapeを押す。
  // 期待値: topを経由せず遷移し、現在地をaria-currentで示し、route変更とEscapeでmenuを閉じる。
  test('navigates directly, marks the current feature, and closes the disclosure safely', async () => {
    await act(async () =>
      root.render(
        <MemoryRouter initialEntries={['/liff']}>
          <AppRouter authGateProps={authGateProps} />
        </MemoryRouter>,
      ),
    )

    const menuButton = container.querySelector(
      'button[aria-controls="application-navigation"]',
    ) as HTMLButtonElement
    await act(async () => menuButton.click())
    expect(menuButton.getAttribute('aria-expanded')).toBe('true')

    const accountLink = container.querySelector('nav a[href="/liff/account"]') as HTMLAnchorElement
    await act(async () => accountLink.click())
    expect(accountLink.getAttribute('aria-current')).toBe('page')
    expect(menuButton.getAttribute('aria-expanded')).toBe('false')
    expect(document.title).toBe('アカウント管理 | LINE Message Playground')
    expect(document.activeElement).toBe(container.querySelector('main'))

    await act(async () => menuButton.click())
    await act(async () =>
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })),
    )
    expect(menuButton.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(menuButton)
  })

  // テストケース: 未定義URLを表示する。
  // 期待値: 認証・共通application headerなしで専用title、単一h1、チャネル管理への明示Linkを表示する。
  test('renders a standalone protected-data-free 404 page', async () => {
    await act(async () =>
      root.render(
        <MemoryRouter initialEntries={['/unknown']}>
          <AppRouter authGateProps={authGateProps} />
        </MemoryRouter>,
      ),
    )

    expect(document.title).toBe('ページが見つかりません | LINE Message Playground')
    expect(container.querySelectorAll('h1')).toHaveLength(1)
    expect(container.querySelector('h1')?.textContent).toBe('ページが見つかりません')
    expect(container.querySelector('a')?.getAttribute('href')).toBe('/liff/channels')
    expect(container.querySelector('header.application-header')).toBeNull()
    expect(authApi.bootstrap).not.toHaveBeenCalled()
  })
})
