import { describe, expect, test } from 'vitest'

import {
  parseChannelList,
  parseChannelLink,
  parseUnlinkExecution,
  parseUnlinkPreview,
} from '../src/accountDto'

const channelId = '2c42a18e-2f3d-4dcb-8f13-3cead52af738'
const recipientId = '82e59ae7-b3f7-4298-b9b1-93d15bd42dc6'

describe('account DTO', () => {
  // テストケース: Backendのchannel projectionをstrict DTOへ変換する。
  // 期待値: opaque IDと安全な状態だけを受理し、未知fieldや矛盾した状態を拒否する。
  test('parses only exact and internally consistent channel projections', () => {
    expect(parseChannelLink({
      channelId,
      channelLabel: '通知チャネル',
      channelState: 'active',
      linkState: 'linked_enabled',
      friendshipState: 'friend',
      deliveryAvailable: true,
      recipientId,
    })).toEqual({ ok: true, value: {
      channelId,
      channelLabel: '通知チャネル',
      channelState: 'active',
      linkState: 'linked_enabled',
      friendshipState: 'friend',
      deliveryAvailable: true,
      recipientId,
    } })
    expect(parseChannelLink({
      channelId,
      channelLabel: '通知チャネル',
      channelState: 'active',
      linkState: 'unlinked',
      friendshipState: 'unknown',
      deliveryAvailable: false,
      recipientId,
    }).ok).toBe(false)
    expect(parseChannelList({ items: [], userId: 'forbidden' }).ok).toBe(false)
  })

  // テストケース: 全連携解除previewと実行結果を検証する。
  // 期待値: timezone付き期限とstage別retry actionだけを受理し、曖昧なpendingを拒否する。
  test('parses unlink preview and safe execution unions strictly', () => {
    expect(parseUnlinkPreview({
      displayName: 'Owner',
      recipientCount: 1,
      channelLabels: ['通知チャネル'],
      deliveryAuditRetained: true,
      confirmationToken: 'opaque-confirmation',
      expiresAt: '2026-07-20T12:00:00+09:00',
    }).ok).toBe(true)
    expect(parseUnlinkPreview({
      displayName: 'Owner',
      recipientCount: 1,
      channelLabels: ['通知チャネル'],
      deliveryAuditRetained: true,
      confirmationToken: 'opaque-confirmation',
      expiresAt: '2026-07-20T12:00:00',
    }).ok).toBe(false)
    expect(parseUnlinkExecution({ state: 'completed' })).toEqual({ ok: true, value: { state: 'completed' } })
    expect(parseUnlinkExecution({
      state: 'pending',
      stage: 'deauthorization_pending',
      retryAction: 'retry_local_delete',
    }).ok).toBe(false)
  })
})
