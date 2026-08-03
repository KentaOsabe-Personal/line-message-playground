import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import RichMenuAdminConsole from '../src/RichMenuAdminConsole'
import type { ChannelAdminApiClient } from '../src/channelAdminApi'
import type { RichMenuAdminApiClient } from '../src/richMenuAdminApi'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const channelId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const now = '2026-08-03T10:00:00+09:00'
const channel = (active = true) => ({ channelId, label: '通知チャネル', messagingApiChannelId: '123', botUserId: `U${'a'.repeat(32)}`, providerId: '456', active, credentialsState: 'configured' as const, credentialsUpdatedAt: now, createdAt: now, updatedAt: now, webhookUrl: `https://example.test/api/line/webhooks/${channelId}/` })
const rich = () => ({ channelId, currentResource: null, blockingOperation: null, activeOperation: null, cleanupResources: [], latestObservation: null, historySummary: { totalCount: 0, latestOperationId: null, latestStatus: null }, nextAllowedActions: ['new_preview' as const], mode: 'enabled' as const, effectiveActions: ['new_preview' as const], unavailableReason: null })
const channels = (active = true): ChannelAdminApiClient => ({ listChannels: vi.fn(), getChannel: vi.fn().mockResolvedValue(channel(active)), register: vi.fn(), update: vi.fn(), setState: vi.fn(), delete: vi.fn(), checkConnection: vi.fn() })
const menus = (): RichMenuAdminApiClient => ({ listTemplates: vi.fn().mockResolvedValue([]), createPreview: vi.fn(), getState: vi.fn().mockResolvedValue(rich()), startOperation: vi.fn(), getOperation: vi.fn(), getHistory: vi.fn().mockResolvedValue({ items: [], nextCursor: null, hasMore: false }), getDeactivation: vi.fn().mockResolvedValue(null), startDeactivation: vi.fn(), recheckDeactivation: vi.fn() })
let container: HTMLDivElement
let root: Root

describe('RichMenuAdminConsole', () => {
  beforeEach(() => { container = document.createElement('div'); document.body.append(container); root = createRoot(container) })
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks() })

  // テストケース: channel detail、template、rich state、history、無効化を同一generationで取得する。
  // 期待値: 全取得成功時だけ一画面へ合成し、server effective actionだけを表示する。
  test('composes one generation only after every projection succeeds', async () => {
    const channelApi = channels(); const richApi = menus()
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} />))
    expect(container.textContent).toContain('通知チャネル')
    expect(container.textContent).toContain('新しいプレビュー')
    expect(container.textContent).not.toContain('confirmationToken')
    expect(channelApi.getChannel).toHaveBeenCalledTimes(1)
    expect(richApi.getState).toHaveBeenCalledTimes(1)
    expect(richApi.getHistory).toHaveBeenCalledTimes(1)
  })

  // テストケース: 成功表示後の再取得で一領域が失敗する。
  // 期待値: 再取得開始時点で古い操作を消し、失敗後は安全な再取得だけを表示する。
  test('does not retain stale actions while refresh fails', async () => {
    const channelApi = channels(); const richApi = menus()
    vi.mocked(richApi.getState).mockResolvedValueOnce(rich()).mockRejectedValueOnce(new Error('private'))
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} />))
    expect(container.textContent).toContain('新しいプレビュー')
    const refresh = [...container.querySelectorAll('button')].find(button => button.textContent === '最新状態を再取得')
    await act(async () => refresh?.click())
    expect(container.textContent).not.toContain('新しいプレビュー')
    expect(container.textContent).toContain('管理状態を取得できませんでした')
  })

  // テストケース: 無効チャネルとsession失効を画面へ渡す。
  // 期待値: 無効時は保存状態だけの読取専用、失効時は画面とmemory-only dataを即時破棄する。
  test('renders inactive channels read-only and clears on invalidation', async () => {
    const channelApi = channels(false); const richApi = menus()
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} />))
    expect(container.textContent).toContain('読取専用')
    expect(container.textContent).not.toContain('新しいプレビュー')
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} invalidated />))
    expect(container.textContent).toBe('')
  })
})
