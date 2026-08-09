import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import ChannelAdminConsole from '../src/ChannelAdminConsole'
import { ChannelAdminApiError, type ChannelAdminApiClient } from '../src/channelAdminApi'
import type { ChannelAdminItem } from '../src/channelAdminDto'
import type { RichMenuAdminApiClient } from '../src/richMenuAdminApi'

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

const richApi = (): RichMenuAdminApiClient => ({
  listTemplates: vi.fn().mockResolvedValue([]), createPreview: vi.fn(),
  getState: vi.fn().mockImplementation((channelId: string) => Promise.resolve({ channelId, currentResource: null, blockingOperation: null, activeOperation: null, cleanupResources: [], latestObservation: null, historySummary: { totalCount: 0, latestOperationId: null, latestStatus: null }, nextAllowedActions: [], mode: 'read_only', effectiveActions: [], unavailableReason: null })),
  startOperation: vi.fn(), getOperation: vi.fn(), getHistory: vi.fn().mockResolvedValue({ items: [], nextCursor: null, hasMore: false }),
  getDeactivation: vi.fn().mockResolvedValue(null), startDeactivation: vi.fn(), recheckDeactivation: vi.fn(),
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

// テストケース: exact providerのチャネルカードからリッチメニュー管理を開く。
// 期待値: 対象をmemory-onlyで渡し、URLへチャネル情報を載せない。
test('opens one channel rich-menu console without putting the target in the URL', async () => {
  const scoped = { ...channel(), providerId: '456' }
  const client = api([scoped]); vi.mocked(client.getChannel).mockResolvedValue(scoped)
  const menus = richApi(); const originalUrl = window.location.href
  await act(async () => root.render(<ChannelAdminConsole api={client} richMenuApi={menus} />))
  const open = [...container.querySelectorAll('button')].find(button => button.textContent === 'リッチメニューを管理')
  await act(async () => open?.click())
  expect(container.textContent).toContain('Rich menu console')
  expect(container.textContent).not.toContain('LINEチャネル管理')
  expect(window.location.href).toBe(originalUrl)
  const back = [...container.querySelectorAll('button')].find(button => button.textContent === 'チャネル一覧へ戻る')
  await act(async () => back?.click())
  expect(container.textContent).toContain('LINEチャネル管理')
})

// テストケース: provider未設定のlegacyチャネルカードを描画する。
// 期待値: リッチメニューライフサイクル導線を公開しない。
test('does not expose rich-menu lifecycle navigation for a legacy null-provider channel', async () => {
  const client = api([channel()])
  await act(async () => root.render(<ChannelAdminConsole api={client} richMenuApi={richApi()} />))
  expect([...container.querySelectorAll('button')].some(button => button.textContent === 'リッチメニューを管理')).toBe(false)
})

// テストケース: 設定済みinactiveチャネルの再有効化導線を選ぶ。
// 期待値: 直接更新せず、複合ライフサイクル再取得gateへ移る。
test('routes configured reactivation through the composite lifecycle refresh gate', async () => {
  const scoped = { ...channel(), providerId: '456', credentialsState: 'configured' as const }
  const client = api([scoped]); vi.mocked(client.getChannel).mockResolvedValue(scoped)
  await act(async () => root.render(<ChannelAdminConsole api={client} richMenuApi={richApi()} />))
  const enable = [...container.querySelectorAll('button')].find(button => button.textContent === '有効化')
  await act(async () => enable?.click())
  expect(client.setState).not.toHaveBeenCalled()
  expect(container.textContent).toContain('チャネルを再有効化')
})

// テストケース: 保存済み無効化intentがあるチャネルカードを描画する。
// 期待値: 競合するcard mutationを閉じ、現在intentの状態を表示する。
test('keeps channel-card mutations closed for a persisted deactivation intent', async () => {
  const pending = { ...channel(), providerId: '456', active: true, deactivationSummary: {
    operationId: '223e4567-e89b-42d3-a456-426614174001', status: 'checking' as const,
    reason: null, updatedAt: channel().updatedAt,
  } }
  const client = api([pending]); vi.mocked(client.getChannel).mockResolvedValue(pending)
  await act(async () => root.render(<ChannelAdminConsole api={client} richMenuApi={richApi()} />))
  const edit = [...container.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === '編集')
  expect(edit?.disabled).toBe(true)
  expect(container.textContent).toContain('無効化 checking')
})

// テストケース: 資格情報修復を伴う再有効化が成功する。
// 期待値: channel detailとrich stateの複合再取得gateへ移り、完了後だけ操作を開く。
test('enters the composite refresh gate after credential-repair reactivation succeeds', async () => {
  const inactive = { ...channel(), providerId: '456' }
  const active = { ...inactive, active: true, credentialsState: 'configured' as const, richMenuRefreshRequired: true }
  const client = api([inactive]); vi.mocked(client.setState).mockResolvedValue(active); vi.mocked(client.getChannel).mockResolvedValue(active)
  await act(async () => root.render(<ChannelAdminConsole api={client} richMenuApi={richApi()} />))
  const enable = [...container.querySelectorAll('button')].find(button => button.textContent === '有効化')
  await act(async () => enable?.click())
  const form = container.querySelector('.confirmation form')!
  container.querySelector<HTMLInputElement>('input[name="accessToken"]')!.value = 'token'
  container.querySelector<HTMLInputElement>('input[name="channelSecret"]')!.value = 'secret'
  await act(async () => form.dispatchEvent(new SubmitEvent('submit', { bubbles: true, cancelable: true })))
  expect(client.setState).toHaveBeenCalledTimes(1)
  expect(container.textContent).toContain('再有効化後の最新チャネル状態とリッチメニュー実状態を取得しました')
})

// テストケース: 資格情報修復mutationの応答を保留して再有効化する。
// 期待値: 一件の実mutation完了前にrefresh gateへ進まず、重複操作もしない。
test('waits for the one real credential-repair mutation before entering the refresh gate', async () => {
  const inactive = { ...channel(), providerId: '456' }
  const active = { ...inactive, active: true, credentialsState: 'configured' as const, richMenuRefreshRequired: true }
  let finish!: (value: ChannelAdminItem) => void
  const client = api([inactive]); vi.mocked(client.setState).mockReturnValue(new Promise(resolve => { finish = resolve })); vi.mocked(client.getChannel).mockResolvedValue(active)
  await act(async () => root.render(<ChannelAdminConsole api={client} richMenuApi={richApi()} />))
  await act(async () => [...container.querySelectorAll('button')].find(button => button.textContent === '有効化')?.click())
  const form = container.querySelector('.confirmation form')!
  container.querySelector<HTMLInputElement>('input[name="accessToken"]')!.value = 'token'
  container.querySelector<HTMLInputElement>('input[name="channelSecret"]')!.value = 'secret'
  await act(async () => {
    form.dispatchEvent(new SubmitEvent('submit', { bubbles: true, cancelable: true }))
    form.dispatchEvent(new SubmitEvent('submit', { bubbles: true, cancelable: true }))
  })
  expect(client.setState).toHaveBeenCalledTimes(1)
  expect(container.textContent).not.toContain('Rich menu console')
  await act(async () => finish(active))
  expect(container.textContent).toContain('Rich menu console')
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
  const menus = richApi()
  await act(async () => root.render(<ChannelAdminConsole api={client} richMenuApi={menus} />))
  const remove = [...container.querySelectorAll('button')].find(button => button.textContent === '削除')
  await act(async () => remove?.click())
  expect(client.delete).not.toHaveBeenCalled()
  expect(container.textContent).toContain('チャネルライフサイクル')
  expect(container.textContent).toContain('チャネルを物理削除')
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
