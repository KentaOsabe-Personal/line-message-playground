import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { useRichMenuAdmin, type RichMenuAdminOptions } from '../src/useRichMenuAdmin'
import { RichMenuAdminApiError, type RichMenuAdminApiClient } from '../src/richMenuAdminApi'
import { ChannelAdminApiError, type ChannelAdminApiClient } from '../src/channelAdminApi'
import {
  parseCurrentMenu,
  type DeactivationView,
  type OperationView,
  type PreviewView,
  type TemplateDescriptor,
  type CurrentMenuView,
} from '../src/richMenuAdminDto'
import fixtures from './fixtures/rich-menu-current.json'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true
const createUrl = vi.fn(() => `blob:${crypto.randomUUID()}`)
const revokeUrl = vi.fn()
const channelId = fixtures.managed.channelId
const revision = fixtures.managed.channelRevision
const channel = {
  channelId,
  label: '通知',
  messagingApiChannelId: '123',
  botUserId: `U${'a'.repeat(32)}`,
  providerId: '456',
  active: true,
  credentialsState: 'configured' as const,
  credentialsUpdatedAt: revision,
  createdAt: revision,
  updatedAt: revision,
  webhookUrl: 'https://example.test/webhook',
  deactivationSummary: null,
  richMenuRefreshRequired: false,
}
function current(key: keyof typeof fixtures = 'managed'): CurrentMenuView {
  const parsed = parseCurrentMenu(fixtures[key], channelId, revision)
  if (!parsed.ok) throw new Error('invalid fixture')
  return parsed.value
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
let options: RichMenuAdminOptions
let hook: ReturnType<typeof useRichMenuAdmin>
let root: Root
let container: HTMLDivElement
function Probe() {
  hook = useRichMenuAdmin(options)
  return null
}
const render = async () => {
  await act(async () => root.render(<Probe />))
}
const run = async (action: () => void | Promise<void>) => {
  await act(async () => {
    await action()
  })
}
function apis() {
  const channelApi: ChannelAdminApiClient = {
    listChannels: vi.fn(),
    getChannel: vi.fn().mockResolvedValue(channel),
    register: vi.fn(),
    update: vi.fn(),
    setState: vi.fn(),
    delete: vi.fn(),
    checkConnection: vi.fn(),
  }
  const richApi: RichMenuAdminApiClient = {
    listTemplates: vi.fn(),
    createPreview: vi.fn(),
    getCurrent: vi.fn().mockResolvedValue(current()),
    getState: vi.fn(),
    startOperation: vi.fn(),
    getOperation: vi.fn().mockRejectedValue(new Error('not found')),
    getHistory: vi.fn(),
    getDeactivation: vi.fn(),
    startDeactivation: vi.fn(),
    recheckDeactivation: vi.fn(),
  }
  return { channelApi, richApi }
}
beforeEach(() => {
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  createUrl.mockClear()
  revokeUrl.mockClear()
  options = {
    channelId,
    initialChannel: channel,
    ...apis(),
    onSessionInvalid: vi.fn(),
    onUnavailable: vi.fn(),
    onDeleted: vi.fn(),
  }
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL = createUrl
      static revokeObjectURL = revokeUrl
    },
  )
})
afterEach(async () => {
  await run(() => root.unmount())
  container.remove()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})
describe('task 8.1 currentの取得と画像寿命', () => {
  // テストケース: 検証済みchannelを初期表示し更新する。
  // 期待値: 初回はcurrentのみ、更新はchannel後にcurrentを取得し画像を差し替える。
  test('loads current alone and refreshes channel before current', async () => {
    await render()
    expect(hook.state.current.state).toBe('ready')
    expect(options.channelApi.getChannel).not.toHaveBeenCalled()
    expect(options.richApi.listTemplates).not.toHaveBeenCalled()
    const old = createUrl.mock.results[0].value as string
    await run(() => hook.refresh())
    expect(options.channelApi.getChannel).toHaveBeenCalledTimes(1)
    expect(vi.spyOn(options.channelApi, 'getChannel').mock.invocationCallOrder[0]).toBeLessThan(
      vi.spyOn(options.richApi, 'getCurrent').mock.invocationCallOrder[1],
    )
    expect(revokeUrl).toHaveBeenCalledWith(old)
    expect(options.richApi.getHistory).not.toHaveBeenCalled()
    expect(options.richApi.getState).not.toHaveBeenCalled()
    expect(options.richApi.getDeactivation).not.toHaveBeenCalled()
    expect(JSON.stringify(hook.state)).not.toContain('base64')
  })
  // テストケース: readを置換し、旧成功と旧失効を遅延して返す。
  // 期待値: 旧要求を中止し、旧画像生成も認証失効通知も行わない。
  test('discards superseded successes and failures before creating URLs or notifying', async () => {
    const first = deferred<CurrentMenuView>()
    const second = deferred<CurrentMenuView>()
    vi.spyOn(options.richApi, 'getCurrent')
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
    await render()
    const signal = vi.spyOn(options.richApi, 'getCurrent').mock.calls[0][2]?.signal
    let refresh!: Promise<void>
    await run(() => {
      refresh = hook.refresh()
    })
    expect(signal?.aborted).toBe(true)
    await run(() =>
      first.reject(
        new RichMenuAdminApiError(
          { code: 'authentication_required', summary: '失効' },
          'load_failed',
          401,
        ),
      ),
    )
    expect(options.onSessionInvalid).not.toHaveBeenCalled()
    expect(createUrl).not.toHaveBeenCalled()
    await run(async () => {
      second.resolve(current())
      await refresh
    })
    expect(hook.state.current.state).toBe('ready')
    expect(createUrl).toHaveBeenCalledTimes(1)
  })
  // テストケース: 競合後、手動更新し、現行readの認証失効後に画像エラーが届く。
  // 期待値: 自動再試行せず、失効で全情報を消去し後着画像エラーは無視する。
  test('does not loop on conflict and clears all data on authorization loss', async () => {
    vi.spyOn(options.richApi, 'getCurrent').mockRejectedValueOnce(
      new RichMenuAdminApiError(
        { code: 'current_changed', summary: '更新が必要' },
        'load_failed',
        409,
      ),
    )
    await render()
    expect(hook.state.current.state).toBe('refresh_required')
    expect(options.richApi.getCurrent).toHaveBeenCalledTimes(1)
    await run(() => hook.refresh())
    const url = createUrl.mock.results[0].value as string
    vi.spyOn(options.richApi, 'getCurrent').mockRejectedValueOnce(
      new RichMenuAdminApiError(
        { code: 'authentication_required', summary: '失効' },
        'load_failed',
        401,
      ),
    )
    await run(() => hook.refresh())
    await run(() => hook.currentImageFailed(url))
    expect(hook.state.channel).toBeNull()
    expect(options.onSessionInvalid).toHaveBeenCalledTimes(1)
    expect(revokeUrl).toHaveBeenCalledWith(url)
  })
  // テストケース: 表示画像のdecodeに失敗し、離脱する。
  // 期待値: 画像だけを失敗にし許可状態を保持し、Object URLを一度だけ破棄する。
  test('isolates image decode failure and revokes on unmount', async () => {
    await render()
    const url = createUrl.mock.results[0].value as string
    await run(() => hook.currentImageFailed(url))
    expect(hook.state.current.state).toBe('ready')
    if (hook.state.current.state !== 'ready') throw new Error('not ready')
    expect(hook.state.current.value.current).toMatchObject({
      kind: 'managed',
      image: { status: 'unavailable' },
    })
    expect(hook.state.current.value.rich.effectiveActions).toContain('new_preview')
    expect(revokeUrl).toHaveBeenCalledExactlyOnceWith(url)
    await run(() => hook.refresh())
    await run(() => root.unmount())
    expect(revokeUrl).toHaveBeenCalledTimes(2)
    root = createRoot(container)
  })
})

const config = fixtures.managed.current.configuration
const template: TemplateDescriptor = {
  templateId: config.templateId,
  version: config.templateVersion,
  displayName: 'テンプレート',
  canvas: { width: 2500, height: 843 },
  areas: config.fields.map((_, i) => ({
    field: `area${i}`,
    description: '領域',
    bounds: { x: 0, y: 0, width: 100, height: 100 },
  })),
  requiredFields: config.fields.map((_, i) => `area${i}`),
  limits: { displayName: 20, uri: 1000 },
}
function previewValue(): PreviewView {
  return {
    channelId,
    channelLabel: '通知',
    templateId: template.templateId,
    templateVersion: template.version,
    fields: config.fields,
    image: { ...fixtures.managed.current.image, contentType: 'image/png' },
    observation: current().rich.latestObservation!,
    warnings: [],
    confirmationToken: 'opaque-fixture',
    expiresAt: new Date(Date.now() + 600000).toISOString(),
  }
}
async function edit() {
  vi.spyOn(options.richApi, 'listTemplates').mockResolvedValue([
    template,
    { ...template, templateId: 'other' },
  ])
  vi.spyOn(options.richApi, 'createPreview').mockImplementation(async () => previewValue())
  await render()
  await run(() => hook.beginEdit())
}
describe('task 8.2 編集・preview・期限', () => {
  // テストケース: 明示編集でcatalogを取得し、previewから戻って取り消す。
  // 期待値: 同版全入力と完全URLを引き継ぎ、現在画像と別URLを一つ所有し確認値を破棄する。
  test('loads catalog explicitly and owns preview separately from current', async () => {
    await edit()
    expect(hook.state.editor.state).toBe('editing')
    if (hook.state.editor.state === 'viewing') throw new Error('missing draft')
    expect(Object.values(hook.state.editor.fields)).toEqual(config.fields)
    await run(() => hook.preview())
    expect(hook.state.editor.state).toBe('confirming')
    expect(createUrl).toHaveBeenCalledTimes(2)
    const previewUrl = createUrl.mock.results[1].value as string
    await run(() => hook.backToEdit())
    expect(revokeUrl).toHaveBeenCalledExactlyOnceWith(previewUrl)
    expect(hook.state.editor.state).toBe('editing')
    expect(JSON.stringify(hook.state.editor)).not.toContain('opaque-fixture')
    await run(() => hook.cancelEdit())
    expect(hook.state.editor).toEqual({ state: 'viewing' })
    expect(options.richApi.startOperation).not.toHaveBeenCalled()
  })
  // テストケース: 版が廃止された設定を編集し、明示して作り直す。
  // 期待値: 別版へfallbackせず理由を返し、作り直しだけ空入力を生成する。
  test('does not fall back to another version until explicitly rebuilding', async () => {
    vi.spyOn(options.richApi, 'listTemplates').mockResolvedValue([{ ...template, version: 99 }])
    await render()
    await run(() => hook.beginEdit())
    expect(hook.state.editor).toEqual({ state: 'viewing', reason: 'template_unavailable' })
    await run(() => hook.beginRebuild())
    expect(hook.state.editor).toMatchObject({ state: 'editing', templateVersion: 99 })
    if (hook.state.editor.state === 'viewing') throw new Error('missing draft')
    expect(
      Object.values(hook.state.editor.fields).every((f) => f.uri === '' && f.displayName === ''),
    ).toBe(true)
  })
  // テストケース: preview待機中に入力変更とcancelを行い、旧成功・旧失効を返す。
  // 期待値: 古い応答からURLと確認値を作らず、失効通知も行わない。
  test('discards preview success and failure after editing or cancellation', async () => {
    await edit()
    const pending = deferred<PreviewView>()
    vi.spyOn(options.richApi, 'createPreview').mockReturnValueOnce(pending.promise)
    let request!: Promise<void>
    await run(() => {
      request = hook.preview()
    })
    await run(() =>
      hook.changeDraft(
        Object.fromEntries(
          template.areas.map((a) => [
            a.field,
            { displayName: '変更', uri: 'https://example.test/new' },
          ]),
        ),
      ),
    )
    await run(async () => {
      pending.resolve(previewValue())
      await request
    })
    expect(createUrl).toHaveBeenCalledTimes(1)
    const failed = deferred<PreviewView>()
    vi.spyOn(options.richApi, 'createPreview').mockReturnValueOnce(failed.promise)
    await run(() => {
      request = hook.preview()
    })
    await run(() => hook.cancelEdit())
    await run(async () => {
      failed.reject(
        new RichMenuAdminApiError(
          { code: 'authentication_required', summary: '失効' },
          'refresh_required',
        ),
      )
      await request
    })
    expect(options.onSessionInvalid).not.toHaveBeenCalled()
    expect(hook.state.editor.state).toBe('viewing')
  })
  // テストケース: previewの期限まで時計を進め、currentを更新して再生成する。
  // 期待値: URL・確認値だけを破棄し入力を保持、更新後の同じ入力で確認へ戻れる。
  test('expires preview and regenerates preserved draft after current refresh', async () => {
    vi.useFakeTimers()
    await edit()
    await run(() => hook.preview())
    expect(hook.state.editor.state).toBe('confirming')
    const url = createUrl.mock.results[1].value as string
    await run(() => {
      vi.advanceTimersByTime(600000)
    })
    expect(hook.state.editor.state).toBe('preview_invalid')
    expect(revokeUrl).toHaveBeenCalledWith(url)
    expect(JSON.stringify(hook.state.editor)).not.toContain('opaque-fixture')
    await run(() => hook.refresh())
    await run(() => hook.preview())
    expect(hook.state.editor.state).toBe('confirming')
    expect(options.richApi.createPreview).toHaveBeenLastCalledWith(
      channelId,
      expect.objectContaining({
        fields: Object.fromEntries(template.areas.map((a, i) => [a.field, config.fields[i]])),
      }),
    )
  })
  // テストケース: 入力エラー後にtemplateを変更し、取消してから承認する。
  // 期待値: エラーとdraftを保持し、承認時だけ全入力を消去する。
  test('preserves draft on field errors and confirms template changes', async () => {
    await edit()
    vi.spyOn(options.richApi, 'createPreview').mockRejectedValueOnce(
      new RichMenuAdminApiError(
        { code: 'invalid_fields', summary: '入力を修正' },
        'refresh_required',
      ),
    )
    await run(() => hook.preview())
    expect(hook.state.editor).toMatchObject({ state: 'editing', error: { code: 'invalid_fields' } })
    await run(() => hook.changeTemplate('other', template.version))
    await run(() => hook.cancelTemplate())
    expect(hook.state.editor).toMatchObject({ templateId: template.templateId })
    await run(() => hook.changeTemplate('other', template.version))
    await run(() => hook.confirmTemplate())
    expect(hook.state.editor).toMatchObject({ templateId: 'other' })
    if (hook.state.editor.state === 'viewing') throw new Error('missing draft')
    expect(Object.values(hook.state.editor.fields).every((f) => f.uri === '')).toBe(true)
  })
})

function result(
  operationId: string,
  kind: OperationView['kind'] = 'apply',
  status: OperationView['status'] = 'succeeded',
): OperationView {
  return {
    operationId,
    kind,
    status,
    stage: null,
    result: status === 'unknown' ? 'timeout_unknown' : 'succeeded',
    subjectOperationId: null,
    targetResourceId: null,
    acceptedAt: revision,
    completedAt: status === 'processing' ? null : revision,
    nextAllowedActions: [],
  }
}
describe('task 8.3 一度だけの受付と同一操作追跡', () => {
  // テストケース: preview適用を同一イベント内で連打し、確定後のcurrent取得を失敗させる。
  // 期待値: POSTは一件でsignalなし、確認済み入力を渡し、結果は再取得失敗後も残る。
  test('latches before acceptance and retains settled results when refreshing fails', async () => {
    await edit()
    await run(() => hook.preview())
    const pending = deferred<OperationView>()
    vi.spyOn(options.richApi, 'startOperation').mockReturnValueOnce(pending.promise)
    let first!: Promise<void>
    await run(async () => {
      first = hook.apply()
      await hook.apply()
      await hook.startRecovery({
        kind: 'release',
        targetResourceId: fixtures.managed.current.resourceId,
      })
    })
    expect(options.richApi.startOperation).toHaveBeenCalledTimes(1)
    const input = vi.spyOn(options.richApi, 'startOperation').mock.calls[0][1]
    expect(input).toMatchObject({
      kind: 'apply',
      confirmationToken: 'opaque-fixture',
      channelRevision: revision,
    })
    expect(input).not.toHaveProperty('signal')
    vi.spyOn(options.richApi, 'getCurrent').mockRejectedValueOnce(new Error('offline'))
    await run(async () => {
      pending.resolve(result(input.operationId))
      await first
    })
    expect(hook.state.operation).toMatchObject({
      state: 'settled',
      operation: { status: 'succeeded' },
    })
    expect(hook.state.current.state).toBe('failed')
    expect(JSON.stringify(hook.state.editor)).not.toContain('opaque-fixture')
    expect(options.richApi.getHistory).not.toHaveBeenCalled()
  })
  // テストケース: POST通信断後、同一IDのGETも失敗し、明示して再追跡する。
  // 期待値: transport_unknownを保ち、POSTを再送せず同一IDのGETで確定して再取得する。
  test('tracks the held ID after transport loss without resending POST', async () => {
    await edit()
    await run(() => hook.preview())
    vi.spyOn(options.richApi, 'startOperation').mockRejectedValueOnce(new Error('connection lost'))
    await run(() => hook.apply())
    expect(hook.state.operation.state).toBe('transport_unknown')
    const id = vi.spyOn(options.richApi, 'startOperation').mock.calls[0][1].operationId
    expect(options.richApi.getOperation).toHaveBeenCalledWith(id, expect.anything())
    expect(vi.spyOn(options.richApi, 'getOperation').mock.calls[0][1]?.signal).toBeInstanceOf(
      AbortSignal,
    )
    vi.spyOn(options.richApi, 'getOperation').mockResolvedValueOnce(result(id))
    await run(() => hook.trackOperation())
    expect(hook.state.operation.state).toBe('settled')
    expect(hook.state.current.state).toBe('ready')
    expect(options.richApi.startOperation).toHaveBeenCalledTimes(1)
  })
  // テストケース: cleanup候補を再訪し、別subject・別資源・有効な組を実行する。
  // 期待値: 保存参照をGETし、不正な組のPOSTを拒否、有効な一組だけを渡す。
  test('resumes referenced operations and validates cleanup pairs before POST', async () => {
    const value = current('cleanup')
    vi.spyOn(options.richApi, 'getCurrent').mockResolvedValue(value)
    vi.spyOn(options.richApi, 'getOperation').mockResolvedValue(value.rich.blockingOperation!)
    await render()
    expect(options.richApi.getOperation).toHaveBeenCalledWith(
      value.rich.blockingOperation!.operationId,
      expect.anything(),
    )
    const target = value.recoveryTargets[0]
    await run(() => hook.startRecovery({ ...target, subjectOperationId: crypto.randomUUID() }))
    await run(() =>
      hook.startRecovery({ ...target, targetResourceId: fixtures.managed.current.resourceId }),
    )
    expect(options.richApi.startOperation).not.toHaveBeenCalled()
    vi.spyOn(options.richApi, 'startOperation').mockImplementation(async (_, input) =>
      result(input.operationId, input.kind),
    )
    await run(() => hook.startRecovery(target))
    expect(options.richApi.startOperation).toHaveBeenCalledExactlyOnceWith(
      channelId,
      expect.objectContaining(target),
    )
  })
  // テストケース: currentのrevisionを更新した後、旧previewで適用し、期限直後も適用する。
  // 期待値: staleな確認値と期限切れ確認値はPOSTへ進まない。
  test('refuses stale and expired confirmation before mutation', async () => {
    vi.useFakeTimers()
    await edit()
    await run(() => hook.preview())
    await run(() => {
      vi.advanceTimersByTime(600001)
    })
    await run(() => hook.apply())
    expect(options.richApi.startOperation).not.toHaveBeenCalled()
    await run(() => hook.refresh())
    await run(() => hook.apply())
    expect(options.richApi.startOperation).not.toHaveBeenCalled()
  })
  // テストケース: 受付済みPOSTの途中でrouteを離脱し、成功応答を返す。
  // 期待値: mutationは中止せず、後着結果は表示・再取得へ反映しない。
  test('does not cancel accepted mutations or adopt their late results on exit', async () => {
    await edit()
    await run(() => hook.preview())
    const pending = deferred<OperationView>()
    vi.spyOn(options.richApi, 'startOperation').mockReturnValueOnce(pending.promise)
    let request!: Promise<void>
    await run(() => {
      request = hook.apply()
    })
    const input = vi.spyOn(options.richApi, 'startOperation').mock.calls[0][1]
    const reads = vi.spyOn(options.richApi, 'getCurrent').mock.calls.length
    options = { ...options, invalidated: true }
    await render()
    await run(async () => {
      pending.resolve(result(input.operationId))
      await request
    })
    expect(hook.state.channel).toBeNull()
    expect(options.richApi.getCurrent).toHaveBeenCalledTimes(reads)
    expect(options.richApi.startOperation).toHaveBeenCalledTimes(1)
  })
})

function stopping(): CurrentMenuView {
  const value = current('cleanup')
  return {
    ...value,
    deactivation: {
      channelId,
      channelActive: true,
      channelUpdatedAt: revision,
      operationId: 'ffffffff-ffff-4fff-8fff-ffffffffffff',
      status: 'confirmation_required',
      reason: 'cleanup_required',
      subjectOperationId: value.recoveryTargets[0].subjectOperationId,
      recoveryOperationId: null,
      nextAction: 'complete_cleanup_then_recheck',
      acceptedAt: revision,
      updatedAt: revision,
      completedAt: null,
    },
  }
}
describe('task 8.4 チャネル停止・回復・再有効化・削除', () => {
  // テストケース: 編集中に停止を連打し、停止pendingを再取得する。
  // 期待値: 停止POSTは一度、draftと画像を捨て、新規previewと任意の回復を禁止する。
  test('starts deactivation once and restricts pending recovery to the intent', async () => {
    await edit()
    const pending = deferred<DeactivationView>()
    vi.spyOn(options.richApi, 'startDeactivation').mockReturnValueOnce(pending.promise)
    vi.spyOn(options.richApi, 'getCurrent').mockResolvedValue(stopping())
    vi.spyOn(options.richApi, 'getOperation').mockResolvedValue(stopping().rich.blockingOperation!)
    let request!: Promise<void>
    await run(async () => {
      request = hook.startDeactivation()
      await hook.startDeactivation()
      await hook.preview()
    })
    expect(options.richApi.startDeactivation).toHaveBeenCalledTimes(1)
    expect(hook.state.editor.state).toBe('viewing')
    expect(options.richApi.createPreview).not.toHaveBeenCalled()
    await run(async () => {
      pending.resolve(stopping().deactivation!)
      await request
    })
    await run(() => hook.beginEdit())
    await run(() =>
      hook.startRecovery({ kind: 'recheck', subjectOperationId: crypto.randomUUID() }),
    )
    await run(() => hook.recheckDeactivation())
    expect(options.richApi.recheckDeactivation).not.toHaveBeenCalled()
    expect(options.richApi.startOperation).not.toHaveBeenCalled()
    const allowed = stopping().recoveryTargets[0]
    vi.spyOn(options.richApi, 'startOperation').mockImplementation(async (_, input) =>
      result(input.operationId, input.kind),
    )
    await run(() => hook.startRecovery(allowed))
    expect(options.richApi.startOperation).toHaveBeenCalledExactlyOnceWith(
      channelId,
      expect.objectContaining(allowed),
    )
  })
  // テストケース: 停止再確認を実行し、inactiveから再有効化後のcurrentを遅延する。
  // 期待値: 同じ停止IDと新規回復IDを使い、再有効化後はchannel→current完了まで変更を禁止する。
  test('rechecks the same intent and waits for current after reactivation', async () => {
    const pending = stopping()
    pending.rich.activeOperation = null
    pending.rich.blockingOperation = null
    pending.deactivation = { ...pending.deactivation!, nextAction: 'recheck' }
    vi.spyOn(options.richApi, 'getCurrent')
      .mockResolvedValueOnce(pending)
      .mockResolvedValueOnce(current('inactive'))
    vi.spyOn(options.channelApi, 'getChannel').mockResolvedValueOnce({ ...channel, active: false })
    vi.spyOn(options.richApi, 'recheckDeactivation').mockResolvedValueOnce(
      current('inactive').deactivation!,
    )
    await render()
    await run(() => hook.recheckDeactivation())
    expect(options.richApi.recheckDeactivation).toHaveBeenCalledWith(
      channelId,
      expect.objectContaining({
        operationId: pending.deactivation.operationId,
        expectedUpdatedAt: revision,
      }),
    )
    expect(
      vi.spyOn(options.richApi, 'recheckDeactivation').mock.calls[0][1].recoveryOperationId,
    ).not.toBe(pending.deactivation.operationId)
    await run(() => hook.beginEdit())
    await run(() =>
      hook.startRecovery({
        kind: 'cleanup',
        subjectOperationId: fixtures.cleanup.recoveryTargets[0].subjectOperationId,
        targetResourceId: fixtures.cleanup.recoveryTargets[0].targetResourceId,
      }),
    )
    expect(options.richApi.startOperation).not.toHaveBeenCalled()
    const loaded = deferred<CurrentMenuView>()
    vi.spyOn(options.channelApi, 'setState').mockResolvedValueOnce(channel)
    vi.spyOn(options.richApi, 'getCurrent').mockReturnValueOnce(loaded.promise)
    let request!: Promise<void>
    await run(() => {
      request = hook.reactivate()
    })
    await run(() => hook.beginCreate())
    expect(hook.state.current.state).toBe('loading')
    expect(hook.state.editor.state).toBe('viewing')
    await run(async () => {
      loaded.resolve(current('unset'))
      await request
    })
    expect(hook.state.channel?.active).toBe(true)
    expect(hook.state.current.state).toBe('ready')
    expect(hook.state.editor.state).toBe('viewing')
  })
  // テストケース: 履歴ありのチャネルを削除確認し、参照による拒否後に再取得・再確認して削除する。
  // 期待値: 履歴APIを呼ばず履歴消失を確認でき、確認なし削除を拒否し、参照エラーと削除通知を保持する。
  test('requires revision-bound deletion confirmation and preserves reference rejection', async () => {
    await render()
    await run(() => hook.deleteChannel())
    expect(options.channelApi.delete).not.toHaveBeenCalled()
    await run(() => hook.requestDelete())
    expect(hook.lifecycle.deleteConfirmation).toMatchObject({
      channelId,
      revision,
      hasHistory: true,
      label: channel.label,
    })
    vi.spyOn(options.channelApi, 'delete').mockRejectedValueOnce(
      new ChannelAdminApiError({ code: 'channel_referenced', summary: '参照あり' }, 409),
    )
    await run(() => hook.deleteChannel())
    expect(hook.lifecycle.error?.code).toBe('channel_referenced')
    expect(options.onDeleted).not.toHaveBeenCalled()
    await run(() => hook.refresh())
    await run(() => hook.requestDelete())
    vi.spyOn(options.channelApi, 'delete').mockResolvedValueOnce({
      channelId,
      label: channel.label,
      deleted: true,
    })
    await run(() => hook.deleteChannel())
    expect(hook.state.channel).toBeNull()
    expect(options.onDeleted).toHaveBeenCalledExactlyOnceWith({
      channelId,
      label: channel.label,
      deleted: true,
    })
    expect(options.richApi.getHistory).not.toHaveBeenCalled()
  })
  // テストケース: 停止mutation中に認可が失効し、旧失敗応答が届く。
  // 期待値: 結果・エラー・確認値を復活させず、失効後の通知も重複しない。
  test('discards late lifecycle errors after invalidation', async () => {
    await render()
    const pending = deferred<DeactivationView>()
    vi.spyOn(options.richApi, 'startDeactivation').mockReturnValueOnce(pending.promise)
    let request!: Promise<void>
    await run(() => {
      request = hook.startDeactivation()
    })
    options = { ...options, invalidated: true }
    await render()
    await run(async () => {
      pending.reject(
        new RichMenuAdminApiError(
          { code: 'authentication_required', summary: '失効' },
          'refresh_required',
        ),
      )
      await request
    })
    expect(hook.state.channel).toBeNull()
    expect(hook.lifecycle).toEqual({
      busy: false,
      error: null,
      result: null,
      deleteConfirmation: null,
    })
    expect(options.onSessionInvalid).not.toHaveBeenCalled()
  })
})

describe('task 8 寿命境界の回帰', () => {
  // テストケース: 有効previewの確認中に現在画像のdecodeが失敗する。
  // 期待値: 現在画像だけを失敗にし、draft・preview・確認値と許可操作を保持する。
  test('preserves confirmation when only the current image fails decoding', async () => {
    await edit()
    await run(() => hook.preview())
    const url = createUrl.mock.results[0].value as string
    await run(() => hook.currentImageFailed(url))
    expect(hook.state.editor.state).toBe('confirming')
    expect(hook.state.current.state).toBe('ready')
    expect(revokeUrl).toHaveBeenCalledExactlyOnceWith(url)
    expect(JSON.stringify(hook.state.editor)).toContain('opaque-fixture')
  })
  // テストケース: 親が新しい通知callbackで再描画する。
  // 期待値: 読取・編集中内容を初期化せず、次の認可失効は最新callbackへ通知する。
  test('does not restart the screen when notification callback identity changes', async () => {
    await edit()
    options = { ...options, onSessionInvalid: vi.fn(), onUnavailable: vi.fn() }
    await render()
    expect(options.richApi.getCurrent).toHaveBeenCalledTimes(1)
    expect(hook.state.editor.state).toBe('editing')
    vi.spyOn(options.richApi, 'getCurrent').mockRejectedValueOnce(
      new RichMenuAdminApiError(
        { code: 'authentication_required', summary: '失効' },
        'load_failed',
      ),
    )
    await run(() => hook.refresh())
    expect(options.onSessionInvalid).toHaveBeenCalledTimes(1)
  })
  // テストケース: 旧channelのreadを待機させ、channel変更後に成功応答を返す。
  // 期待値: 旧readは中止され、旧画像を生成せず、新channelの画像一つだけを所有する。
  test('discards a previous channel read and aborts all reads on route exit', async () => {
    const pending = deferred<CurrentMenuView>()
    vi.spyOn(options.richApi, 'getCurrent').mockReturnValueOnce(pending.promise)
    const signal = new AbortController()
    options = { ...options, readSignal: signal.signal }
    await render()
    const oldRead = vi.spyOn(options.richApi, 'getCurrent').mock.calls[0][2]?.signal
    const nextId = crypto.randomUUID()
    const next = current()
    next.channelId = nextId
    next.rich.channelId = nextId
    vi.spyOn(options.richApi, 'getCurrent').mockResolvedValueOnce(next)
    options = { ...options, channelId: nextId, initialChannel: { ...channel, channelId: nextId } }
    await render()
    expect(oldRead?.aborted).toBe(true)
    await run(() => pending.resolve(current()))
    expect(hook.state.channel?.channelId).toBe(nextId)
    expect(createUrl).toHaveBeenCalledTimes(1)
    await run(() => signal.abort())
    expect(hook.state.channel).toBeNull()
    expect(revokeUrl).toHaveBeenCalledTimes(1)
  })
  // テストケース: 管理終了の対象を外部IDへ差し替え、有効な現在管理資源で再実行する。
  // 期待値: 外部IDを拒否し、現在管理資源だけを既存releaseへ渡す。
  test('releases only the currently authorized managed resource', async () => {
    await render()
    await run(() => hook.startRecovery({ kind: 'release', targetResourceId: crypto.randomUUID() }))
    expect(options.richApi.startOperation).not.toHaveBeenCalled()
    vi.spyOn(options.richApi, 'startOperation').mockImplementation(async (_, input) =>
      result(input.operationId, input.kind),
    )
    await run(() =>
      hook.startRecovery({
        kind: 'release',
        targetResourceId: fixtures.managed.current.resourceId,
      }),
    )
    expect(options.richApi.startOperation).toHaveBeenCalledExactlyOnceWith(
      channelId,
      expect.objectContaining({
        kind: 'release',
        targetResourceId: fixtures.managed.current.resourceId,
      }),
    )
  })
})

describe('task 11.3 画像・確認値の寿命と非永続化', () => {
  // テストケース: 確認中に全連携解除・owner利用不能・画面離脱の失効通知を受ける。
  // 期待値: 現在画像とpreviewを一度ずつ破棄し、確認値・draft・操作可否を復元しない。
  test.each(['全連携解除', 'owner利用不能', '画面離脱'])(
    '%s clears both owned images and confirmation',
    async (reason) => {
      const route = new AbortController()
      options = { ...options, readSignal: route.signal }
      await edit()
      await run(() => hook.preview())
      const urls = createUrl.mock.results.map((entry) => entry.value as string)
      expect(urls).toHaveLength(2)
      if (route.signal.aborted) throw new Error('route already aborted')
      if (reason === '画面離脱') await run(() => route.abort())
      else {
        options = { ...options, invalidated: true }
        await render()
      }
      for (const url of urls) {
        expect(revokeUrl.mock.calls.filter(([value]) => value === url)).toHaveLength(1)
        await run(() => hook.currentImageFailed(url))
      }
      expect(hook.state.channel).toBeNull()
      expect(hook.state.editor).toEqual({ state: 'viewing' })
      expect(JSON.stringify(hook.state)).not.toContain('opaque-fixture')
      await run(() => hook.apply())
      expect(options.richApi.startOperation).not.toHaveBeenCalled()
    },
  )

  // テストケース: 編集で完全URLを変更してpreviewを生成し、unmount後に同じチャネルを再mountする。
  // 期待値: storage・ページURLに入力/画像/確認値を書かず、draftもpreviewも復元しない。
  test('does not persist or restore drafts, full URLs, images or confirmation', async () => {
    const storage = vi.spyOn(Storage.prototype, 'setItem')
    const push = vi.spyOn(history, 'pushState')
    const replace = vi.spyOn(history, 'replaceState')
    const href = location.href
    await edit()
    const uri = 'https://example.test/private?draft=unique-canary#full'
    await run(() => hook.changeDraft({ area0: { displayName: '未保存の変更', uri } }))
    await run(() => hook.preview())
    expect(storage).not.toHaveBeenCalled()
    expect(push).not.toHaveBeenCalled()
    expect(replace).not.toHaveBeenCalled()
    expect(location.href).toBe(href)
    const urls = createUrl.mock.results.map((entry) => entry.value as string)
    await run(() => root.unmount())
    for (const url of urls) expect(revokeUrl).toHaveBeenCalledWith(url)
    root = createRoot(container)
    await render()
    expect(hook.state.editor).toEqual({ state: 'viewing' })
    expect(JSON.stringify(hook.state)).not.toContain(uri)
    expect(JSON.stringify(hook.state)).not.toContain('opaque-fixture')
    expect(options.richApi.startOperation).not.toHaveBeenCalled()
  })
})
