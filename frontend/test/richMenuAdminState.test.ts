import { describe, expect, test } from 'vitest'

import { initialRichMenuAdminState, transitionRichMenuAdmin } from '../src/richMenuAdminState'
import type { RichMenuAdminLoaded } from '../src/richMenuAdminState'

const now = '2026-08-03T10:00:00+09:00'
const channelId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const loaded = (): RichMenuAdminLoaded => ({
  channel: {
    channelId,
    label: '通知',
    messagingApiChannelId: '123',
    botUserId: `U${'a'.repeat(32)}`,
    providerId: '456',
    active: true,
    credentialsState: 'configured',
    credentialsUpdatedAt: now,
    createdAt: now,
    updatedAt: now,
    webhookUrl: `https://example.test/api/line/webhooks/${channelId}/`,
    deactivationSummary: null,
    richMenuRefreshRequired: false,
  },
  rich: {
    channelId,
    currentResource: null,
    blockingOperation: null,
    activeOperation: null,
    cleanupResources: [],
    latestObservation: null,
    historySummary: { totalCount: 0, latestOperationId: null, latestStatus: null },
    nextAllowedActions: ['new_preview'],
    mode: 'enabled',
    effectiveActions: ['new_preview'],
    unavailableReason: null,
  },
  history: { items: [], nextCursor: null, hasMore: false },
  templates: [],
  deactivation: null,
})

describe('rich menu admin state', () => {
  // テストケース: 新しいgeneration開始後に古い成功・失敗が返る。
  // 期待値: 古い応答を採用せず現在generationのloadingを維持する。
  test('ignores stale request generations', () => {
    const first = transitionRichMenuAdmin(initialRichMenuAdminState, {
      type: 'loadStarted',
      generation: 1,
    })
    const latest = transitionRichMenuAdmin(first, { type: 'loadStarted', generation: 2 })
    expect(
      transitionRichMenuAdmin(latest, { type: 'loadSucceeded', generation: 1, value: loaded() }),
    ).toEqual(latest)
    expect(
      transitionRichMenuAdmin(latest, {
        type: 'loadFailed',
        generation: 1,
        error: { code: 'network_error', summary: '失敗' },
      }),
    ).toEqual(latest)
  })

  // テストケース: draftからpreviewを作り、入力・template・revision・期限を変える。
  // 期待値: preview tokenと画像参照を保持するのはpreview_validだけで、各変化時に無効化する。
  test('invalidates preview bindings on every mutable input', () => {
    const ready = transitionRichMenuAdmin(
      transitionRichMenuAdmin(initialRichMenuAdminState, { type: 'loadStarted', generation: 1 }),
      { type: 'loadSucceeded', generation: 1, value: loaded() },
    )
    const dirty = transitionRichMenuAdmin(ready, {
      type: 'draftChanged',
      templateId: 'one',
      templateVersion: 1,
      fields: { area1: { displayName: '案内', uri: 'https://example.com' } },
    })
    const previewing = transitionRichMenuAdmin(dirty, { type: 'previewStarted', generation: 1 })
    const preview = transitionRichMenuAdmin(previewing, {
      type: 'previewSucceeded',
      generation: 1,
      confirmationToken: 'opaque',
      imageUrl: 'blob:preview',
      expiresAt: '2026-08-03T11:00:00+09:00',
      channelRevision: now,
    })
    expect(preview.state).toBe('ready')
    if (preview.state !== 'ready') throw new Error('expected ready')
    expect(preview.editor.state).toBe('preview_valid')
    for (const action of [
      {
        type: 'draftChanged',
        templateId: 'one',
        templateVersion: 1,
        fields: { area1: { displayName: '変更', uri: 'https://example.com' } },
      } as const,
      { type: 'templateChanged', templateId: 'two', templateVersion: 1 } as const,
      { type: 'channelRevisionChanged', channelRevision: '2026-08-03T10:01:00+09:00' } as const,
      { type: 'previewExpired', at: '2026-08-03T11:00:01+09:00' } as const,
    ]) {
      const next = transitionRichMenuAdmin(preview, action)
      if (next.state !== 'ready') throw new Error('expected ready')
      expect(next.editor.state).not.toBe('preview_valid')
      expect(JSON.stringify(next.editor)).not.toContain('opaque')
      expect(JSON.stringify(next.editor)).not.toContain('blob:preview')
    }
  })

  // テストケース: preview request中に入力またはtemplateを変更した後、旧generationの成功が到着する。
  // 期待値: 旧token・画像を変更後draftへ結合せず、ownerによる新previewが必要な状態を維持する。
  test('drops delayed preview success after draft binding changes', () => {
    const ready = transitionRichMenuAdmin(
      transitionRichMenuAdmin(initialRichMenuAdminState, { type: 'loadStarted', generation: 1 }),
      { type: 'loadSucceeded', generation: 1, value: loaded() },
    )
    const dirty = transitionRichMenuAdmin(ready, {
      type: 'draftChanged',
      templateId: 'one',
      templateVersion: 1,
      fields: { area1: { displayName: '旧入力', uri: 'https://example.com/old' } },
    })
    const previewing = transitionRichMenuAdmin(dirty, { type: 'previewStarted', generation: 7 })
    const changedStates = [
      transitionRichMenuAdmin(previewing, {
        type: 'draftChanged',
        templateId: 'one',
        templateVersion: 1,
        fields: { area1: { displayName: '新入力', uri: 'https://example.com/new' } },
      }),
      transitionRichMenuAdmin(previewing, {
        type: 'templateChanged',
        templateId: 'two',
        templateVersion: 1,
      }),
    ]
    for (const changed of changedStates) {
      const delayed = transitionRichMenuAdmin(changed, {
        type: 'previewSucceeded',
        generation: 7,
        confirmationToken: 'token-for-old',
        imageUrl: 'blob:old',
        expiresAt: '2026-08-03T11:00:00+09:00',
        channelRevision: now,
      })
      expect(delayed).toEqual(changed)
      expect(JSON.stringify(delayed)).not.toContain('token-for-old')
      expect(JSON.stringify(delayed)).not.toContain('blob:old')
    }
  })

  // テストケース: session失効またはunmount時にdraft・確認値・画像参照を保持している。
  // 期待値: 全memory-only dataを同期的に破棄してidleへ戻る。
  test('clears all ephemeral references on invalidation and unmount', () => {
    const ready = transitionRichMenuAdmin(
      transitionRichMenuAdmin(initialRichMenuAdminState, { type: 'loadStarted', generation: 1 }),
      { type: 'loadSucceeded', generation: 1, value: loaded() },
    )
    const dirty = transitionRichMenuAdmin(ready, {
      type: 'draftChanged',
      templateId: 'one',
      templateVersion: 1,
      fields: { area1: { displayName: '秘密', uri: 'https://example.com' } },
    })
    expect(transitionRichMenuAdmin(dirty, { type: 'sessionInvalidated' })).toEqual(
      initialRichMenuAdminState,
    )
    expect(transitionRichMenuAdmin(dirty, { type: 'unmounted' })).toEqual(initialRichMenuAdminState)
  })

  // テストケース: ownerが未適用入力の消去を承認する。
  // 期待値: draftとpreviewのmemory-only参照を同じ遷移で空へ戻す。
  test('clears draft and preview references as one editor transition', () => {
    const ready = transitionRichMenuAdmin(
      transitionRichMenuAdmin(initialRichMenuAdminState, { type: 'loadStarted', generation: 1 }),
      { type: 'loadSucceeded', generation: 1, value: loaded() },
    )
    const dirty = transitionRichMenuAdmin(ready, {
      type: 'draftChanged',
      templateId: 'one',
      templateVersion: 1,
      fields: { area1: { displayName: '案内', uri: 'https://example.com' } },
    })
    const previewing = transitionRichMenuAdmin(dirty, { type: 'previewStarted', generation: 1 })
    const preview = transitionRichMenuAdmin(previewing, {
      type: 'previewSucceeded',
      generation: 1,
      confirmationToken: 'opaque',
      imageUrl: 'blob:preview',
      expiresAt: '2026-08-03T11:00:00+09:00',
      channelRevision: now,
    })
    const cleared = transitionRichMenuAdmin(preview, { type: 'editorCleared' })
    expect(cleared.state).toBe('ready')
    if (cleared.state !== 'ready') throw new Error('expected ready')
    expect(cleared.editor).toEqual({ state: 'empty' })
    expect(JSON.stringify(cleared)).not.toContain('opaque')
    expect(JSON.stringify(cleared)).not.toContain('blob:preview')
  })

  // テストケース: previewを入力変更または期限切れで無効化する。
  // 期待値: tokenと画像を破棄しつつ、安全な再生成理由だけを状態へ残す。
  test('retains a safe regeneration reason after preview invalidation and expiry', () => {
    const ready = transitionRichMenuAdmin(
      transitionRichMenuAdmin(initialRichMenuAdminState, { type: 'loadStarted', generation: 1 }),
      { type: 'loadSucceeded', generation: 1, value: loaded() },
    )
    const dirty = transitionRichMenuAdmin(ready, {
      type: 'draftChanged',
      templateId: 'one',
      templateVersion: 1,
      fields: { area1: { displayName: '案内', uri: 'https://example.com' } },
    })
    const previewing = transitionRichMenuAdmin(dirty, { type: 'previewStarted', generation: 1 })
    const preview = transitionRichMenuAdmin(previewing, {
      type: 'previewSucceeded',
      generation: 1,
      confirmationToken: 'opaque',
      imageUrl: 'blob:preview',
      expiresAt: '2026-08-03T11:00:00+09:00',
      channelRevision: now,
    })
    const expired = transitionRichMenuAdmin(preview, {
      type: 'previewExpired',
      at: '2026-08-03T11:00:01+09:00',
    })
    expect(expired.state).toBe('ready')
    if (expired.state !== 'ready') throw new Error('expected ready')
    expect(expired.editor.state).toBe('preview_expired')
  })
})
