import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import ChannelAdminConsole from '../src/ChannelAdminConsole'
import { ChannelAdminApiError, type ChannelAdminApiClient } from '../src/channelAdminApi'
import type { ChannelAdminItem } from '../src/channelAdminDto'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

const channel = (): ChannelAdminItem => ({
  channelId: '123e4567-e89b-42d3-a456-426614174000',
  label: '開発用 bot',
  messagingApiChannelId: '1234567890',
  botUserId: `U${'a'.repeat(32)}`,
  providerId: null,
  active: false,
  credentialsState: 'repair_required',
  credentialsUpdatedAt: null,
  createdAt: '2026-07-29T10:00:00+09:00',
  updatedAt: '2026-07-29T11:00:00+09:00',
  webhookUrl: 'https://example.com/api/line/webhooks/123e4567-e89b-42d3-a456-426614174000/',
  deactivationSummary: null,
  richMenuRefreshRequired: false,
})

const api = (items: ChannelAdminItem[]): ChannelAdminApiClient => ({
  listChannels: vi.fn().mockResolvedValue(items),
  getChannel: vi.fn(),
  register: vi.fn(),
  update: vi.fn(),
  setState: vi.fn(),
  delete: vi.fn(),
  checkConnection: vi.fn(),
})

beforeEach(() => {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
})

afterEach(async () => {
  await act(async () => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

// テストケース: チャネル一覧readの途中で管理画面をunmountする。
// 期待値: read signalを中止し、mutation APIへsignalを追加しない。
test('6.1 scopes channel reads to the console lifetime without changing mutation calls', async () => {
  let resolveRead!: (value: ChannelAdminItem[]) => void
  const client = api([])
  vi.mocked(client.listChannels).mockReturnValue(new Promise((resolve) => { resolveRead = resolve }))
  await act(async () => root.render(<ChannelAdminConsole api={client} />))

  const signal = vi.mocked(client.listChannels).mock.calls[0]?.[0]?.signal as AbortSignal
  expect(signal).toBeInstanceOf(AbortSignal)
  await act(async () => root.render(<p>移動先</p>))
  expect(signal.aborted).toBe(true)
  await act(async () => resolveRead([channel()]))
  expect(container.textContent).toBe('移動先')
  expect(client.register).not.toHaveBeenCalled()
  expect(client.update).not.toHaveBeenCalled()
})

// テストケース: 空一覧、activeチャネル、inactiveチャネルを順に描画する。
// 期待値: 各状態を区別し、inactiveチャネルを利用可能とは表示しない。
test('renders empty and ready states without treating inactive channels as available', async () => {
  const emptyApi = api([])
  await act(async () => root.render(<ChannelAdminConsole api={emptyApi} />))
  expect(container.textContent).toContain('登録済みチャネルはありません')
  expect(container.textContent).toContain('新しいチャネルを登録')

  const readyApi = api([channel()])
  await act(async () => root.render(<ChannelAdminConsole api={readyApi} />))
  expect(container.textContent).toContain('開発用 bot')
  expect(container.textContent).toContain('無効')
  expect(container.textContent).toContain('資格情報の修復が必要')
  expect(container.textContent).toContain('legacy（未設定）')
  expect(container.textContent).toContain(channel().webhookUrl)
  expect(container.textContent).not.toContain('受付可能')
})

// テストケース: exact providerのチャネルカードへroute navigationを接続する。
// 期待値: canonical channel IDだけをdetail pathへ載せ、Consoleをinline mountしない。
test('exposes one channel rich-menu detail link without inline mounting its console', async () => {
  const scoped = { ...channel(), providerId: '456' }
  const client = api([scoped])
  await act(async () => root.render(
    <MemoryRouter><ChannelAdminConsole api={client} onNavigateRichMenu={vi.fn()} /></MemoryRouter>,
  ))
  const open = [...container.querySelectorAll('a')].find(link => link.textContent === 'リッチメニューを管理')
  expect(open?.getAttribute('href')).toBe(`/liff/rich-menus/${scoped.channelId}`)
  expect(open?.classList.contains('button-link')).toBe(true)
  expect(open?.classList.contains('secondary')).toBe(false)
  expect(container.querySelector('.rich-menu-admin')).toBeNull()
  expect(container.textContent).toContain('登録チャネル')
})

// テストケース: provider未設定のlegacyチャネルカードを描画する。
// 期待値: リッチメニューライフサイクル導線を公開しない。
test('does not expose rich-menu lifecycle navigation for a legacy null-provider channel', async () => {
  const client = api([channel()])
  await act(async () => root.render(<ChannelAdminConsole api={client} />))
  expect([...container.querySelectorAll('button')].some(button => button.textContent === 'リッチメニューを管理')).toBe(false)
})

// テストケース: 設定済みinactiveチャネルの再有効化導線を選ぶ。
// 期待値: 直接更新せず、channel detail routeへ対象IDを渡す。
test('routes configured reactivation through the composite lifecycle refresh gate', async () => {
  const scoped = { ...channel(), providerId: '456', credentialsState: 'configured' as const }
  const client = api([scoped]); const navigate = vi.fn()
  await act(async () => root.render(
    <MemoryRouter><ChannelAdminConsole api={client} onNavigateRichMenu={navigate} /></MemoryRouter>,
  ))
  const enable = [...container.querySelectorAll('button')].find(button => button.textContent === '有効化')
  await act(async () => enable?.click())
  expect(client.setState).not.toHaveBeenCalled()
  expect(navigate).toHaveBeenCalledWith(scoped.channelId)
})

// テストケース: 保存済み無効化intentがあるチャネルカードを描画する。
// 期待値: 競合するcard mutationを閉じ、現在intentの状態を表示する。
test('keeps channel-card mutations closed for a persisted deactivation intent', async () => {
  const pending = { ...channel(), providerId: '456', active: true, deactivationSummary: {
    operationId: '223e4567-e89b-42d3-a456-426614174001', status: 'checking' as const,
    reason: null, updatedAt: channel().updatedAt,
  } }
  const client = api([pending]); vi.mocked(client.getChannel).mockResolvedValue(pending)
  await act(async () => root.render(<ChannelAdminConsole api={client} />))
  const edit = [...container.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === '設定を編集')
  expect(edit?.disabled).toBe(true)
  expect(container.textContent).toContain('無効化 checking')
})

// テストケース: チャネル一覧取得を失敗させた後、ownerが明示再取得する。
// 期待値: 古い一覧を表示せず、安全な失敗からclick時だけ一度再試行する。
test('shows a safe load failure and retries only after an explicit click', async () => {
  const listChannels = vi.fn()
    .mockRejectedValueOnce(new Error('private failure'))
    .mockResolvedValueOnce([])
  const client = { ...api([]), listChannels }
  await act(async () => root.render(<ChannelAdminConsole api={client} />))
  expect(container.textContent).toContain('チャネル一覧を取得できませんでした')
  expect(container.textContent).not.toContain('private failure')
  expect(listChannels).toHaveBeenCalledTimes(1)

  const retry = [...container.querySelectorAll('button')].find((item) => item.textContent === '再取得')
  await act(async () => retry?.click())
  expect(listChannels).toHaveBeenCalledTimes(2)
  expect(container.textContent).toContain('登録済みチャネルはありません')
})

// テストケース: create操作を再描画前に二重clickする。
// 期待値: 同じoperation keyのPOSTを一件だけ開始する。
test('starts the same create operation only once before React can rerender', async () => {
  let resolveRegister: ((item: ChannelAdminItem) => void) | undefined
  const register = vi.fn().mockReturnValue(new Promise<ChannelAdminItem>((resolve) => { resolveRegister = resolve }))
  const client = { ...api([]), register }
  await act(async () => root.render(<ChannelAdminConsole api={client} />))
  const open = [...container.querySelectorAll('button')].find((item) => item.textContent === '新しいチャネルを登録')
  await act(async () => open?.click())
  const values: Record<string, string> = {
    label: '新規', messagingApiChannelId: '123', botUserId: `U${'d'.repeat(32)}`,
    providerId: '456', accessToken: 'one-shot-token', channelSecret: 'one-shot-secret',
  }
  for (const [name, value] of Object.entries(values)) container.querySelector<HTMLInputElement>(`input[name="${name}"]`)!.value = value
  const form = container.querySelector('form')!
  await act(async () => {
    form.dispatchEvent(new SubmitEvent('submit', { bubbles: true, cancelable: true }))
    form.dispatchEvent(new SubmitEvent('submit', { bubbles: true, cancelable: true }))
  })
  expect(register).toHaveBeenCalledTimes(1)
  await act(async () => resolveRegister?.(channel()))
})

// テストケース: カードの削除導線を選ぶ。
// 期待値: 旧DELETEを直接呼ばず、参照状態を確認できる専用画面へ収束する。
test('routes card deletion through the rich-menu lifecycle screen', async () => {
  const scoped = { ...channel(), providerId: '456' }
  const client = api([scoped]); vi.mocked(client.getChannel).mockResolvedValue(scoped)
  const navigate = vi.fn()
  await act(async () => root.render(
    <MemoryRouter><ChannelAdminConsole api={client} onNavigateRichMenu={navigate} /></MemoryRouter>,
  ))
  const remove = [...container.querySelectorAll('button')].find(button => button.textContent === '削除')
  await act(async () => remove?.click())
  expect(client.delete).not.toHaveBeenCalled()
  expect(navigate).toHaveBeenCalledWith(scoped.channelId)
})

// テストケース: 接続確認完了時にstale_channelを受け取る
// 期待値: 外部分類を表示せずrefresh_requiredへ遷移し、owner clickでだけ再取得する
test('moves stale connection checks to refresh-required and reloads only after owner click', async () => {
  const listChannels = vi.fn().mockResolvedValueOnce([channel()]).mockResolvedValueOnce([])
  const checkConnection = vi.fn().mockRejectedValue(
    new ChannelAdminApiError({ code: 'stale_channel', summary: '更新競合です。' }, 409),
  )
  const client = { ...api([]), listChannels, checkConnection }
  await act(async () => root.render(<ChannelAdminConsole api={client} />))
  const check = [...container.querySelectorAll('button')].find((button) => button.textContent === '接続を確認')
  await act(async () => check?.click())

  expect(container.textContent).toContain('操作結果を確定できません')
  expect(container.textContent).not.toContain('接続できました')
  expect(listChannels).toHaveBeenCalledTimes(1)
  const refresh = [...container.querySelectorAll('button')].find((button) => button.textContent === '最新状態を再取得')
  await act(async () => refresh?.click())
  expect(listChannels).toHaveBeenCalledTimes(2)
  expect(container.textContent).toContain('登録済みチャネルはありません')
})
