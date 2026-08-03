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
const template = { templateId: 'jp-link-one', version: 1, displayName: '1リンク', canvas: { width: 2500, height: 843 }, areas: [{ field: 'whole', description: '全面', bounds: { x: 0, y: 0, width: 2500, height: 843 } }], requiredFields: ['whole'], limits: { displayName: 20, uri: 1000 } }
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

  // 6.1 RED: feature flag OFFでは保守的な状態panelがまだ表示されない。
  test('shows saved state and LINE observation separately', async () => {
    const channelApi = channels(); const richApi = menus()
    vi.mocked(richApi.getState).mockResolvedValue({
      ...rich(),
      latestObservation: { kind: 'external_default', observedAt: now, fingerprint: 'b'.repeat(64), managedResourceId: null },
      unavailableReason: 'integration_not_ready',
      mode: 'unavailable',
      effectiveActions: [],
    })
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} />))
    expect(container.textContent).toContain('LINE実状態')
    expect(container.textContent).toContain('アプリ外の既定')
    expect(container.textContent).toContain('integration_not_ready')
  })

  // 5.1-5.3 RED: editorの有効入力だけをpreview APIへ渡し、object URLとbeforeunloadを画面境界で管理する。
  test('creates and clears one memory-only expiring preview', async () => {
    const channelApi = channels(); const richApi = menus()
    vi.mocked(richApi.listTemplates).mockResolvedValue([template])
    vi.mocked(richApi.createPreview).mockResolvedValue({
      channelId, channelLabel: '通知チャネル', templateId: 'jp-link-one', templateVersion: 1,
      fields: [{ displayName: '案内', uri: 'https://example.com/guide' }],
      image: { contentType: 'image/png', width: 2500, height: 843, digest: 'a'.repeat(64), base64: 'aGVsbG8=' },
      observation: { kind: 'default_none', observedAt: now, fingerprint: 'b'.repeat(64), managedResourceId: null },
      warnings: [], confirmationToken: 'opaque', expiresAt: '2099-08-03T11:00:00+09:00',
    })
    const createObjectURL = vi.fn().mockReturnValue('blob:preview')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} />))

    const inputs = [...container.querySelectorAll('input')]
    await act(async () => { inputs[0].value = '案内'; inputs[0].dispatchEvent(new Event('input', { bubbles: true })) })
    await act(async () => { inputs[1].value = 'https://example.com/guide'; inputs[1].dispatchEvent(new Event('input', { bubbles: true })) })
    const unload = new Event('beforeunload', { cancelable: true }) as BeforeUnloadEvent
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    await act(async () => container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    expect(richApi.createPreview).toHaveBeenCalledWith(channelId, {
      templateId: 'jp-link-one', templateVersion: 1, channelRevision: now,
      fields: { whole: { displayName: '案内', uri: 'https://example.com/guide' } },
    })
    expect(container.textContent).toContain('期限付きプレビュー')
    expect(container.textContent).toContain('1リンク')
    expect(createObjectURL).toHaveBeenCalledTimes(1)

    const editedName = container.querySelector('input') as HTMLInputElement
    await act(async () => { editedName.value = '変更'; editedName.dispatchEvent(new Event('input', { bubbles: true })) })
    expect(container.textContent).toContain('以前のプレビューは適用できません。新しいプレビューを生成してください。')
    expect(container.textContent).not.toContain('期限付きプレビュー')
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:preview')

    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} invalidated />))
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:preview')
    expect(container.textContent).toBe('')
  })

  // review remediation RED: preview中のunmountは遅延応答を採用せずobject URLを作らない。
  test('invalidates an in-flight preview before unmount', async () => {
    const channelApi = channels(); const richApi = menus()
    vi.mocked(richApi.listTemplates).mockResolvedValue([template])
    let resolvePreview!: (value: Awaited<ReturnType<RichMenuAdminApiClient['createPreview']>>) => void
    vi.mocked(richApi.createPreview).mockReturnValue(new Promise(resolve => { resolvePreview = resolve }))
    const createObjectURL = vi.fn().mockReturnValue('blob:late')
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} />))
    const inputs = [...container.querySelectorAll('input')]
    await act(async () => { inputs[0].value = '案内'; inputs[0].dispatchEvent(new Event('input', { bubbles: true })) })
    await act(async () => { inputs[1].value = 'https://example.com/guide'; inputs[1].dispatchEvent(new Event('input', { bubbles: true })) })
    await act(async () => container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    await act(async () => root.unmount())
    resolvePreview({
      channelId, channelLabel: '通知チャネル', templateId: 'jp-link-one', templateVersion: 1,
      fields: [{ displayName: '案内', uri: 'https://example.com/guide' }],
      image: { contentType: 'image/png', width: 2500, height: 843, digest: 'a'.repeat(64), base64: 'aGVsbG8=' },
      observation: { kind: 'default_none', observedAt: now, fingerprint: 'b'.repeat(64), managedResourceId: null },
      warnings: [], confirmationToken: 'opaque', expiresAt: '2099-08-03T11:00:00+09:00',
    })
    await Promise.resolve()
    expect(createObjectURL).not.toHaveBeenCalled()
    root = createRoot(container)
  })

  test('keeps one operation fenced during refresh and reloads saved projections after completion', async () => {
    const channelApi = channels(); const richApi = menus()
    vi.mocked(richApi.listTemplates).mockResolvedValue([template])
    vi.mocked(richApi.getState).mockResolvedValue({ ...rich(), effectiveActions: ['new_preview', 'apply'], nextAllowedActions: ['new_preview', 'apply'] })
    vi.mocked(richApi.createPreview).mockResolvedValue({
      channelId, channelLabel: '通知チャネル', templateId: 'jp-link-one', templateVersion: 1,
      fields: [{ displayName: '案内', uri: 'https://example.com/guide' }],
      image: { contentType: 'image/png', width: 2500, height: 843, digest: 'a'.repeat(64), base64: 'aGVsbG8=' },
      observation: { kind: 'default_none', observedAt: now, fingerprint: 'b'.repeat(64), managedResourceId: null },
      warnings: [], confirmationToken: 'opaque', expiresAt: '2099-08-03T11:00:00+09:00',
    })
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn().mockReturnValue('blob:operation-preview') })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
    let finishOperation!: (value: Awaited<ReturnType<RichMenuAdminApiClient['startOperation']>>) => void
    vi.mocked(richApi.startOperation).mockImplementation((_channelId, input) => new Promise(resolve => {
      finishOperation = resolve
      expect(input.kind).toBe('apply')
    }))
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} />))
    const inputs = [...container.querySelectorAll('input')]
    await act(async () => { inputs[0].value = '案内'; inputs[0].dispatchEvent(new Event('input', { bubbles: true })) })
    await act(async () => { inputs[1].value = 'https://example.com/guide'; inputs[1].dispatchEvent(new Event('input', { bubbles: true })) })
    await act(async () => container.querySelector('form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    const apply = [...container.querySelectorAll('button')].find(button => button.textContent === '適用を確定')!
    await act(async () => apply.click())
    const refresh = [...container.querySelectorAll('button')].find(button => button.textContent === '操作完了を待っています…')!
    expect(refresh.disabled).toBe(true)
    expect(richApi.startOperation).toHaveBeenCalledTimes(1)
    const input = vi.mocked(richApi.startOperation).mock.calls[0][1]
    await act(async () => finishOperation({ operationId: input.operationId, kind: 'apply', status: 'unknown', stage: 'verifying', result: 'timeout_unknown', subjectOperationId: null, targetResourceId: null, acceptedAt: now, completedAt: null, nextAllowedActions: ['recheck'] }))
    expect(richApi.getState).toHaveBeenCalledTimes(2)
    expect(richApi.getHistory).toHaveBeenCalledTimes(2)
    expect(container.textContent).toContain(`apply: ${input.operationId}`)
    expect(container.textContent).toContain('次の明示操作: 結果を再確認')
    expect(richApi.startOperation).toHaveBeenCalledTimes(1)
  })
})
