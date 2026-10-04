import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, useLocation, useNavigate } from 'react-router'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import { AppRouter } from '../src/App'
import type { AuthApiClient } from '../src/authApi'
import type { LinePlatformLiffAdapter } from '../src/liffClient'
import { LabHttpError, type LabHttpClient } from '../src/textJudgmentLabApi'

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
const authProps = {
  config: {
    liffId: '123-a',
    liffUrl: 'https://liff.line.me/123-a',
    endpointUrl: 'https://example.com/liff',
    redirectUri: 'https://example.com/liff',
  } as const,
  authApi,
  liffAdapter,
}

function LocationProbe() {
  return <span data-location>{useLocation().pathname}</span>
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
    await act(async () =>
      root.render(
        <MemoryRouter initialEntries={[path]}>
          <AppRouter authGateProps={authProps} />
        </MemoryRouter>,
      ),
    )
  }

  // テストケース: `/`へ直接アクセスする。
  // 期待値: 認証後は中間トップを表示せずチャネル管理を表示する。
  test('replaces root with the channel administration route', async () => {
    await renderAt('/')
    expect(container.querySelector('h1')?.textContent).toBe('チャネル管理')
    expect(container.querySelector('nav [aria-current="page"]')?.textContent).toBe('チャネル管理')
  })

  // テストケース: 各定義済み保護URLへ直接アクセスする。
  // 期待値: URLに対応するroute elementだけをmountする。
  test.each([
    ['/liff/channels', 'チャネル管理'],
    ['/liff/account', 'アカウント管理'],
    ['/liff/rich-menus', 'リッチメニュー管理'],
    ['/liff/rich-menus/123e4567-e89b-42d3-a456-426614174000', 'リッチメニュー管理'],
    ['/liff/deliveries', 'LINEテスト配信'],
  ])('mounts only the route element for %s', async (path, expected) => {
    await renderAt(path)
    expect(container.textContent).toContain(expected)
  })

  // テストケース: 未定義URLへ直接アクセスする。
  // 期待値: 認証を開始せず404とチャネル管理への導線を表示する。
  test('shows 404 without redirecting an unknown path', async () => {
    await renderAt('/unknown')
    expect(container.textContent).toContain('ページが見つかりません')
    expect(container.querySelector('a')?.getAttribute('href')).toBe('/liff/channels')
    expect(authApi.bootstrap).not.toHaveBeenCalled()
  })

  // テストケース: 専用ラボrouteへ直接アクセスする
  // 期待値: owner認証・shellをmountせず、専用LIFFと専用APIだけを利用する
  test('mounts text judgment lab route outside owner authentication and shell', async () => {
    const labLiff: LinePlatformLiffAdapter = {
      ...liffAdapter,
      initialize: vi.fn().mockResolvedValue('liff_browser'),
      getIdToken: vi.fn().mockReturnValue('lab-token'),
    }
    const labApi: LabHttpClient = {
      checkAccess: vi.fn().mockRejectedValue(new LabHttpError('not_allowed')),
      judge: vi.fn(),
    }
    await act(async () =>
      root.render(
        <MemoryRouter initialEntries={['/liff/labs/text-judgment']}>
          <AppRouter
            authGateProps={authProps}
            featureClients={{ textJudgmentLabApi: labApi }}
            textJudgmentLabAuthGateProps={{
              liffAdapter: labLiff,
              config: {
                liffId: '123-lab',
                liffUrl: 'https://liff.line.me/123-lab/labs/text-judgment',
                entryUrl: 'https://lab.example.test/liff/labs/text-judgment',
              },
            }}
          />
        </MemoryRouter>,
      ),
    )
    expect(labLiff.initialize).toHaveBeenCalledWith('123-lab')
    expect(labApi.checkAccess).toHaveBeenCalledWith('lab-token')
    expect(authApi.bootstrap).not.toHaveBeenCalled()
    expect(container.querySelector('.application-shell')).toBeNull()
    expect(container.textContent).toContain('このラボは利用できません')
  })

  // テストケース: 旧ラボURLへ直接アクセスする。
  // 期待値: LIFF Endpoint URL配下のcanonical入口へ置換し、owner認証を開始しない。
  test('replaces the legacy lab route with the LIFF-compatible route', async () => {
    const labLiff: LinePlatformLiffAdapter = {
      ...liffAdapter,
      initialize: vi.fn().mockResolvedValue('external_browser'),
    }
    await act(async () =>
      root.render(
        <MemoryRouter initialEntries={['/labs/text-judgment']}>
          <AppRouter
            textJudgmentLabAuthGateProps={{
              liffAdapter: labLiff,
              config: {
                liffId: '123-lab',
                liffUrl: 'https://liff.line.me/123-lab/labs/text-judgment',
                entryUrl: 'https://lab.example.test/liff/labs/text-judgment',
              },
            }}
          />
          <LocationProbe />
        </MemoryRouter>,
      ),
    )
    expect(container.querySelector('[data-location]')?.textContent).toBe('/liff/labs/text-judgment')
    expect(labLiff.initialize).toHaveBeenCalledWith('123-lab')
    expect(authApi.bootstrap).not.toHaveBeenCalled()
  })

  // テストケース: ラボから404へ移動して戻り、さらに同URLを再読込相当に再mountする。
  // 期待値: 404ではどの認証も開始せず、復帰・再読込では新しいラボpage寿命として専用LIFFだけを再初期化する。
  test('recreates only the lab lifetime after 404 return and reload', async () => {
    const labLiff: LinePlatformLiffAdapter = {
      ...liffAdapter,
      initialize: vi.fn().mockResolvedValue('liff_browser'),
      getIdToken: vi.fn().mockReturnValue('lab-token'),
    }
    const labApi: LabHttpClient = {
      checkAccess: vi.fn().mockResolvedValue({
        status: 'authorized',
        expiresAt: '2026-10-01T00:00:00Z',
        serverTime: '2026-09-21T00:00:00Z',
      }),
      judge: vi.fn(),
    }
    const Navigation = () => {
      const navigate = useNavigate()
      return (
        <>
          <button
            type="button"
            data-testid="unknown"
            onClick={() => {
              void navigate('/unknown')
            }}
          >
            404へ
          </button>
          <button
            type="button"
            data-testid="owner"
            onClick={() => {
              void navigate('/liff/channels')
            }}
          >
            管理へ
          </button>
          <button
            type="button"
            data-testid="lab"
            onClick={() => {
              void navigate('/liff/labs/text-judgment')
            }}
          >
            ラボへ
          </button>
        </>
      )
    }
    const labProps = {
      authGateProps: authProps,
      featureClients: { textJudgmentLabApi: labApi },
      textJudgmentLabAuthGateProps: {
        liffAdapter: labLiff,
        config: {
          liffId: '123-lab',
          liffUrl: 'https://liff.line.me/123-lab/labs/text-judgment',
          entryUrl: 'https://lab.example.test/liff/labs/text-judgment',
        } as const,
      },
    }
    await act(async () =>
      root.render(
        <MemoryRouter initialEntries={['/liff/labs/text-judgment']}>
          <AppRouter {...labProps} />
          <Navigation />
        </MemoryRouter>,
      ),
    )
    expect(labLiff.initialize).toHaveBeenCalledTimes(1)
    expect(labLiff.initialize).toHaveBeenLastCalledWith('123-lab')
    expect(liffAdapter.initialize).not.toHaveBeenCalled()

    await act(async () =>
      (container.querySelector('[data-testid="owner"]') as HTMLButtonElement).click(),
    )
    await act(async () => {
      await Promise.resolve()
    })
    expect(liffAdapter.initialize).toHaveBeenCalledTimes(1)
    expect(liffAdapter.initialize).toHaveBeenLastCalledWith('123-a')
    expect(labLiff.initialize).toHaveBeenCalledTimes(1)
    expect(container.querySelector('.application-shell')).not.toBeNull()

    await act(async () =>
      (container.querySelector('[data-testid="lab"]') as HTMLButtonElement).click(),
    )
    expect(labLiff.initialize).toHaveBeenCalledTimes(2)
    expect(liffAdapter.initialize).toHaveBeenCalledTimes(1)
    expect(container.querySelector('.application-shell')).toBeNull()

    await act(async () =>
      (container.querySelector('[data-testid="unknown"]') as HTMLButtonElement).click(),
    )
    expect(container.textContent).toContain('ページが見つかりません')
    expect(authApi.bootstrap).toHaveBeenCalledTimes(1)
    expect(labLiff.initialize).toHaveBeenCalledTimes(2)

    await act(async () =>
      (container.querySelector('[data-testid="lab"]') as HTMLButtonElement).click(),
    )
    expect(labLiff.initialize).toHaveBeenCalledTimes(3)
    expect(authApi.bootstrap).toHaveBeenCalledTimes(1)

    await act(async () => root.unmount())
    root = createRoot(container)
    await act(async () =>
      root.render(
        <MemoryRouter initialEntries={['/liff/labs/text-judgment']}>
          <AppRouter {...labProps} />
        </MemoryRouter>,
      ),
    )
    expect(labLiff.initialize).toHaveBeenCalledTimes(4)
    expect(authApi.bootstrap).toHaveBeenCalledTimes(1)
  })

  // テストケース: canonical UUIDでないリッチメニューdetail URLへアクセスする。
  // 期待値: 存在や権限を開示しない対象not-foundとselector導線へ縮約する。
  test('collapses a non-canonical rich-menu target into the safe detail not-found state', async () => {
    await renderAt('/liff/rich-menus/not-a-uuid')
    expect(container.textContent).toContain('対象が見つかりません')
    expect(container.querySelector('a[href="/liff/rich-menus"]')).not.toBeNull()
    expect(authApi.bootstrap).toHaveBeenCalledTimes(1)
  })

  // テストケース: 全連携解除中に配信URLへアクセスする。
  // 期待値: 通常画面を除外し、アカウント回復contentだけを表示する。
  test('mounts only account recovery content while unlinking', async () => {
    const unlinkingApi: AuthApiClient = {
      ...authApi,
      bootstrap: vi.fn().mockResolvedValue({
        state: 'unlinking',
        stage: 'local_deletion_pending',
        retryAction: 'retry_local_delete',
      }),
    }
    await act(async () =>
      root.render(
        <MemoryRouter initialEntries={['/liff/deliveries']}>
          <AppRouter authGateProps={{ ...authProps, authApi: unlinkingApi }} />
        </MemoryRouter>,
      ),
    )
    expect(container.textContent).toContain('全連携解除を処理中です')
    expect(container.textContent).not.toContain('LINEテスト配信')
    expect(container.textContent).not.toContain('認証済みowner')
  })
})
