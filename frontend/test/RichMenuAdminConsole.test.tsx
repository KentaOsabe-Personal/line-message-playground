import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import RichMenuAdminConsole from '../src/RichMenuAdminConsole'
import { ChannelAdminApiError, type ChannelAdminApiClient } from '../src/channelAdminApi'
import type { RichMenuAdminApiClient } from '../src/richMenuAdminApi'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const channelId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const now = '2026-08-03T10:00:00+09:00'
const channel = (active = true) => ({ channelId, label: '通知チャネル', messagingApiChannelId: '123', botUserId: `U${'a'.repeat(32)}`, providerId: '456', active, credentialsState: 'configured' as const, credentialsUpdatedAt: now, createdAt: now, updatedAt: now, webhookUrl: `https://example.test/api/line/webhooks/${channelId}/`, deactivationSummary: null, richMenuRefreshRequired: false })
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

  // 7.1 RED: memory-only画面内遷移はdirty editorを破棄する前に確認する。
  test('confirms a dirty in-app back navigation and clears it on approval', async () => {
    const channelApi = channels(); const richApi = menus(); const onBack = vi.fn()
    vi.mocked(richApi.listTemplates).mockResolvedValue([template])
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} onBack={onBack} />))

    const name = container.querySelector('input') as HTMLInputElement
    await act(async () => { name.value = '未適用'; name.dispatchEvent(new Event('input', { bubbles: true })) })
    const back = [...container.querySelectorAll('button')].find(button => button.textContent === 'チャネル一覧へ戻る')
    await act(async () => back?.click())
    expect(onBack).not.toHaveBeenCalled()
    expect(container.querySelector<HTMLInputElement>('input')?.value).toBe('未適用')
    await act(async () => back?.click())
    expect(onBack).toHaveBeenCalledTimes(1)
    expect(confirm).toHaveBeenCalledTimes(2)
  })

  test('returns to the non-disclosing channel console when channel scope is rejected', async () => {
    const channelApi = channels(); const richApi = menus(); const onBack = vi.fn()
    vi.mocked(channelApi.getChannel).mockRejectedValue(new ChannelAdminApiError({ code: 'provider_mismatch', summary: 'private target' }, 403))
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} onBack={onBack} />))
    expect(onBack).toHaveBeenCalledTimes(1)
    expect(container.textContent).not.toContain('private target')
  })

  // 7.2 RED: 無効化はrich状態を確認後に専用operationを一件だけ開始する。
  test('starts and preserves one explicit channel deactivation intent', async () => {
    const channelApi = channels(); const richApi = menus()
    const result = { channelId, channelActive: true, channelUpdatedAt: now, operationId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', status: 'confirmation_required' as const, reason: 'external_default', subjectOperationId: null, recoveryOperationId: null, nextAction: 'resolve_external_default_then_recheck' as const, acceptedAt: now, updatedAt: now, completedAt: null }
    vi.mocked(richApi.startDeactivation).mockResolvedValue(result)
    vi.mocked(richApi.getDeactivation).mockResolvedValueOnce(null).mockResolvedValue(result)
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} />))
    const open = [...container.querySelectorAll('button')].find(button => button.textContent === 'チャネルを無効化')
    await act(async () => open?.click())
    expect(container.textContent).toContain('リッチメニュー管理状態')
    expect(container.textContent).toContain('LINE実状態')
    const confirm = [...container.querySelectorAll('button')].find(button => button.textContent === '無効化を確定')
    await act(async () => { confirm?.click(); confirm?.click() })
    expect(richApi.startDeactivation).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('外部で既定を解消してから同じ無効化を再確認')
    expect(richApi.recheckDeactivation).not.toHaveBeenCalled()
  })

  test('starts a new deactivation after reactivation preserves the completed intent as history', async () => {
    const latestUpdatedAt = '2026-08-03T11:00:00+09:00'
    const completedOperationId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
    const newOperationId = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
    const channelApi = channels(); const richApi = menus()
    const completed = { channelId, channelActive: false, channelUpdatedAt: now, operationId: completedOperationId, status: 'completed' as const, reason: null, subjectOperationId: null, recoveryOperationId: null, nextAction: 'none' as const, acceptedAt: now, updatedAt: now, completedAt: now }
    const result = { ...completed, channelActive: true, channelUpdatedAt: latestUpdatedAt, operationId: newOperationId, status: 'checking' as const, nextAction: 'get_state' as const, completedAt: null }
    vi.mocked(channelApi.getChannel).mockResolvedValue({ ...channel(), updatedAt: latestUpdatedAt })
    vi.mocked(richApi.getDeactivation).mockResolvedValueOnce(completed).mockResolvedValue(result)
    vi.mocked(richApi.startDeactivation).mockResolvedValue(result)
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(newOperationId as ReturnType<typeof crypto.randomUUID>)

    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} />))
    const open = [...container.querySelectorAll('button')].find(button => button.textContent === 'チャネルを無効化')
    expect(open).toBeDefined()
    await act(async () => open?.click())
    const confirm = [...container.querySelectorAll('button')].find(button => button.textContent === '無効化を確定')
    await act(async () => confirm?.click())

    expect(richApi.startDeactivation).toHaveBeenCalledTimes(1)
    expect(richApi.startDeactivation).toHaveBeenCalledWith(channelId, { operationId: newOperationId, expectedUpdatedAt: latestUpdatedAt })
    expect(newOperationId).not.toBe(completedOperationId)
  })

  test('rechecks the persisted deactivation only after an explicit owner click', async () => {
    const channelApi = channels(); const richApi = menus()
    const pending = { channelId, channelActive: true, channelUpdatedAt: now, operationId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', status: 'confirmation_required' as const, reason: 'cleanup_required', subjectOperationId: null, recoveryOperationId: null, nextAction: 'complete_cleanup_then_recheck' as const, acceptedAt: now, updatedAt: now, completedAt: null }
    vi.mocked(richApi.getDeactivation).mockResolvedValue(pending)
    vi.mocked(richApi.recheckDeactivation).mockResolvedValue(pending)
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} />))
    expect(richApi.recheckDeactivation).not.toHaveBeenCalled()
    const recheck = [...container.querySelectorAll('button')].find(button => button.textContent === '同じ無効化を再確認')
    await act(async () => { recheck?.click(); recheck?.click() })
    expect(richApi.recheckDeactivation).toHaveBeenCalledTimes(1)
    expect(richApi.recheckDeactivation).toHaveBeenCalledWith(channelId, expect.objectContaining({ operationId: pending.operationId, expectedUpdatedAt: now }))
  })

  test('keeps every competing mutation closed while a persisted deactivation is pending', async () => {
    const channelApi = channels(); const richApi = menus()
    vi.mocked(richApi.listTemplates).mockResolvedValue([template])
    vi.mocked(richApi.getDeactivation).mockResolvedValue({ channelId, channelActive: true, channelUpdatedAt: now, operationId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', status: 'checking', reason: null, subjectOperationId: null, recoveryOperationId: null, nextAction: 'get_state', acceptedAt: now, updatedAt: now, completedAt: null })
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} />))
    expect(container.querySelector('input')).toBeNull()
    const remove = [...container.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === 'チャネルを物理削除')
    expect(remove?.disabled).toBe(true)
    expect(container.textContent).toContain('無効化の確認中は競合する操作を実行できません')
  })

  test('discards stale lifecycle dialogs when refresh discovers a persisted deactivation', async () => {
    const channelApi = channels(); const richApi = menus()
    const pending = { channelId, channelActive: true, channelUpdatedAt: now, operationId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', status: 'checking' as const, reason: null, subjectOperationId: null, recoveryOperationId: null, nextAction: 'get_state' as const, acceptedAt: now, updatedAt: now, completedAt: null }
    vi.mocked(richApi.getDeactivation).mockResolvedValueOnce(null).mockResolvedValue(pending)
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} />))
    const deactivate = [...container.querySelectorAll('button')].find(button => button.textContent === 'チャネルを無効化')
    await act(async () => deactivate?.click())
    expect(container.textContent).toContain('無効化を確定')
    const refresh = [...container.querySelectorAll('button')].find(button => button.textContent === '最新状態を再取得')
    await act(async () => refresh?.click())
    expect(container.textContent).not.toContain('無効化を確定')
    expect(container.textContent).toContain('無効化の確認中は競合する操作を実行できません')
    expect(richApi.startDeactivation).not.toHaveBeenCalled()
  })

  // 7.3 RED: 再有効化後はchannel detailとrich stateの再取得完了まで変更操作を閉じる。
  test('gates mutations on a full refresh after reactivation without restoring editor data', async () => {
    const channelApi = channels(false); const richApi = menus()
    const reactivated = { ...channel(true), richMenuRefreshRequired: true }
    vi.mocked(channelApi.getChannel).mockResolvedValueOnce(channel(false)).mockResolvedValue(reactivated)
    vi.mocked(channelApi.setState).mockResolvedValue(reactivated)
    let finishRich!: (value: ReturnType<typeof rich>) => void
    vi.mocked(richApi.getState).mockResolvedValueOnce({ ...rich(), mode: 'read_only', effectiveActions: [], nextAllowedActions: [] }).mockReturnValueOnce(new Promise(resolve => { finishRich = resolve }))
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} />))
    const activate = [...container.querySelectorAll('button')].find(button => button.textContent === 'チャネルを再有効化')
    await act(async () => activate?.click())
    expect(container.textContent).toContain('管理状態を読み込んでいます')
    expect(container.textContent).not.toContain('新しいプレビュー')
    await act(async () => finishRich(rich()))
    expect(container.textContent).toContain('再有効化後の最新チャネル状態とリッチメニュー実状態を取得しました')
    expect(container.textContent).toContain('新しいプレビュー')
    expect(container.querySelectorAll('input')).toHaveLength(0)
  })

  // 7.4 RED: 物理削除はrich参照とterminal履歴を確認し、原子的成功だけを画面終了へ渡す。
  test('confirms rich references and terminal history before one atomic channel delete', async () => {
    const channelApi = channels(false); const richApi = menus(); const onDeleted = vi.fn()
    vi.mocked(richApi.getState).mockResolvedValue({ ...rich(), mode: 'read_only', effectiveActions: [], nextAllowedActions: [], historySummary: { totalCount: 3, latestOperationId: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', latestStatus: 'succeeded' } })
    vi.mocked(channelApi.delete).mockResolvedValue({ channelId, label: '通知チャネル', deleted: true })
    await act(async () => root.render(<RichMenuAdminConsole channelId={channelId} channelApi={channelApi} richApi={richApi} onDeleted={onDeleted} />))
    const open = [...container.querySelectorAll('button')].find(button => button.textContent === 'チャネルを物理削除')
    await act(async () => open?.click())
    expect(container.textContent).toContain('取り消せません')
    expect(container.textContent).toContain('現在のリッチメニュー参照')
    expect(container.textContent).toContain('確定済み履歴 3件')
    const confirm = [...container.querySelectorAll('button')].find(button => button.textContent === '物理削除を確定')
    await act(async () => { confirm?.click(); confirm?.click() })
    expect(channelApi.delete).toHaveBeenCalledTimes(1)
    expect(channelApi.delete).toHaveBeenCalledWith(channelId, now)
    expect(onDeleted).toHaveBeenCalledWith({ channelId, label: '通知チャネル', deleted: true })
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
