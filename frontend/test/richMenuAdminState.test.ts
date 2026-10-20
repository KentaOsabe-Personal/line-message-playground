import { describe, expect, test } from 'vitest'

import { parseCurrentMenu } from '../src/richMenuAdminDto'
import type { TemplateDescriptor } from '../src/richMenuAdminDto'
import {
  initialRichMenuAdminState,
  transitionRichMenuAdmin as step,
} from '../src/richMenuAdminState'
import type { CurrentDisplay, PreviewDisplay, RichMenuAdminState } from '../src/richMenuAdminState'
import fixtures from './fixtures/rich-menu-current.json'

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
function display(key: keyof typeof fixtures = 'managed'): CurrentDisplay {
  const parsed = parseCurrentMenu(fixtures[key], channelId, revision)
  if (!parsed.ok) throw new Error('invalid fixture')
  const value = parsed.value
  if (value.current.kind !== 'managed' || value.current.image.status !== 'available')
    return value as CurrentDisplay
  const { base64: _bytes, ...image } = value.current.image
  return { ...value, current: { ...value.current, image: { ...image, imageUrl: 'blob:current' } } }
}
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
const scope = (state: RichMenuAdminState) => ({ epoch: state.epoch, channelId })
function ready(key: keyof typeof fixtures = 'managed') {
  let state = step(initialRichMenuAdminState, { type: 'channelSelected', channel })
  state = step(state, { type: 'currentStarted', generation: 1, ...scope(state) })
  return step(state, {
    type: 'currentSucceeded',
    generation: 1,
    value: display(key),
    ...scope(state),
  })
}
function catalog(
  state: RichMenuAdminState,
  templates = [template, { ...template, templateId: 'other' }],
) {
  state = step(state, { type: 'catalogStarted', generation: 1, ...scope(state) })
  return step(state, { type: 'catalogSucceeded', generation: 1, templates, ...scope(state) })
}
function editing() {
  return step(catalog(ready()), { type: 'beginEdit' })
}
function confirming() {
  let state = editing()
  if (state.editor.state === 'viewing') throw new Error('expected draft')
  const preview: PreviewDisplay = {
    channelId,
    channelLabel: '通知',
    templateId: template.templateId,
    templateVersion: template.version,
    fields: Object.values(state.editor.fields),
    image: {
      contentType: 'image/png',
      width: 2500,
      height: 843,
      digest: 'a'.repeat(64),
      imageUrl: 'blob:preview',
    },
    observation: fixtures.managed.rich.latestObservation as PreviewDisplay['observation'],
    warnings: [],
    confirmationToken: 'opaque',
    expiresAt: '2026-10-18T00:10:00+00:00',
  }
  state = step(state, { type: 'previewStarted', generation: state.editorGeneration })
  return step(state, {
    type: 'previewSucceeded',
    generation: state.editorGeneration,
    preview,
    channelRevision: revision,
    ...scope(state),
  })
}

describe('現在閲覧と編集の純粋遷移 task 7.1', () => {
  // テストケース: カタログ未取得のまま現在画像を読み、更新する。
  // 期待値: 閲覧が成立し、更新開始で旧画像と許可操作を破棄する。
  test('reads independently from catalog and discards previous current on refresh', () => {
    const state = ready()
    expect(state.current.state).toBe('ready')
    expect(state.catalog.state).toBe('not_loaded')
    expect(state.editor.state).toBe('viewing')
    const next = step(state, { type: 'currentStarted', generation: 2, ...scope(state) })
    expect(next.current).toEqual({ state: 'loading', generation: 2 })
    expect(JSON.stringify(next)).not.toContain('blob:current')
    expect(JSON.stringify(next)).not.toContain('base64')
    expect(next).not.toHaveProperty('history')
  })
  // テストケース: 正確な版で編集し、同じIDの別版しかない場合も編集を試す。
  // 期待値: area順に完全URLを引き継ぎ、別版へ暗黙に移行しない。
  test('initializes exact-version fields in area order without fallback', () => {
    const state = editing()
    expect(state.editor.state).toBe('editing')
    if (state.editor.state === 'viewing') throw new Error('draft missing')
    expect(Object.keys(state.editor.fields)).toEqual(template.areas.map((a) => a.field))
    expect(Object.values(state.editor.fields)).toEqual(config.fields)
    const missing = step(catalog(ready(), [{ ...template, version: 999 }]), { type: 'beginEdit' })
    expect(missing.editor).toEqual({ state: 'viewing', reason: 'template_unavailable' })
    const rebuild = step(missing, { type: 'beginRebuild' })
    expect(rebuild.editor.state).toBe('editing')
  })
  // テストケース: 未設定時の明示作成と、対応設定の欠落・項目不足からの編集を試す。
  // 期待値: 作成時だけ空入力を選び、不正設定を推測しない。
  test('requires explicit creation and complete configuration', () => {
    const state = catalog(ready('unset'))
    expect(state.editor.state).toBe('viewing')
    const created = step(state, { type: 'beginCreate' })
    expect(created.editor).toMatchObject({
      state: 'editing',
      fields: { area0: { displayName: '', uri: '' } },
    })
    const value = display()
    if (value.current.kind !== 'managed' || value.current.configuration.status !== 'available')
      throw new Error('managed missing')
    value.current.configuration.fields = []
    const broken = catalog({ ...ready(), current: { state: 'ready', value } })
    expect(step(broken, { type: 'beginEdit' }).editor).toEqual({
      state: 'viewing',
      reason: 'configuration_unavailable',
    })
  })
  // テストケース: 入力済みの別テンプレート選択を取消し、その後承認する。
  // 期待値: 取消しで元入力を保ち、承認で全領域を空にする。
  test('confirms template replacement before clearing inputs', () => {
    const state = editing()
    const pending = step(state, {
      type: 'templateRequested',
      templateId: 'other',
      templateVersion: template.version,
    })
    expect(step(pending, { type: 'templateCancelled' }).editor).toEqual(state.editor)
    const changed = step(pending, { type: 'templateConfirmed' })
    expect(changed.editor).toMatchObject({ state: 'editing', templateId: 'other' })
    if (changed.editor.state === 'viewing') throw new Error('missing draft')
    expect(
      Object.values(changed.editor.fields).every((f) => f.displayName === '' && f.uri === ''),
    ).toBe(true)
  })
  // テストケース: 確認から戻る・キャンセル・期限切れ・競合へ遷移する。
  // 期待値: 確認値と画像を捨て、戻る・失効では入力を保持し、キャンセルでは破棄する。
  test('separates current from preview and preserves drafts only where authorized', () => {
    const state = confirming()
    expect(state.editor.state).toBe('confirming')
    for (const action of [
      { type: 'backToEdit' } as const,
      { type: 'previewExpired', at: '2026-10-18T00:10:00+00:00' } as const,
      { type: 'refreshRequired', reason: 'current_changed', ...scope(state) } as const,
    ]) {
      const next = step(state, action)
      expect(JSON.stringify(next.editor)).not.toContain('opaque')
      expect(JSON.stringify(next.editor)).not.toContain('blob:preview')
      expect(next.editor).toHaveProperty('fields')
    }
    expect(step(state, { type: 'cancelEdit' }).editor).toEqual({ state: 'viewing' })
    expect(JSON.stringify(state.current)).not.toContain('blob:preview')
  })
  // テストケース: preview生成中に入力を変更し旧応答を返す。別途入力エラーを返す。
  // 期待値: 旧確認値を採用せず、入力エラーはdraftと一緒に保持する。
  test('drops delayed previews after input changes and retains field errors', () => {
    let state = editing()
    state = step(state, { type: 'previewStarted', generation: state.editorGeneration })
    const error = {
      code: 'invalid_input',
      summary: '入力を確認',
      fields: { area0: ['表示名が不正'] },
    }
    const failed = step(state, {
      type: 'previewFailed',
      generation: state.editorGeneration,
      error,
      ...scope(state),
    })
    expect(failed.editor).toMatchObject({ state: 'editing', error })
    if (state.editor.state === 'viewing') throw new Error('missing draft')
    const changed = step(state, {
      type: 'draftChanged',
      fields: {
        ...state.editor.fields,
        area0: { displayName: '変更', uri: 'https://example.com/new' },
      },
    })
    const valid = confirming()
    if (valid.editor.state !== 'confirming') throw new Error('missing preview')
    expect(
      step(changed, {
        type: 'previewSucceeded',
        generation: state.editorGeneration,
        preview: valid.editor.preview,
        channelRevision: revision,
        ...scope(changed),
      }),
    ).toEqual(changed)
  })
})

describe('操作と認可の純粋遷移 task 7.2', () => {
  const operationId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
  const operation = (status: 'succeeded' | 'failed' | 'unknown' | 'processing' = 'succeeded') => ({
    operationId,
    kind: 'apply' as const,
    status,
    stage: null,
    result: status === 'failed' ? ('line_rejected' as const) : ('succeeded' as const),
    subjectOperationId: null,
    targetResourceId: null,
    acceptedAt: revision,
    completedAt: revision,
    nextAllowedActions: [],
  })
  function submitting() {
    const state = confirming()
    return step(state, {
      type: 'operationStarted',
      request: { operationId, kind: 'apply' },
      at: revision,
      ...scope(state),
    })
  }
  // テストケース: 適用受付後の処理中・成功・失敗と、表示再取得失敗を受け取る。
  // 期待値: 操作軸が独立して遷移し、確定結果は表示失敗で失われない。
  test('preserves settled results across current read failures and conflicts', () => {
    const state = submitting()
    expect(state.operation.state).toBe('submitting')
    expect(state.editor.state).toBe('viewing')
    const tracking = step(state, {
      type: 'operationReceived',
      operation: operation('processing'),
      ...scope(state),
    })
    expect(tracking.operation.state).toBe('tracking')
    for (const status of ['succeeded', 'failed'] as const) {
      const settled = step(tracking, {
        type: 'operationReceived',
        operation: operation(status),
        ...scope(tracking),
      })
      expect(settled.operation).toEqual({ state: 'settled', operation: operation(status) })
      const loading = step(settled, { type: 'currentStarted', generation: 9, ...scope(settled) })
      const failed = step(loading, {
        type: 'currentFailed',
        generation: 9,
        error: { code: 'network_error', summary: '表示未確認' },
        ...scope(loading),
      })
      expect(failed.operation).toEqual(settled.operation)
      expect(failed.current.state).toBe('failed')
      expect(
        step(failed, { type: 'refreshRequired', reason: 'current_changed', ...scope(failed) })
          .operation,
      ).toEqual(settled.operation)
    }
  })
  // テストケース: 適用通信が切断し、別操作と同一操作の追跡を要求する。
  // 期待値: 成否を推測せず同一IDだけをGET追跡へ戻し、新規適用を受け付けない。
  test('keeps transport uncertainty bound to the same accepted operation', () => {
    const state = submitting()
    const unknown = step(state, {
      type: 'operationTransportFailed',
      operationId,
      error: { code: 'network_error', summary: '結果未確認' },
      ...scope(state),
    })
    expect(unknown.operation.state).toBe('transport_unknown')
    expect(
      step(unknown, { type: 'trackingResumed', operationId: 'other', ...scope(unknown) }),
    ).toEqual(unknown)
    const tracking = step(unknown, { type: 'trackingResumed', operationId, ...scope(unknown) })
    expect(tracking.operation).toEqual({ state: 'tracking', operationId })
    expect(
      step(unknown, {
        type: 'operationStarted',
        request: { operationId: 'new', kind: 'apply' },
        at: revision,
        ...scope(unknown),
      }),
    ).toEqual(unknown)
  })
  // テストケース: 再訪時のcurrent参照と無関係なIDで追跡を再開する。
  // 期待値: active・blocking・latestの保存済み参照だけが追跡へ入り、外部作用を始めない。
  test('resumes only operation references supplied by the server', () => {
    for (const key of ['activeOperation', 'blockingOperation', 'latest'] as const) {
      const state = ready()
      if (state.current.state !== 'ready') throw new Error('not ready')
      if (key === 'latest') state.current.value.rich.historySummary.latestOperationId = operationId
      else state.current.value.rich[key] = operation('processing')
      expect(
        step(state, { type: 'trackingResumed', operationId, ...scope(state) }).operation,
      ).toEqual({ state: 'tracking', operationId })
      expect(
        step(state, { type: 'trackingResumed', operationId: 'unreferenced', ...scope(state) }),
      ).toEqual(state)
    }
  })
  // テストケース: 期限切れ適用、停止中・停止pending・unknown・提供モードで編集を試す。
  // 期待値: 禁止された変更を開始しない。
  test('enforces expiry and backend restrictions without inventing actions', () => {
    const preview = confirming()
    expect(
      step(preview, {
        type: 'operationStarted',
        request: { operationId, kind: 'apply' },
        at: '2026-10-18T00:10:00+00:00',
        ...scope(preview),
      }),
    ).toEqual(preview)
    for (const key of ['inactive', 'attention'] as const) {
      const state = catalog(ready(key))
      expect(step(state, { type: 'beginEdit' }).editor.state).toBe('viewing')
    }
    for (const mode of ['read_only', 'recovery_only', 'unavailable'] as const) {
      const state = catalog(ready())
      if (state.current.state !== 'ready') throw new Error('not ready')
      state.current.value.rich.mode = mode
      expect(step(state, { type: 'beginEdit' }).editor.state).toBe('viewing')
    }
  })
  // テストケース: cleanupで有効な対象組と異なる資源・subjectを指定する。
  // 期待値: サーバーが許可した一組だけを受付し、停止中は同じ組も拒否する。
  test('binds cleanup to exact recovery targets and active channels', () => {
    const state = ready('cleanup')
    if (state.current.state !== 'ready') throw new Error('not ready')
    const target = state.current.value.recoveryTargets[0]
    const request = { ...target, operationId }
    const valid = step(state, { type: 'operationStarted', request, at: revision, ...scope(state) })
    expect(valid.operation.state).toBe('submitting')
    for (const invalid of [
      { ...request, targetResourceId: 'other' },
      { ...request, subjectOperationId: 'other' },
    ]) {
      expect(
        step(state, { type: 'operationStarted', request: invalid, at: revision, ...scope(state) }),
      ).toEqual(state)
    }
    const inactive = { ...state, channel: { ...channel, active: false } }
    expect(
      step(inactive, { type: 'operationStarted', request, at: revision, ...scope(inactive) }),
    ).toEqual(inactive)
  })
  // テストケース: チャネル切替・離脱・認可失効の後に旧成功と旧失敗が到着する。
  // 期待値: チャネル情報・入力・画像・結果を消し、古いepochから復元しない。
  test('erases scoped data and rejects late responses after lifetime changes', () => {
    const old = submitting()
    for (const action of [
      { type: 'sessionInvalidated' } as const,
      { type: 'unmounted' } as const,
      {
        type: 'channelSelected',
        channel: { ...channel, channelId: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc' },
      } as const,
    ]) {
      const cleared = step(old, action)
      expect(cleared.operation.state).toBe('idle')
      expect(cleared.editor.state).toBe('viewing')
      expect(JSON.stringify(cleared)).not.toContain('blob:')
      expect(
        step(cleared, { type: 'operationReceived', operation: operation(), ...scope(old) }),
      ).toEqual(cleared)
      expect(
        step(cleared, {
          type: 'currentFailed',
          generation: 1,
          error: { code: 'network_error', summary: '旧応答' },
          ...scope(old),
        }),
      ).toEqual(cleared)
      expect(
        step(cleared, {
          type: 'catalogFailed',
          generation: 1,
          error: { code: 'network_error', summary: '旧応答' },
          ...scope(old),
        }),
      ).toEqual(cleared)
    }
    const revisit = step(step(old, { type: 'sessionInvalidated' }), {
      type: 'channelSelected',
      channel,
    })
    expect(
      step(revisit, { type: 'operationReceived', operation: operation(), ...scope(old) }),
    ).toEqual(revisit)
  })
  // テストケース: チャネル停止と再有効化後、current再取得前に編集を始める。
  // 期待値: draftを復元せず、最新read完了までは変更を拒否する。
  test('requires a fresh current after reactivation and preserves no old editor', () => {
    const old = confirming()
    const inactive = step(old, {
      type: 'channelUpdated',
      channel: { ...channel, active: false },
      ...scope(old),
    })
    expect(inactive.editor.state).toBe('viewing')
    const active = step(inactive, { type: 'channelUpdated', channel, ...scope(inactive) })
    expect(active.current.state).toBe('refresh_required')
    expect(step(active, { type: 'beginEdit' }).editor.state).toBe('viewing')
    expect(JSON.stringify(active)).not.toContain('opaque')
  })
})

describe('状態境界の回帰', () => {
  // テストケース: 新しいcurrent・catalog取得中に旧世代の成功と失敗が到着する。
  // 期待値: 旧画像・旧許可操作・旧カタログへ戻らない。
  test('ignores stale current and catalog results in both success and failure paths', () => {
    const state = ready()
    const loading = step(state, { type: 'currentStarted', generation: 2, ...scope(state) })
    expect(
      step(loading, {
        type: 'currentSucceeded',
        generation: 1,
        value: display(),
        ...scope(loading),
      }),
    ).toEqual(loading)
    expect(
      step(loading, {
        type: 'currentFailed',
        generation: 1,
        error: { code: 'network_error', summary: '旧応答' },
        ...scope(loading),
      }),
    ).toEqual(loading)
    const loadingCatalog = step(state, { type: 'catalogStarted', generation: 2, ...scope(state) })
    expect(
      step(loadingCatalog, {
        type: 'catalogSucceeded',
        generation: 1,
        templates: [template],
        ...scope(state),
      }),
    ).toEqual(loadingCatalog)
    expect(
      step(loadingCatalog, {
        type: 'catalogFailed',
        generation: 1,
        error: { code: 'network_error', summary: '旧応答' },
        ...scope(state),
      }),
    ).toEqual(loadingCatalog)
  })
  // テストケース: 停止pendingのcleanupをintent対象と別subjectに対して試みる。
  // 期待値: 明示されたintentの対象組だけを許可し、新規作成は拒否する。
  test('limits deactivation-pending recovery to the intent subject', () => {
    const state = catalog(ready('cleanup'))
    if (state.current.state !== 'ready') throw new Error('missing current')
    const target = state.current.value.recoveryTargets[0]
    state.current.value.deactivation = {
      ...fixtures.inactive.deactivation,
      channelActive: true,
      status: 'confirmation_required',
      subjectOperationId: target.subjectOperationId,
      nextAction: 'complete_cleanup_then_recheck',
    }
    const request = { ...target, operationId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' }
    expect(
      step(state, { type: 'operationStarted', request, at: revision, ...scope(state) }).operation
        .state,
    ).toBe('submitting')
    state.current.value.deactivation.subjectOperationId = 'other'
    expect(
      step(state, { type: 'operationStarted', request, at: revision, ...scope(state) }),
    ).toEqual(state)
    state.current.value.rich.effectiveActions.push('new_preview')
    expect(step(state, { type: 'beginRebuild' }).editor.state).toBe('viewing')
  })
  // テストケース: 結果不明operationを受け取った後、currentが通常操作を返す。
  // 期待値: 結果不明を成功に変換せず、新規編集・適用を禁止する。
  test('retains unresolved operation restrictions after current refresh', () => {
    const state = ready()
    const unresolved: RichMenuAdminState = {
      ...state,
      operation: {
        state: 'settled',
        operation: {
          operationId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
          kind: 'apply',
          status: 'unknown',
          result: 'timeout_unknown',
          stage: null,
          subjectOperationId: null,
          targetResourceId: null,
          acceptedAt: revision,
          completedAt: null,
          nextAllowedActions: ['recheck'],
        },
      },
    }
    expect(step(catalog(unresolved), { type: 'beginEdit' }).editor.state).toBe('viewing')
  })
})
