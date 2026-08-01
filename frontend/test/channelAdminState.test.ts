import { describe, expect, test } from 'vitest'

import type { ChannelAdminItem } from '../src/channelAdminDto'
import {
  initialChannelAdminState,
  transitionChannelAdmin,
} from '../src/channelAdminState'
import type { ChannelAdminState } from '../src/channelAdminState'

const channel = (label = '通知チャネル'): ChannelAdminItem => ({
  channelId: '11111111-1111-4111-8111-111111111111',
  label,
  messagingApiChannelId: '1234567890',
  botUserId: 'U1234567890abcdef1234567890abcdef',
  providerId: '9876543210',
  active: true,
  credentialsState: 'configured',
  credentialsUpdatedAt: '2026-08-01T10:00:00+09:00',
  createdAt: '2026-07-01T10:00:00Z',
  updatedAt: '2026-08-01T10:00:00+09:00',
  webhookUrl: 'https://example.test/api/line/webhooks/11111111-1111-4111-8111-111111111111/',
})

describe('channel admin state', () => {
  // テストケース: 一覧取得を開始し、空またはチャネルありの応答を受け取る。
  // 期待値: loadingからempty/readyへ排他的に遷移する。
  test('models loading, empty, and ready states exclusively', () => {
    const loading = transitionChannelAdmin(initialChannelAdminState, { type: 'loadStarted', generation: 1 })
    expect(loading).toEqual({ state: 'loading', generation: 1 })
    expect(transitionChannelAdmin(loading, { type: 'loadSucceeded', generation: 1, items: [] }))
      .toEqual({ state: 'empty', operations: {} })
    expect(transitionChannelAdmin(loading, { type: 'loadSucceeded', generation: 1, items: [channel()] }))
      .toEqual({ state: 'ready', items: [channel()], operations: {} })
  })

  // テストケース: 新しい取得開始後に古いgenerationの成功・失敗が到着する。
  // 期待値: stale responseを破棄して現在のloadingを維持する。
  test('ignores stale load generations', () => {
    const first = transitionChannelAdmin(initialChannelAdminState, { type: 'loadStarted', generation: 1 })
    const latest = transitionChannelAdmin(first, { type: 'loadStarted', generation: 2 })
    const staleSuccess = transitionChannelAdmin(latest, { type: 'loadSucceeded', generation: 1, items: [channel()] })
    const staleFailure = transitionChannelAdmin(latest, {
      type: 'loadFailed', generation: 1, error: { code: 'network_error', summary: '失敗' },
    })
    expect(staleSuccess).toBe(latest)
    expect(staleFailure).toBe(latest)
  })

  // テストケース: 現在generationの一覧取得が安全なerrorで失敗する。
  // 期待値: 古い一覧を持たず、明示再取得可能なload_failedへ遷移する。
  test('moves the current load failure to an exclusive safe error state', () => {
    const loading = transitionChannelAdmin(initialChannelAdminState, { type: 'loadStarted', generation: 1 })
    expect(transitionChannelAdmin(loading, {
      type: 'loadFailed',
      generation: 1,
      error: { code: 'storage_unavailable', summary: '取得できません。' },
    })).toEqual({
      state: 'load_failed',
      error: { code: 'storage_unavailable', summary: '取得できません。' },
    })
  })

  // テストケース: 同じ操作keyを連続開始し、別keyも開始する。
  // 期待値: 二重開始だけを拒否し、一覧と独立操作を維持する。
  test('suppresses duplicate operation keys without hiding read-only data', () => {
    const ready: ChannelAdminState = { state: 'ready', items: [channel()], operations: {} }
    const first = transitionChannelAdmin(ready, { type: 'operationStarted', key: `${channel().channelId}:update` })
    const duplicate = transitionChannelAdmin(first, { type: 'operationStarted', key: `${channel().channelId}:update` })
    const independent = transitionChannelAdmin(first, { type: 'operationStarted', key: `${channel().channelId}:check` })
    expect(duplicate).toBe(first)
    expect(independent.state === 'ready' && Object.keys(independent.operations)).toHaveLength(2)
    expect(independent.state === 'ready' && independent.items).toEqual([channel()])
  })

  // テストケース: mutation成功時にclient入力とは別のserver DTOを受け取る。
  // 期待値: server DTOだけで対象itemを置換し、操作中状態を解除する。
  test('updates items only from a successful server DTO', () => {
    const key = `${channel().channelId}:update`
    const ready: ChannelAdminState = { state: 'ready', items: [channel()], operations: { [key]: 'pending' } }
    const updated = channel('サーバー確定名')
    const next = transitionChannelAdmin(ready, { type: 'mutationSucceeded', key, item: updated })
    expect(next).toEqual({ state: 'ready', items: [updated], operations: {} })
  })

  // テストケース: mutationのnetwork errorまたはstale_channelを受け取る。
  // 期待値: 成功を推測せず明示refresh必須へ遷移する。
  test('requires explicit refresh for unknown and stale mutation results', () => {
    const key = `${channel().channelId}:state`
    const ready: ChannelAdminState = { state: 'ready', items: [channel()], operations: { [key]: 'pending' } }
    expect(transitionChannelAdmin(ready, {
      type: 'operationFailed', key, error: { code: 'network_error', summary: '不明' },
    })).toEqual({ state: 'refresh_required', reason: 'unknown_result' })
    expect(transitionChannelAdmin(ready, {
      type: 'operationFailed', key, error: { code: 'stale_channel', summary: '競合' },
    })).toEqual({ state: 'refresh_required', reason: 'stale_channel' })
  })

  // テストケース: 結果が確定したsafe operation errorを受け取る。
  // 期待値: 対象pendingだけを解除し、取得済みチャネルと他操作を維持する。
  test('clears only the failed operation for a determinate safe error', () => {
    const failedKey = `${channel().channelId}:update`
    const otherKey = `${channel().channelId}:check`
    const ready: ChannelAdminState = {
      state: 'ready',
      items: [channel()],
      operations: { [failedKey]: 'pending', [otherKey]: 'pending' },
    }
    expect(transitionChannelAdmin(ready, {
      type: 'operationFailed',
      key: failedKey,
      error: { code: 'validation_error', summary: '入力を確認してください。' },
    })).toEqual({ state: 'ready', items: [channel()], operations: { [otherKey]: 'pending' } })
  })

  // テストケース: 一覧最後のチャネル削除がserverで確定する。
  // 期待値: server指定channelだけを除去してemptyへ遷移する。
  test('removes a server-confirmed deletion and reaches empty', () => {
    const key = `${channel().channelId}:delete`
    const ready: ChannelAdminState = {
      state: 'ready', items: [channel()], operations: { [key]: 'pending' },
    }
    expect(transitionChannelAdmin(ready, {
      type: 'deleteSucceeded', key, channelId: channel().channelId,
    })).toEqual({ state: 'empty', operations: {} })
  })

  // テストケース: 接続確認などDTO更新を伴わない操作が完了する。
  // 期待値: 対象pendingだけを解除し、一覧snapshotを変更しない。
  test('completes a read-only operation without changing channel items', () => {
    const key = `${channel().channelId}:check`
    const ready: ChannelAdminState = {
      state: 'ready', items: [channel()], operations: { [key]: 'pending' },
    }
    expect(transitionChannelAdmin(ready, { type: 'operationCompleted', key }))
      .toEqual({ state: 'ready', items: [channel()], operations: {} })
  })

  // テストケース: refresh_requiredからownerが明示的に再取得する。
  // 期待値: 自動再実行情報を持たず、新generationのloadingへ移る。
  test('refreshes only through an explicit load action', () => {
    const required = { state: 'refresh_required', reason: 'unknown_result' } as const
    expect(transitionChannelAdmin(required, { type: 'loadStarted', generation: 3 }))
      .toEqual({ state: 'loading', generation: 3 })
  })
})
