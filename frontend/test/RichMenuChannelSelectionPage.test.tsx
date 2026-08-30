import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import RichMenuChannelSelectionPage, { projectRichMenuChannelChoice } from '../src/RichMenuChannelSelectionPage'
import type { ChannelAdminApiClient } from '../src/channelAdminApi'
import type { ChannelAdminItem } from '../src/channelAdminDto'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const channel = (overrides: Partial<ChannelAdminItem> = {}): ChannelAdminItem => ({
  channelId: '123e4567-e89b-42d3-a456-426614174000', label: '通知bot',
  messagingApiChannelId: '1234567890', botUserId: `U${'a'.repeat(32)}`, providerId: '456',
  active: true, credentialsState: 'configured', credentialsUpdatedAt: '2026-08-29T10:00:00+09:00',
  createdAt: '2026-08-29T09:00:00+09:00', updatedAt: '2026-08-29T10:00:00+09:00',
  webhookUrl: 'https://example.com/api/line/webhooks/123e4567-e89b-42d3-a456-426614174000/',
  deactivationSummary: null, richMenuRefreshRequired: false, ...overrides,
})

const api = (items: ChannelAdminItem[]): ChannelAdminApiClient => ({
  listChannels: vi.fn().mockResolvedValue(items), getChannel: vi.fn(), register: vi.fn(), update: vi.fn(),
  setState: vi.fn(), delete: vi.fn(), checkConnection: vi.fn(),
})

describe('リッチメニューチャネル選択', () => {
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

  // テストケース: selectorのchannel read中に別routeへ移動する。
  // 期待値: selector固有signalを中止し、後着結果を描画しない。
  test('6.1 aborts selector read and fences its late result on route leave', async () => {
    let resolveRead!: (value: ChannelAdminItem[]) => void
    const client = api([])
    vi.mocked(client.listChannels).mockReturnValue(new Promise((resolve) => { resolveRead = resolve }))
    await act(async () => root.render(
      <MemoryRouter initialEntries={['/liff/rich-menus']}>
        <RichMenuChannelSelectionPage api={client} />
      </MemoryRouter>,
    ))
    const signal = vi.mocked(client.listChannels).mock.calls[0]?.[0]?.signal as AbortSignal
    expect(signal).toBeInstanceOf(AbortSignal)

    await act(async () => root.render(<MemoryRouter><p>移動先</p></MemoryRouter>))
    expect(signal.aborted).toBe(true)
    await act(async () => resolveRead([channel()]))
    expect(container.textContent).toBe('移動先')
  })

  // テストケース: active、inactive、provider未設定、lifecycle進行中のチャネルを投影する。
  // 期待値: 各チャネルをeditable、readOnly、unavailable、recoveryOnlyへ純粋に分類する。
  test('5.1 projects every registered channel into one safe rich-menu mode', () => {
    expect(projectRichMenuChannelChoice(channel()).mode).toBe('editable')
    expect(projectRichMenuChannelChoice(channel({ active: false })).mode).toBe('readOnly')
    expect(projectRichMenuChannelChoice(channel({ providerId: null })).mode).toBe('unavailable')
    expect(projectRichMenuChannelChoice(channel({
      deactivationSummary: {
        operationId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', status: 'checking', reason: null,
        updatedAt: '2026-08-29T10:30:00+09:00',
      },
    })).mode).toBe('recoveryOnly')
  })

  // テストケース: provider IDありと未設定を含む全チャネルを選択pageへ表示する。
  // 期待値: provider IDありだけdetail Linkを示し、未設定には理由とチャネル設定Linkを示す。
  test('5.1 shows detail links only for provider-ready channels', async () => {
    await act(async () => root.render(
      <MemoryRouter initialEntries={['/liff/rich-menus']}>
        <RichMenuChannelSelectionPage api={api([channel(), channel({
          channelId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', label: '未設定bot', providerId: null,
          webhookUrl: 'https://example.com/api/line/webhooks/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/',
        })])} />
      </MemoryRouter>,
    ))

    const links = [...container.querySelectorAll('a')]
    expect(links.some((link) => link.getAttribute('href') === '/liff/rich-menus/123e4567-e89b-42d3-a456-426614174000')).toBe(true)
    expect(links.some((link) => link.getAttribute('href') === '/liff/rich-menus/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')).toBe(false)
    expect(container.textContent).toContain('provider IDが設定されていません')
    expect(links.some((link) => link.getAttribute('href') === '/liff/channels')).toBe(true)
    expect(container.querySelector('[role="status"]')?.textContent).toContain('2件のチャネルを表示しました')
  })

  // テストケース: 登録済みチャネルが0件の選択pageを表示する。
  // 期待値: empty stateとチャネル管理Linkだけを表示し、登録formを重複させない。
  test('5.1 renders a bounded empty state without a registration form', async () => {
    await act(async () => root.render(
      <MemoryRouter initialEntries={['/liff/rich-menus']}>
        <RichMenuChannelSelectionPage api={api([])} />
      </MemoryRouter>,
    ))

    expect(container.textContent).toContain('登録済みチャネルはありません')
    expect(container.querySelector('a[href="/liff/channels"]')).not.toBeNull()
    expect(container.querySelector('form')).toBeNull()
  })
})
