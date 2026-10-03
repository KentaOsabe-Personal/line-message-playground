import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { createMemoryRouter, MemoryRouter, RouterProvider, useLocation } from 'react-router'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import { AppRouter } from '../src/App'
import type { AccountApiClient } from '../src/accountApi'
import type { AuthApiClient } from '../src/authApi'
import type { ChannelAdminApiClient } from '../src/channelAdminApi'
import type { ChannelAdminItem } from '../src/channelAdminDto'
import type { LinkedDeliveryApiClient } from '../src/deliveryApi'
import type { LinkedDeliveryStatus } from '../src/deliveryDto'
import type { LinePlatformLiffAdapter } from '../src/liffClient'
import type { RichMenuAdminApiClient } from '../src/richMenuAdminApi'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

const authenticatedSession = {
  state: 'authenticated' as const,
  profile: { displayName: 'Owner', linked: true },
}

const authApi = (overrides: Partial<AuthApiClient> = {}): AuthApiClient => ({
  bootstrap: vi.fn().mockResolvedValue(authenticatedSession),
  login: vi.fn(),
  logout: vi.fn().mockResolvedValue({ state: 'anonymous' }),
  ...overrides,
})

const liffAdapter = (): LinePlatformLiffAdapter => ({
  initialize: vi.fn().mockResolvedValue('external_browser'),
  ensureProfilePermission: vi.fn().mockResolvedValue(true),
  isLoggedIn: vi.fn().mockReturnValue(false),
  login: vi.fn(),
  reauthenticate: vi.fn(),
  logout: vi.fn(),
  getIdToken: vi.fn().mockReturnValue(null),
  getAccessToken: vi.fn().mockReturnValue(null),
})

const authGateProps = (api: AuthApiClient) => ({
  config: {
    liffId: '123-a',
    liffUrl: 'https://liff.line.me/123-a',
    endpointUrl: 'https://example.com/liff',
    redirectUri: 'https://example.com/liff',
  } as const,
  authApi: api,
  liffAdapter: liffAdapter(),
})

function LocationProbe() {
  const location = useLocation()
  return <output data-location>{location.pathname}</output>
}

const enterInput = async (input: HTMLInputElement, value: string) => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, value)
  await act(async () => input.dispatchEvent(new Event('input', { bubbles: true })))
}

const featureClients = () => {
  const channelApi: ChannelAdminApiClient = {
    listChannels: vi.fn().mockResolvedValue([]),
    getChannel: vi.fn(),
    register: vi.fn(),
    update: vi.fn(),
    setState: vi.fn(),
    delete: vi.fn(),
    checkConnection: vi.fn(),
  }
  const accountApi: AccountApiClient = {
    listChannels: vi.fn().mockResolvedValue([]),
    registerRecipient: vi.fn(),
    setRecipientEnabled: vi.fn(),
    unlinkRecipient: vi.fn(),
    previewUnlink: vi.fn(),
    executeUnlink: vi.fn(),
  }
  const deliveryApi: LinkedDeliveryApiClient = {
    listChannels: vi.fn().mockResolvedValue([]),
    listRecipients: vi.fn(),
    preview: vi.fn(),
    send: vi.fn(),
    checkStatus: vi.fn(),
  }
  const richMenuApi = {} as RichMenuAdminApiClient
  return { channelApi, accountApi, deliveryApi, richMenuApi }
}

const channel = (overrides: Partial<ChannelAdminItem> = {}): ChannelAdminItem => ({
  channelId: '123e4567-e89b-42d3-a456-426614174000',
  label: '通知bot',
  messagingApiChannelId: '1234567890',
  botUserId: `U${'a'.repeat(32)}`,
  providerId: '456',
  active: true,
  credentialsState: 'configured',
  credentialsUpdatedAt: '2026-08-29T10:00:00+09:00',
  createdAt: '2026-08-29T09:00:00+09:00',
  updatedAt: '2026-08-29T10:00:00+09:00',
  webhookUrl: 'https://example.com/webhook',
  deactivationSummary: null,
  richMenuRefreshRequired: false,
  ...overrides,
})

const deliveryOperationId = '33333333-3333-4333-8333-333333333333'
const processingDelivery: LinkedDeliveryStatus = {
  operationId: deliveryOperationId,
  snapshot: {
    channelId: '11111111-1111-4111-8111-111111111111',
    channelLabel: '配信bot',
    recipientId: '22222222-2222-4222-8222-222222222222',
    channelActive: true,
    recipientEnabled: true,
    friendshipState: 'friend',
  },
  status: 'processing',
  acceptedAt: '2026-08-29T10:00:00+09:00',
  completedAt: null,
  lineRequestId: null,
  receipt: {
    requested: false,
    status: 'not_requested',
    expiresAt: null,
    confirmedAt: null,
  },
}

describe('app-screen-separation task 8 integration contracts', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    window.sessionStorage.clear()
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  })

  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.restoreAllMocks()
  })

  describe('8.1 route、認証、共通shell', () => {
    // テストケース: `/`をBrowser履歴相当のmemory routerで開く。
    // 期待値: 中間トップを挟まず`/liff/channels`へREPLACEし、共通shellを表示する。
    test('replaces root history and renders the authenticated channel shell', async () => {
      const api = authApi()
      const router = createMemoryRouter(
        [
          {
            path: '*',
            element: (
              <>
                <AppRouter authGateProps={authGateProps(api)} featureClients={featureClients()} />
                <LocationProbe />
              </>
            ),
          },
        ],
        { initialEntries: ['/'] },
      )

      await act(async () => root.render(<RouterProvider router={router} />))

      expect(router.state.location.pathname).toBe('/liff/channels')
      expect(router.state.historyAction).toBe('REPLACE')
      expect(document.title).toBe('チャネル管理 | LINE Message Playground')
      expect(container.querySelectorAll('h1')).toHaveLength(1)
      expect(container.querySelector('h1')?.textContent).toBe('チャネル管理')
      expect(container.querySelector('header')).not.toBeNull()
      expect(api.bootstrap).toHaveBeenCalledTimes(1)
    })

    // テストケース: 共通navigationからアカウント管理へpushし、戻る・進むを実行する。
    // 期待値: 各履歴entryに対応する画面、title、現在地、h1 focusへ収束する。
    test('supports link push plus browser back and forward with route focus', async () => {
      const router = createMemoryRouter(
        [
          {
            path: '*',
            element: (
              <>
                <AppRouter
                  authGateProps={authGateProps(authApi())}
                  featureClients={featureClients()}
                />
                <LocationProbe />
              </>
            ),
          },
        ],
        { initialEntries: ['/liff'] },
      )
      await act(async () => root.render(<RouterProvider router={router} />))

      await act(async () =>
        (container.querySelector('nav a[href="/liff/account"]') as HTMLAnchorElement).click(),
      )
      expect(router.state.location.pathname).toBe('/liff/account')
      expect(router.state.historyAction).toBe('PUSH')
      expect(document.title).toBe('アカウント管理 | LINE Message Playground')
      expect(container.querySelector('nav [aria-current="page"]')?.textContent).toBe(
        'アカウント管理',
      )
      expect(document.activeElement).toBe(container.querySelector('main'))

      await act(async () => {
        await router.navigate(-1)
      })
      expect(container.querySelector('h1')?.textContent).toBe('チャネル管理')
      await act(async () => {
        await router.navigate(1)
      })
      expect(container.querySelector('h1')?.textContent).toBe('アカウント管理')
    })

    // テストケース: 未認証ownerが保護URLを直接開く。
    // 期待値: URLを維持し、共通shellと機能contentをmountせずloginだけを表示する。
    test('keeps the protected URL while authentication hides all protected content', async () => {
      const api = authApi({ bootstrap: vi.fn().mockResolvedValue({ state: 'anonymous' }) })
      await act(async () =>
        root.render(
          <MemoryRouter initialEntries={['/liff/deliveries']}>
            <AppRouter authGateProps={authGateProps(api)} />
            <LocationProbe />
          </MemoryRouter>,
        ),
      )

      expect(container.querySelector('[data-location]')?.textContent).toBe('/liff/deliveries')
      expect(container.textContent).toContain('LINEでログイン')
      expect(container.textContent).not.toContain('LINEテスト配信')
      expect(container.querySelector('header')).toBeNull()
    })
  })

  describe('8.2 チャネルとアカウントのroute分離', () => {
    // テストケース: 認証入口、チャネル、アカウントの各URLを独立して表示する。
    // 期待値: 認証入口はチャネル管理へ収束し、現在routeのConsoleだけがreadを開始する。
    test.each([
      ['/liff', 1, 0],
      ['/liff/channels', 1, 0],
      ['/liff/account', 0, 1],
    ] as const)(
      'isolates channel and account reads at %s',
      async (path, channelReads, accountReads) => {
        const clients = featureClients()
        await act(async () =>
          root.render(
            <MemoryRouter initialEntries={[path]}>
              <AppRouter authGateProps={authGateProps(authApi())} featureClients={clients} />
            </MemoryRouter>,
          ),
        )

        expect(clients.channelApi.listChannels).toHaveBeenCalledTimes(channelReads)
        expect(clients.accountApi.listChannels).toHaveBeenCalledTimes(accountReads)
        expect(clients.deliveryApi.listChannels).not.toHaveBeenCalled()
        expect(clients.deliveryApi.preview).not.toHaveBeenCalled()
        expect(clients.deliveryApi.send).not.toHaveBeenCalled()
      },
    )

    // テストケース: チャネル登録formへwrite-only資格情報を入力し、navigationで離脱して再訪する。
    // 期待値: 確認を表示せずformと秘密入力を破棄し、最新チャネルreadから再開する。
    test('discards channel credentials on route leave without a discard confirmation', async () => {
      const clients = featureClients()
      const confirm = vi.spyOn(window, 'confirm')
      await act(async () =>
        root.render(
          <MemoryRouter initialEntries={['/liff/channels']}>
            <AppRouter authGateProps={authGateProps(authApi())} featureClients={clients} />
          </MemoryRouter>,
        ),
      )

      const open = [...container.querySelectorAll('button')].find(
        (button) => button.textContent === '新しいチャネルを登録',
      )
      await act(async () => open?.click())
      const secret = container.querySelector('input[name="channelSecret"]') as HTMLInputElement
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(
        secret,
        'secret-canary',
      )
      await act(async () => secret.dispatchEvent(new Event('input', { bubbles: true })))
      expect(secret.value).toBe('secret-canary')

      await act(async () =>
        (container.querySelector('nav a[href="/liff/account"]') as HTMLAnchorElement).click(),
      )
      await act(async () =>
        (container.querySelector('nav a[href="/liff/channels"]') as HTMLAnchorElement).click(),
      )

      expect(confirm).not.toHaveBeenCalled()
      expect(container.querySelector('input[name="channelSecret"]')).toBeNull()
      expect(container.textContent).not.toContain('secret-canary')
      expect(clients.channelApi.listChannels).toHaveBeenCalledTimes(2)
      expect(clients.accountApi.listChannels).toHaveBeenCalledTimes(1)
    })
  })

  describe('8.3 rich-menuと配信のroute分離', () => {
    // テストケース: 全modeのチャネルをselector routeへ表示する。
    // 期待値: provider-ready項目だけdetailへ進め、未設定項目は設定導線を示し、mutationを開始しない。
    test('renders every rich-menu selector mode without starting a mutation', async () => {
      const clients = featureClients()
      vi.mocked(clients.channelApi).listChannels.mockResolvedValue([
        channel(),
        channel({
          channelId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
          label: '停止bot',
          active: false,
        }),
        channel({
          channelId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
          label: '未設定bot',
          providerId: null,
        }),
        channel({
          channelId: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
          label: '回復bot',
          deactivationSummary: {
            operationId: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
            status: 'checking',
            reason: null,
            updatedAt: '2026-08-29T10:30:00+09:00',
          },
        }),
      ])
      await act(async () =>
        root.render(
          <MemoryRouter initialEntries={['/liff/rich-menus']}>
            <AppRouter authGateProps={authGateProps(authApi())} featureClients={clients} />
          </MemoryRouter>,
        ),
      )

      expect(container.textContent).toContain('管理可能')
      expect(container.textContent).toContain('停止中・読み取り専用')
      expect(container.textContent).toContain('設定が必要')
      expect(container.textContent).toContain('回復操作のみ')
      expect(container.querySelectorAll('a[href^="/liff/rich-menus/"]')).toHaveLength(3)
      expect(clients.channelApi.register).not.toHaveBeenCalled()
      expect(clients.channelApi.update).not.toHaveBeenCalled()
      expect(clients.deliveryApi.send).not.toHaveBeenCalled()
    })

    // テストケース: provider ID未設定チャネルのdetail routeを直接開く。
    // 期待値: 動的titleと固定h1を表示し、管理操作ではなくselectorとチャネル設定への導線だけを示す。
    test('keeps dynamic rich-menu metadata while blocking a provider-missing detail', async () => {
      const clients = featureClients()
      const providerMissing = channel({ label: '設定待ちbot', providerId: null })
      vi.mocked(clients.channelApi).getChannel.mockResolvedValue(providerMissing)
      await act(async () =>
        root.render(
          <MemoryRouter initialEntries={[`/liff/rich-menus/${providerMissing.channelId}`]}>
            <AppRouter authGateProps={authGateProps(authApi())} featureClients={clients} />
          </MemoryRouter>,
        ),
      )

      expect(document.title).toBe('設定待ちbot | リッチメニュー管理')
      expect(container.querySelector('h1')?.textContent).toBe('リッチメニュー管理')
      expect(container.querySelector('a[href="/liff/rich-menus"]')).not.toBeNull()
      expect(container.querySelector('a[href="/liff/channels"]')).not.toBeNull()
      expect(container.textContent).not.toContain('新しいプレビュー')
    })

    // テストケース: sessionStorageに受付済み配信operation IDを保持して配信routeを再訪する。
    // 期待値: 入力・previewを復元せず同じIDのstatusだけを再取得し、新規送信しない。
    test('resumes a delivery with status-only hydration and no automatic resend', async () => {
      const clients = featureClients()
      window.sessionStorage.setItem('line-owner:delivery-operation-id', deliveryOperationId)
      vi.mocked(clients.deliveryApi).checkStatus.mockResolvedValue(processingDelivery)
      await act(async () =>
        root.render(
          <MemoryRouter initialEntries={['/liff/deliveries']}>
            <AppRouter authGateProps={authGateProps(authApi())} featureClients={clients} />
          </MemoryRouter>,
        ),
      )

      expect(clients.deliveryApi.checkStatus).toHaveBeenCalledWith(deliveryOperationId, {
        signal: expect.any(AbortSignal) as unknown,
      })
      expect(clients.deliveryApi.listChannels).not.toHaveBeenCalled()
      expect(clients.deliveryApi.listRecipients).not.toHaveBeenCalled()
      expect(clients.deliveryApi.preview).not.toHaveBeenCalled()
      expect(clients.deliveryApi.send).not.toHaveBeenCalled()
      expect(container.textContent).toContain('配信を処理中です')
      expect(container.querySelector('input[name="subject"]')).toBeNull()
    })
  })

  describe('8.4 async lifecycleとaccessibility', () => {
    // テストケース: チャネルread中に共通navigationでアカウント画面へ移動し、旧readが後着する。
    // 期待値: 旧signalをabortし、後着結果を移動先へ表示せず、移動先readだけを有効にする。
    test('aborts the leaving route read and fences its late result', async () => {
      const clients = featureClients()
      let resolveChannels!: (items: ChannelAdminItem[]) => void
      vi.mocked(clients.channelApi).listChannels.mockReturnValue(
        new Promise((resolve) => {
          resolveChannels = resolve
        }),
      )
      await act(async () =>
        root.render(
          <MemoryRouter initialEntries={['/liff/channels']}>
            <AppRouter authGateProps={authGateProps(authApi())} featureClients={clients} />
          </MemoryRouter>,
        ),
      )
      const signal = vi.mocked(clients.channelApi).listChannels.mock.calls[0]?.[0]
        ?.signal as AbortSignal
      expect(signal.aborted).toBe(false)

      await act(async () =>
        (container.querySelector('nav a[href="/liff/account"]') as HTMLAnchorElement).click(),
      )
      expect(signal.aborted).toBe(true)
      await act(async () => resolveChannels([channel({ label: '旧画面の後着結果' })]))

      expect(container.querySelector('h1')?.textContent).toBe('アカウント管理')
      expect(container.textContent).not.toContain('旧画面の後着結果')
      expect(clients.accountApi.listChannels).toHaveBeenCalledTimes(1)
    })

    // テストケース: チャネル登録mutation受付後、完了前に別画面へ移動して元画面を再訪する。
    // 期待値: mutationをabort・自動再送せず、後着結果を旧画面へ反映せず、再訪時は最新GETを行う。
    test('lets an accepted mutation finish without replaying it after navigation', async () => {
      const clients = featureClients()
      let resolveRegister!: (item: ChannelAdminItem) => void
      vi.mocked(clients.channelApi).register.mockReturnValue(
        new Promise((resolve) => {
          resolveRegister = resolve
        }),
      )
      await act(async () =>
        root.render(
          <MemoryRouter initialEntries={['/liff/channels']}>
            <AppRouter authGateProps={authGateProps(authApi())} featureClients={clients} />
          </MemoryRouter>,
        ),
      )
      const open = [...container.querySelectorAll('button')].find(
        (button) => button.textContent === '新しいチャネルを登録',
      )
      await act(async () => open?.click())
      await enterInput(
        container.querySelector('input[name="label"]') as HTMLInputElement,
        '受付済みbot',
      )
      await enterInput(
        container.querySelector('input[name="messagingApiChannelId"]') as HTMLInputElement,
        '1234567890',
      )
      await enterInput(
        container.querySelector('input[name="botUserId"]') as HTMLInputElement,
        `U${'a'.repeat(32)}`,
      )
      await enterInput(
        container.querySelector('input[name="providerId"]') as HTMLInputElement,
        '456',
      )
      await enterInput(
        container.querySelector('input[name="accessToken"]') as HTMLInputElement,
        'access-token-canary',
      )
      await enterInput(
        container.querySelector('input[name="channelSecret"]') as HTMLInputElement,
        'channel-secret-canary',
      )

      const form = container.querySelector('form.channel-editor') as HTMLFormElement
      await act(async () =>
        form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })),
      )
      expect(clients.channelApi.register).toHaveBeenCalledTimes(1)

      await act(async () =>
        (container.querySelector('nav a[href="/liff/account"]') as HTMLAnchorElement).click(),
      )
      await act(async () => resolveRegister(channel({ label: '受付済みbot' })))
      expect(container.textContent).not.toContain('チャネルを登録しました')

      await act(async () =>
        (container.querySelector('nav a[href="/liff/channels"]') as HTMLAnchorElement).click(),
      )
      expect(clients.channelApi.register).toHaveBeenCalledTimes(1)
      expect(clients.channelApi.listChannels).toHaveBeenCalledTimes(2)
      expect(JSON.stringify(window.sessionStorage)).not.toContain('access-token-canary')
      expect(JSON.stringify(window.sessionStorage)).not.toContain('channel-secret-canary')
    })

    // テストケース: 認証入口から表示される機能画面の共通shellをkeyboard操作可能なDOMとして確認する。
    // 期待値: header、nav、main、単一h1、label付きdisclosure、24px対象classのLinkを維持する。
    test('exposes the shared landmarks and keyboard-operable navigation contract', async () => {
      await act(async () =>
        root.render(
          <MemoryRouter initialEntries={['/liff']}>
            <AppRouter authGateProps={authGateProps(authApi())} featureClients={featureClients()} />
          </MemoryRouter>,
        ),
      )

      expect(container.querySelector('header')).not.toBeNull()
      expect(container.querySelector('nav[aria-label="機能ナビゲーション"]')).not.toBeNull()
      expect(container.querySelector('main')).not.toBeNull()
      expect(container.querySelectorAll('h1')).toHaveLength(1)
      const disclosure = container.querySelector(
        'button[aria-controls="application-navigation"]',
      ) as HTMLButtonElement
      disclosure.focus()
      await act(async () =>
        disclosure.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })),
      )
      await act(async () => disclosure.click())
      expect(disclosure.getAttribute('aria-expanded')).toBe('true')
      expect(container.querySelectorAll('a[data-home-card]')).toHaveLength(0)
      expect(container.querySelector('h1')?.textContent).toBe('チャネル管理')
      expect(container.querySelector('nav [aria-current="page"]')?.textContent).toBe('チャネル管理')
    })
  })
})
