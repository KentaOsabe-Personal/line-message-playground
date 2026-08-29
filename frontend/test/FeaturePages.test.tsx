import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import AccountPage from '../src/AccountPage'
import ChannelAdminPage from '../src/ChannelAdminPage'
import DeliveryPage from '../src/DeliveryPage'
import type { AuthGateContext } from '../src/AuthGate'
import type { AccountApiClient } from '../src/accountApi'
import type { ChannelAdminApiClient } from '../src/channelAdminApi'
import type { ChannelAdminItem } from '../src/channelAdminDto'
import type { LinkedDeliveryApiClient } from '../src/deliveryApi'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const channel: ChannelAdminItem = {
  channelId: '123e4567-e89b-42d3-a456-426614174000',
  label: '管理対象bot',
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
}

const channelApi: ChannelAdminApiClient = {
  listChannels: vi.fn().mockResolvedValue([channel]),
  getChannel: vi.fn(), register: vi.fn(), update: vi.fn(), setState: vi.fn(), delete: vi.fn(), checkConnection: vi.fn(),
}

const accountApi: AccountApiClient = {
  listChannels: vi.fn().mockResolvedValue([]), registerRecipient: vi.fn(), setRecipientEnabled: vi.fn(),
  unlinkRecipient: vi.fn(), previewUnlink: vi.fn(), executeUnlink: vi.fn(),
}

const deliveryApi: LinkedDeliveryApiClient = {
  listChannels: vi.fn().mockResolvedValue([]), listRecipients: vi.fn(), preview: vi.fn(), send: vi.fn(), checkStatus: vi.fn(),
}

const authContext: AuthGateContext = {
  session: { state: 'authenticated', profile: { displayName: 'Owner', linked: true } },
  logout: vi.fn(), getAccessToken: vi.fn().mockReturnValue(null), reauthenticate: vi.fn(),
  reauthenticateForUnlink: vi.fn(), unlinkReauthenticationReady: false,
  onSessionReceived: vi.fn(), refreshSession: vi.fn().mockResolvedValue(undefined),
}

describe('機能page adapters', () => {
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

  // テストケース: チャネル管理URLのpage adapterを描画する。
  // 期待値: 単一h1とChannel Consoleだけを表示し、他機能Consoleをinline mountしない。
  test('4.1 renders only channel administration under the channel page heading', async () => {
    await act(async () => root.render(
      <MemoryRouter initialEntries={['/liff/channels']}>
        <ChannelAdminPage api={channelApi} onSessionInvalid={vi.fn()} />
      </MemoryRouter>,
    ))

    expect(container.querySelectorAll('h1')).toHaveLength(1)
    expect(container.querySelector('h1')?.textContent).toBe('チャネル管理')
    expect(container.textContent).toContain('管理対象bot')
    expect(container.querySelector('.rich-menu-admin')).toBeNull()
    expect(container.textContent).not.toContain('配信先管理')
    expect(container.textContent).not.toContain('LINEテスト配信')
  })

  // テストケース: アカウント管理URLのpage adapterを描画する。
  // 期待値: owner・配信先連携と全連携解除だけを表示し、画面固有logoutや他機能を含めない。
  test('4.2 renders only account linking responsibilities without page-local logout', async () => {
    await act(async () => root.render(
      <MemoryRouter initialEntries={['/liff/account']}>
        <AccountPage context={authContext} api={accountApi} />
      </MemoryRouter>,
    ))

    expect(container.querySelectorAll('h1')).toHaveLength(1)
    expect(container.querySelector('h1')?.textContent).toBe('アカウント管理')
    expect(container.textContent).toContain('配信先管理')
    expect(container.textContent).toContain('全連携解除')
    expect(container.textContent).not.toContain('ログアウト')
    expect(container.textContent).not.toContain('チャネルアクセストークン')
    expect(container.textContent).not.toContain('LINEテスト配信')
  })

  // テストケース: メッセージ配信URLのpage adapterを描画する。
  // 期待値: LINEテスト配信の単一対象Consoleだけを表示し、対象外の配信機能を追加しない。
  test('4.3 renders the single-target delivery console under its route heading', async () => {
    await act(async () => root.render(
      <MemoryRouter initialEntries={['/liff/deliveries']}>
        <DeliveryPage linkedClient={deliveryApi} onSessionInvalid={vi.fn()} />
      </MemoryRouter>,
    ))

    expect(container.querySelectorAll('h1')).toHaveLength(1)
    expect(container.querySelector('h1')?.textContent).toBe('LINEテスト配信')
    expect(container.textContent).toContain('配信元チャネル')
    expect(container.textContent).not.toContain('一括配信')
    expect(container.textContent).not.toContain('配信予約')
    expect(container.textContent).not.toContain('配信履歴')
  })

  // テストケース: 全連携解除mutationの受付後、完了前にアカウントpageをunmountする。
  // 期待値: mutationは一度だけ完了するが、late resultでsessionやunmount済みUIを更新しない。
  test('4.4 ignores an accepted account mutation result after route unmount', async () => {
    let resolveUnlink: ((value: { state: 'completed' }) => void) | undefined
    const executeUnlink = vi.fn().mockReturnValue(new Promise<{ state: 'completed' }>((resolve) => {
      resolveUnlink = resolve
    }))
    const pendingApi: AccountApiClient = {
      ...accountApi,
      previewUnlink: vi.fn().mockResolvedValue({
        displayName: 'Owner', recipientCount: 0, channelLabels: [], deliveryAuditRetained: true,
        confirmationToken: 'opaque-confirmation', expiresAt: '2026-08-29T23:00:00+09:00',
      }),
      executeUnlink,
    }
    const context: AuthGateContext = {
      ...authContext,
      getAccessToken: vi.fn().mockReturnValue('fresh-token'),
      onSessionReceived: vi.fn(),
    }
    await act(async () => root.render(
      <MemoryRouter initialEntries={['/liff/account']}>
        <AccountPage context={context} api={pendingApi} />
      </MemoryRouter>,
    ))
    const click = async (label: string) => {
      const button = [...container.querySelectorAll('button')].find((candidate) => candidate.textContent === label)
      if (button === undefined) throw new Error(`button not found: ${label}`)
      await act(async () => button.click())
    }
    await click('全連携解除の内容を確認')
    await click('確認して全連携解除')
    expect(executeUnlink).toHaveBeenCalledTimes(1)

    await act(async () => root.render(<MemoryRouter><p>別画面</p></MemoryRouter>))
    await act(async () => resolveUnlink?.({ state: 'completed' }))

    expect(context.onSessionReceived).not.toHaveBeenCalled()
    expect(executeUnlink).toHaveBeenCalledTimes(1)
    expect(container.textContent).toBe('別画面')
  })
})
