import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
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

// テストケース: 未参照チャネルと参照中チャネルを順に削除する
// 期待値: 成功対象だけを除き、参照中対象には無効化案内を表示する
test('removes only a successfully deleted channel and presents referenced deletion safely', async () => {
  const second = { ...channel(), channelId: '223e4567-e89b-42d3-a456-426614174001', label: '参照中' }
  const deleteChannel = vi.fn()
    .mockResolvedValueOnce({ channelId: channel().channelId, label: channel().label, deleted: true })
    .mockRejectedValueOnce(new ChannelAdminApiError({ code: 'channel_referenced', summary: '削除できません。' }, 409))
  const client = { ...api([channel(), second]), delete: deleteChannel }
  await act(async () => root.render(<ChannelAdminConsole api={client} />))

  const cards = () => [...container.querySelectorAll<HTMLElement>('.channel-card')]
  const clickIn = async (label: string, buttonText: string) => {
    const card = cards().find((candidate) => candidate.textContent?.includes(label))!
    const button = [...card.querySelectorAll('button')].find((candidate) => candidate.textContent === buttonText)
    await act(async () => button?.click())
  }
  await clickIn('開発用 bot', '削除')
  await clickIn('開発用 bot', '削除を確定')
  expect(cards()).toHaveLength(1)
  expect(cards()[0].textContent).not.toContain('開発用 bot')
  expect(container.textContent).toContain('参照中')

  await clickIn('参照中', '削除')
  await clickIn('参照中', '削除を確定')
  expect(container.textContent).toContain('このチャネルには保持すべき履歴があります')
  expect(container.textContent).toContain('参照中')
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
