import { describe, expect, test, vi } from 'vitest'

import { createAccountApiClient } from '../src/accountApi'
import type { ProtectedHttpClient } from '../src/httpApi'

const channelId = '2c42a18e-2f3d-4dcb-8f13-3cead52af738'
const recipientId = '82e59ae7-b3f7-4298-b9b1-93d15bd42dc6'
const channel = {
  channelId,
  channelLabel: '通知チャネル',
  channelState: 'active',
  linkState: 'unlinked',
  friendshipState: 'unknown',
  deliveryAvailable: false,
  recipientId: null,
}

describe('account API client', () => {
  // テストケース: recipient操作を共通HTTP clientへ渡す。
  // 期待値: opaque IDとwrite-only tokenだけを正しいpath/bodyで送信する。
  test('uses protected HTTP paths and minimal request bodies', async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [channel] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...channel, linkState: 'linked_enabled', recipientId }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...channel, linkState: 'linked_disabled', recipientId }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    const client = createAccountApiClient({ request } as ProtectedHttpClient)

    await client.listChannels()
    await client.registerRecipient(channelId, 'fresh-token')
    await client.setRecipientEnabled(recipientId, false)
    await client.unlinkRecipient(recipientId)

    expect(request.mock.calls).toEqual([
      [{ path: '/api/account/channels/', method: 'GET' }],
      [{ path: '/api/account/recipients/', method: 'POST', body: { channelId, accessToken: 'fresh-token' } }],
      [{ path: `/api/account/recipients/${recipientId}/`, method: 'PATCH', body: { enabled: false } }],
      [{ path: `/api/account/recipients/${recipientId}/`, method: 'DELETE' }],
    ])
  })

  // テストケース: Backendが未知fieldを含む成功応答を返す。
  // 期待値: 描画用dataへ渡さずprotocol errorに変換する。
  test('rejects malformed success responses', async () => {
    const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], subject: 'secret' }), { status: 200 }))
    const client = createAccountApiClient({ request } as ProtectedHttpClient)

    await expect(client.listChannels()).rejects.toMatchObject({
      error: { code: 'protocol_error' },
    })
  })

  // テストケース: unlink preview、初回実行、local retryを呼び分ける。
  // 期待値: confirmation/tokenは必要なrequestだけへ含める。
  test('sends stage-specific unlink requests without stale credentials', async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        displayName: 'Owner', recipientCount: 0, channelLabels: [], deliveryAuditRetained: true,
        confirmationToken: 'opaque', expiresAt: '2026-07-20T12:00:00+09:00',
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ state: 'completed' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        state: 'pending', stage: 'local_deletion_pending', retryAction: 'retry_local_delete',
      }), { status: 202 }))
    const client = createAccountApiClient({ request } as ProtectedHttpClient)

    await client.previewUnlink()
    await client.executeUnlink({ confirmationToken: 'opaque', userAccessToken: 'fresh-token' })
    await client.executeUnlink({})

    expect(request.mock.calls[0]).toEqual([{ path: '/api/account/unlink-preview/', method: 'POST' }])
    expect(request.mock.calls[1]).toEqual([{ path: '/api/account/unlink/', method: 'POST', body: { confirmationToken: 'opaque', userAccessToken: 'fresh-token' } }])
    expect(request.mock.calls[2]).toEqual([{ path: '/api/account/unlink/', method: 'POST', body: {} }])
  })
})
