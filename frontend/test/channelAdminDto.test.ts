import { describe, expect, test } from 'vitest'

import {
  parseChannelAdminItem,
  parseChannelAdminList,
  parseConnectionCheck,
  parseDeletedChannel,
} from '../src/channelAdminDto'

const channelId = '11111111-1111-4111-8111-111111111111'

const item = () => ({
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
})

describe('channel admin DTO', () => {
  // テストケース: exactな安全チャネルDTOとその一覧を解析する。
  // 期待値: 非秘密フィールドだけを持つ型付き値として受理する。
  test('accepts exact safe channel values and list envelopes', () => {
    expect(parseChannelAdminItem(item())).toEqual({ ok: true, value: item() })
    expect(parseChannelAdminList({ items: [item()] })).toEqual({ ok: true, value: [item()] })
  })

  // テストケース: secret様field、余分なfield、別channelのWebhook URLを解析する。
  // 期待値: いずれもprotocol_errorとして拒否する。
  test('rejects secret-like, extra, and mismatched webhook fields', () => {
    for (const invalid of [
      { ...item(), accessToken: 'secret' },
      { ...item(), note: 'extra' },
      { ...item(), webhookUrl: 'https://example.test/api/line/webhooks/22222222-2222-4222-8222-222222222222/' },
    ]) {
      expect(parseChannelAdminItem(invalid)).toMatchObject({ ok: false, error: { code: 'protocol_error' } })
    }
  })

  // テストケース: 非canonical UUID、naive/実在しない日時、HTTP URL、無効enumを解析する。
  // 期待値: 境界検証で全て拒否する。
  test('rejects malformed identifiers, datetimes, URLs, and enums', () => {
    const invalidValues = [
      { ...item(), channelId: 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA' },
      { ...item(), updatedAt: '2026-08-01T10:00:00' },
      { ...item(), createdAt: '2026-02-30T10:00:00Z' },
      { ...item(), webhookUrl: `http://example.test/api/line/webhooks/${channelId}/` },
      { ...item(), credentialsState: 'unknown' },
    ]
    for (const invalid of invalidValues) {
      expect(parseChannelAdminItem(invalid)).toMatchObject({ ok: false, error: { code: 'protocol_error' } })
    }
  })

  // テストケース: 削除結果と限定scopeの接続確認結果を解析する。
  // 期待値: exactな非秘密結果だけを受理し、余分な生応答を拒否する。
  test('validates delete and connection results exactly', () => {
    expect(parseDeletedChannel({ channelId, label: '通知チャネル', deleted: true })).toMatchObject({ ok: true })
    expect(parseConnectionCheck({
      channelId,
      status: 'connected',
      checkedAt: '2026-08-01T10:01:00+09:00',
      scope: 'access_token_and_bot_identity_only',
    })).toMatchObject({ ok: true })
    expect(parseConnectionCheck({
      channelId,
      status: 'connected',
      checkedAt: '2026-08-01T10:01:00+09:00',
      scope: 'access_token_and_bot_identity_only',
      rawResponse: {},
    })).toMatchObject({ ok: false, error: { code: 'protocol_error' } })
  })
})
