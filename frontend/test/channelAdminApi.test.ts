import { describe, expect, test, vi } from 'vitest'

import { ChannelAdminApiError, createChannelAdminApiClient } from '../src/channelAdminApi'
import { createProtectedHttpClient } from '../src/httpApi'
import type { ProtectedHttpClient } from '../src/httpApi'

const channelId = '11111111-1111-4111-8111-111111111111'
const channel = {
  channelId,
  label: '通知チャネル',
  messagingApiChannelId: '1234567890',
  botUserId: 'U1234567890abcdef1234567890abcdef',
  providerId: '9876543210',
  active: true,
  credentialsState: 'configured',
  credentialsUpdatedAt: '2026-08-01T10:00:00+09:00',
  createdAt: '2026-07-01T10:00:00Z',
  updatedAt: '2026-08-01T10:00:00+09:00',
  webhookUrl: `https://example.test/api/line/webhooks/${channelId}/`,
}

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json' },
})

describe('channel admin API', () => {
  // テストケース: 7種類の管理操作を各一回実行する。
  // 期待値: 正しい相対path/method/bodyへ写像し、requestを自動再試行しない。
  test('maps every operation to exactly one protected request', async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(response({ items: [channel] }))
      .mockResolvedValueOnce(response(channel))
      .mockResolvedValueOnce(response(channel, 201))
      .mockResolvedValueOnce(response(channel))
      .mockResolvedValueOnce(response(channel))
      .mockResolvedValueOnce(response({ channelId, label: channel.label, deleted: true }))
      .mockResolvedValueOnce(response({
        channelId,
        status: 'connected',
        checkedAt: '2026-08-01T10:01:00Z',
        scope: 'access_token_and_bot_identity_only',
      }))
    const client = createChannelAdminApiClient({ request } as ProtectedHttpClient)

    await client.listChannels()
    await client.getChannel(channelId)
    await client.register({
      label: channel.label,
      messagingApiChannelId: channel.messagingApiChannelId,
      botUserId: channel.botUserId,
      providerId: channel.providerId,
      accessToken: 'access-secret',
      channelSecret: 'channel-secret',
      active: true,
    })
    await client.update(channelId, { expectedUpdatedAt: channel.updatedAt, label: '更新後' })
    await client.setState(channelId, { expectedUpdatedAt: channel.updatedAt, active: false })
    await client.delete(channelId, channel.updatedAt)
    await client.checkConnection(channelId)

    expect(request).toHaveBeenCalledTimes(7)
    expect(request.mock.calls.map(([input]) => input)).toEqual([
      { path: '/api/line/channels/', method: 'GET' },
      { path: `/api/line/channels/${channelId}/`, method: 'GET' },
      { path: '/api/line/channels/', method: 'POST', body: expect.objectContaining({ accessToken: 'access-secret' }) },
      { path: `/api/line/channels/${channelId}/`, method: 'PATCH', body: { expectedUpdatedAt: channel.updatedAt, label: '更新後' } },
      { path: `/api/line/channels/${channelId}/state/`, method: 'POST', body: { expectedUpdatedAt: channel.updatedAt, active: false } },
      { path: `/api/line/channels/${channelId}/`, method: 'DELETE', body: { expectedUpdatedAt: channel.updatedAt } },
      { path: `/api/line/channels/${channelId}/connection-check/`, method: 'POST', body: {} },
    ])
  })

  // テストケース: 秘密値を含むmutationがnetwork errorになる。
  // 期待値: client errorは安全分類だけを持ち、request payloadや秘密値を保持しない。
  test('does not retain secret request values in client errors', async () => {
    const secret = 'must-not-survive'
    const http = { request: vi.fn().mockRejectedValue(new Error(secret)) } as unknown as ProtectedHttpClient
    const client = createChannelAdminApiClient(http)

    let caught: unknown
    try {
      await client.register({
        label: '通知', messagingApiChannelId: '123', botUserId: 'U123', providerId: '456',
        accessToken: secret, channelSecret: secret, active: true,
      })
    } catch (error) {
      caught = error
    }
    expect(caught).toBeInstanceOf(ChannelAdminApiError)
    expect(caught).toMatchObject({ error: { code: 'network_error' } })
    expect(JSON.stringify(caught)).not.toContain(secret)
    expect(String(caught)).not.toContain(secret)
    expect(http.request).toHaveBeenCalledTimes(1)
  })

  // テストケース: safe API errorと不正な成功応答を受け取る。
  // 期待値: 前者を許可済み分類、後者をprotocol_errorとして返す。
  test('distinguishes safe API and protocol errors', async () => {
    const http = { request: vi.fn()
      .mockResolvedValueOnce(response({ error: { code: 'stale_channel', summary: '再取得してください。' } }, 409))
      .mockResolvedValueOnce(response({ ...channel, channelSecret: 'leak' })) } as unknown as ProtectedHttpClient
    const client = createChannelAdminApiClient(http)

    await expect(client.getChannel(channelId)).rejects.toMatchObject({
      error: { code: 'stale_channel' }, httpStatus: 409,
    })
    await expect(client.getChannel(channelId)).rejects.toMatchObject({
      error: { code: 'protocol_error' }, httpStatus: 200,
    })
    expect(http.request).toHaveBeenCalledTimes(2)
  })

  // テストケース: 実ProtectedHttpClient経由の管理requestが401を受け取る。
  // 期待値: safe authentication errorを返し、既存session invalid callbackを一回呼ぶ。
  test('connects 401 responses to the existing session invalid callback', async () => {
    const onSessionInvalid = vi.fn()
    const fetchRequest = vi.fn().mockResolvedValue(response({
      error: { code: 'authentication_required', summary: '再認証してください。' },
    }, 401))
    const client = createChannelAdminApiClient(createProtectedHttpClient({
      fetch: fetchRequest,
      onSessionInvalid,
    }))

    await expect(client.listChannels()).rejects.toMatchObject({
      error: { code: 'authentication_required' },
      httpStatus: 401,
    })
    expect(fetchRequest).toHaveBeenCalledTimes(1)
    expect(onSessionInvalid).toHaveBeenCalledTimes(1)
  })
})
