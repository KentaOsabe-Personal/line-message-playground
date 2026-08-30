import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, useNavigate } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { AppRouter } from '../src/App'
import type { AccountApiClient } from '../src/accountApi'
import type { AuthApiClient } from '../src/authApi'
import type { ChannelAdminApiClient } from '../src/channelAdminApi'
import type { ChannelAdminItem } from '../src/channelAdminDto'
import type { LinkedDeliveryApiClient } from '../src/deliveryApi'
import type { LinePlatformLiffAdapter } from '../src/liffClient'
import type { RichMenuAdminApiClient } from '../src/richMenuAdminApi'

vi.mock('../src/RichMenuAdminConsole', () => ({
  default: ({ channelId }: { channelId: string }) => `rich-console:${channelId}`,
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const authApi: AuthApiClient = {
  bootstrap: vi.fn().mockResolvedValue({ state: 'authenticated', profile: { displayName: 'Owner', linked: true } }),
  login: vi.fn(), logout: vi.fn().mockResolvedValue({ state: 'anonymous' }),
}
const liffAdapter: LinePlatformLiffAdapter = {
  initialize: vi.fn().mockResolvedValue('external_browser'), isLoggedIn: vi.fn().mockReturnValue(false),
  login: vi.fn(), reauthenticate: vi.fn(), logout: vi.fn(), getIdToken: vi.fn().mockReturnValue(null),
  getAccessToken: vi.fn().mockReturnValue(null),
}
const authGateProps = {
  config: { liffId: '123-a', liffUrl: 'https://liff.line.me/123-a', endpointUrl: 'https://example.com/liff', redirectUri: 'https://example.com/liff' } as const,
  authApi, liffAdapter,
}

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

const clients = () => {
  const channelApi: ChannelAdminApiClient = {
    listChannels: vi.fn().mockResolvedValue([]), getChannel: vi.fn(), register: vi.fn(), update: vi.fn(),
    setState: vi.fn(), delete: vi.fn(), checkConnection: vi.fn(),
  }
  const accountApi: AccountApiClient = {
    listChannels: vi.fn().mockResolvedValue([]), registerRecipient: vi.fn(), setRecipientEnabled: vi.fn(),
    unlinkRecipient: vi.fn(), previewUnlink: vi.fn(), executeUnlink: vi.fn(),
  }
  const deliveryApi: LinkedDeliveryApiClient = {
    listChannels: vi.fn().mockResolvedValue([]), listRecipients: vi.fn(), preview: vi.fn(), send: vi.fn(), checkStatus: vi.fn(),
  }
  const richMenuApi = {} as RichMenuAdminApiClient
  return { channelApi, accountApi, deliveryApi, richMenuApi }
}

const routedChannel: ChannelAdminItem = {
  channelId: '123e4567-e89b-42d3-a456-426614174000', label: '経路確認bot',
  messagingApiChannelId: '1234567890', botUserId: `U${'a'.repeat(32)}`, providerId: '456',
  active: true, credentialsState: 'configured', credentialsUpdatedAt: '2026-08-29T10:00:00+09:00',
  createdAt: '2026-08-29T09:00:00+09:00', updatedAt: '2026-08-29T10:00:00+09:00',
  webhookUrl: 'https://example.com/webhook', deactivationSummary: null, richMenuRefreshRequired: false,
}

// テストケース: チャネル管理pageから対象チャネルのリッチメニュー管理を選ぶ。
// 期待値: channels URL内へConsoleをinline表示せず、channel detail routeへ遷移する。
test('4.1 navigates rich-menu management out of the channel page', async () => {
  const featureClients = clients()
  vi.mocked(featureClients.channelApi.listChannels).mockResolvedValue([routedChannel])
  vi.mocked(featureClients.channelApi.getChannel).mockResolvedValue(routedChannel)
  await act(async () => root.render(
    <MemoryRouter initialEntries={['/liff/channels']}>
      <AppRouter authGateProps={authGateProps} featureClients={featureClients} />
    </MemoryRouter>,
  ))

  const link = [...container.querySelectorAll('a')]
    .find((candidate) => candidate.textContent === 'リッチメニューを管理') as HTMLAnchorElement
  expect(link.getAttribute('href')).toBe(`/liff/rich-menus/${routedChannel.channelId}`)
  await act(async () => link.click())

  expect(container.querySelector('h1')?.textContent).toBe('リッチメニュー管理')
  expect(container.textContent).toContain(`rich-console:${routedChannel.channelId}`)
  expect(container.textContent).not.toContain('LINEチャネル管理')
})

// テストケース: selectorからdetailへ通常遷移し、browser back相当を実行する。
// 期待値: selector履歴へ戻り、最新channel一覧を再取得してdetail Consoleを破棄する。
test('5.4 pushes detail navigation and restores the selector on browser back', async () => {
  const featureClients = clients()
  vi.mocked(featureClients.channelApi.listChannels).mockResolvedValue([routedChannel])
  vi.mocked(featureClients.channelApi.getChannel).mockResolvedValue(routedChannel)
  const HistoryBack = () => {
    const navigate = useNavigate()
    return <button type="button" data-testid="history-back" onClick={() => navigate(-1)}>戻る</button>
  }
  await act(async () => root.render(
    <MemoryRouter initialEntries={['/liff/rich-menus']}>
      <AppRouter authGateProps={authGateProps} featureClients={featureClients} />
      <HistoryBack />
    </MemoryRouter>,
  ))

  const detail = container.querySelector(`a[href="/liff/rich-menus/${routedChannel.channelId}"]`) as HTMLAnchorElement
  expect(detail).not.toBeNull()
  await act(async () => detail.click())
  expect(container.textContent).toContain(`rich-console:${routedChannel.channelId}`)

  await act(async () => (container.querySelector('[data-testid="history-back"]') as HTMLButtonElement).click())
  expect(container.textContent).not.toContain('rich-console:')
  expect(container.querySelector(`a[href="/liff/rich-menus/${routedChannel.channelId}"]`)).not.toBeNull()
  expect(featureClients.channelApi.listChannels).toHaveBeenCalledTimes(2)
})

// テストケース: メッセージ配信URLへ直接アクセスする。
// 期待値: Delivery Consoleだけをmountして必要なGETだけを開始する。
test('4.4 mounts and loads only the console selected by the current URL', async () => {
  const featureClients = clients()
  await act(async () => root.render(
    <MemoryRouter initialEntries={['/liff/deliveries']}>
      <AppRouter authGateProps={authGateProps} featureClients={featureClients} />
    </MemoryRouter>,
  ))

  expect(container.querySelector('h1')?.textContent).toBe('LINEテスト配信')
  expect(container.textContent).toContain('配信元チャネル')
  expect(featureClients.deliveryApi.listChannels).toHaveBeenCalledTimes(1)
  expect(featureClients.channelApi.listChannels).not.toHaveBeenCalled()
  expect(featureClients.accountApi.listChannels).not.toHaveBeenCalled()
})

// テストケース: 未保存の配信件名を入力して別routeへ移動後、配信画面を再訪する。
// 期待値: 破棄確認なしでdraftを捨て、再訪時は空入力と最新GETから開始する。
test('4.4 discards a delivery draft on route leave and starts from a fresh GET on revisit', async () => {
  const featureClients = clients()
  await act(async () => root.render(
    <MemoryRouter initialEntries={['/liff/deliveries']}>
      <AppRouter authGateProps={authGateProps} featureClients={featureClients} />
    </MemoryRouter>,
  ))

  const subject = container.querySelector('input[name="subject"]') as HTMLInputElement
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(subject, '未保存の件名')
  await act(async () => subject.dispatchEvent(new Event('input', { bubbles: true })))
  expect(subject.value).toBe('未保存の件名')

  await act(async () => (container.querySelector('nav a[href="/liff/account"]') as HTMLAnchorElement).click())
  expect(container.querySelector('h1')?.textContent).toBe('アカウント管理')
  await act(async () => (container.querySelector('nav a[href="/liff/deliveries"]') as HTMLAnchorElement).click())

  expect((container.querySelector('input[name="subject"]') as HTMLInputElement).value).toBe('')
  expect(featureClients.deliveryApi.listChannels).toHaveBeenCalledTimes(2)
  expect(featureClients.accountApi.listChannels).toHaveBeenCalledTimes(1)
})
