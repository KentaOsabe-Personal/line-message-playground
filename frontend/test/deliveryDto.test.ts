import { describe, expect, test } from 'vitest'

import {
  parseDeliveryChannelChoices,
  parseDeliveryRecipientChoices,
  parseDeliveryStatus,
  parseErrorResponse,
  parseLinkedPreviewResponse,
} from '../src/deliveryDto'

const channelId = '123e4567-e89b-12d3-a456-426614174000'
const recipientId = '223e4567-e89b-12d3-a456-426614174000'
const operationId = '323e4567-e89b-12d3-a456-426614174000'
const protocolError = { ok: false, error: { code: 'protocol_error', summary: '応答形式を確認できません。' } }

const channelResponse = {
  items: [{
    channelId,
    label: '通知チャネル',
    active: true,
    deliveryAvailable: true,
    unavailableReason: null,
  }],
}

const recipientResponse = {
  items: [{
    recipientId,
    displayName: '受信者',
    enabled: true,
    friendshipState: 'friend',
    deliveryAvailable: true,
    unavailableReason: null,
  }],
}

const previewResponse = {
  channelId,
  channelLabel: '通知チャネル',
  recipientId,
  recipientDisplayName: '受信者',
  friendshipState: 'friend',
  formattedText: '【件名】\n\n本文',
  receiptRequested: true,
  receiptExpiresAt: '2026-07-27T00:00:00+00:00',
  confirmationToken: 'opaque-confirmation',
}

const linkedStatus = {
  operationId,
  snapshot: {
    channelId,
    channelLabel: '通知チャネル',
    recipientId,
    channelActive: true,
    recipientEnabled: true,
    friendshipState: 'friend',
  },
  status: 'succeeded',
  acceptedAt: '2026-07-26T00:00:00+00:00',
  completedAt: '2026-07-26T00:00:01+00:00',
  lineRequestId: 'safe-request-id',
  receipt: {
    requested: true,
    status: 'pending',
    expiresAt: '2026-07-27T00:00:00+00:00',
    confirmedAt: null,
  },
}

describe('linked delivery DTO', () => {
  // テストケース: channel・recipient一覧の公開DTOをruntimeで検証する。
  // 期待値: exact key、canonical UUID、closed enumを満たす一覧だけを受理する。
  test('parses strict channel and recipient choice envelopes', () => {
    expect(parseDeliveryChannelChoices(channelResponse)).toEqual({ ok: true, value: channelResponse.items })
    expect(parseDeliveryRecipientChoices(recipientResponse)).toEqual({ ok: true, value: recipientResponse.items })
    expect(parseDeliveryChannelChoices({ items: [{ ...channelResponse.items[0], channelId: '00000000-0000-0000-0000-000000000000' }] }).ok).toBe(true)
    expect(parseDeliveryRecipientChoices({ items: [{ ...recipientResponse.items[0], recipientId: 'ffffffff-ffff-ffff-ffff-ffffffffffff' }] }).ok).toBe(true)
    expect(parseDeliveryChannelChoices({ ...channelResponse, accessToken: 'token-canary' })).toEqual(protocolError)
    expect(parseDeliveryChannelChoices({ items: [{ ...channelResponse.items[0], channelId: channelId.toUpperCase() }] })).toEqual(protocolError)
    expect(parseDeliveryRecipientChoices({ items: [{ ...recipientResponse.items[0], friendshipState: 'blocked' }] })).toEqual(protocolError)
    expect(parseDeliveryRecipientChoices({ items: [{ ...recipientResponse.items[0], lineUserId: 'U-secret-canary' }] })).toEqual(protocolError)
  })

  // テストケース: recipientのenabled・friendship・配信可否・理由の全状態を検証する。
  // 期待値: Backendの理由優先順位と整合する組合せだけを受理し、矛盾したsummaryを拒否する。
  test('validates every recipient availability combination', () => {
    const accepted = [
      { enabled: true, friendshipState: 'friend', deliveryAvailable: true, unavailableReason: null },
      { enabled: false, friendshipState: 'friend', deliveryAvailable: false, unavailableReason: 'recipient_disabled' },
      { enabled: false, friendshipState: 'not_friend', deliveryAvailable: false, unavailableReason: 'recipient_disabled' },
      { enabled: true, friendshipState: 'not_friend', deliveryAvailable: false, unavailableReason: 'not_friend' },
      { enabled: true, friendshipState: 'unknown', deliveryAvailable: false, unavailableReason: 'friendship_unknown' },
      { enabled: true, friendshipState: 'friend', deliveryAvailable: false, unavailableReason: 'channel_inactive' },
      { enabled: false, friendshipState: 'unknown', deliveryAvailable: false, unavailableReason: 'channel_inactive' },
    ]
    for (const state of accepted) {
      expect(parseDeliveryRecipientChoices({ items: [{ ...recipientResponse.items[0], ...state }] }).ok).toBe(true)
    }
    const rejected = [
      { enabled: false, friendshipState: 'friend', deliveryAvailable: true, unavailableReason: null },
      { enabled: true, friendshipState: 'friend', deliveryAvailable: false, unavailableReason: 'recipient_disabled' },
      { enabled: true, friendshipState: 'unknown', deliveryAvailable: false, unavailableReason: 'not_friend' },
      { enabled: true, friendshipState: 'not_friend', deliveryAvailable: false, unavailableReason: 'friendship_unknown' },
      { enabled: true, friendshipState: 'friend', deliveryAvailable: false, unavailableReason: null },
    ]
    for (const state of rejected) {
      expect(parseDeliveryRecipientChoices({ items: [{ ...recipientResponse.items[0], ...state }] })).toEqual(protocolError)
    }
  })

  // テストケース: linked previewのreceipt有無と公開summaryを検証する。
  // 期待値: nullable expiryを含む完全なshapeだけを受理し、欠落・型違い・秘密fieldを拒否する。
  test('parses strict linked preview responses', () => {
    expect(parseLinkedPreviewResponse(previewResponse)).toEqual({ ok: true, value: previewResponse })
    expect(parseLinkedPreviewResponse({ ...previewResponse, receiptRequested: false, receiptExpiresAt: null }).ok).toBe(true)
    expect(parseLinkedPreviewResponse({ ...previewResponse, receiptExpiresAt: 123 })).toEqual(protocolError)
    expect(parseLinkedPreviewResponse({ ...previewResponse, receiptExpiresAt: '2026-02-30T00:00:00+00:00' })).toEqual(protocolError)
    expect(parseLinkedPreviewResponse({ ...previewResponse, receiptExpiresAt: '2026-07-27' })).toEqual(protocolError)
    expect(parseLinkedPreviewResponse({ ...previewResponse, receiptExpiresAt: '0000-01-01T00:00:00+00:00' })).toEqual(protocolError)
    expect(parseLinkedPreviewResponse({ ...previewResponse, receiptExpiresAt: '10000-01-01T00:00:00+00:00' })).toEqual(protocolError)
    const { channelLabel: _missing, ...missing } = previewResponse
    expect(parseLinkedPreviewResponse(missing)).toEqual(protocolError)
    expect(parseLinkedPreviewResponse({ ...previewResponse, channelSecret: 'secret-canary' })).toEqual(protocolError)
  })

  // テストケース: linked delivery状態とreceipt状態を独立したclosed unionとして検証する。
  // 期待値: deliveryとreceiptの全状態軸を直交して受理し、unknown enum・余剰field・不正UUIDを拒否する。
  test('parses delivery and receipt axes orthogonally', () => {
    const deliveryStates = ['processing', 'succeeded', 'failed', 'unknown'] as const
    const receiptStates = ['not_requested', 'pending', 'confirmed', 'expired'] as const
    for (const status of deliveryStates) {
      for (const receiptStatus of receiptStates) {
        const value = {
          ...linkedStatus,
          status,
          completedAt: status === 'processing' ? null : linkedStatus.completedAt,
          lineRequestId: status === 'processing' ? null : linkedStatus.lineRequestId,
          ...(status === 'failed' || status === 'unknown'
            ? { error: { code: status === 'failed' ? 'permission' : 'service_unknown', summary: '安全な要約' } }
            : {}),
          receipt: {
            requested: receiptStatus !== 'not_requested',
            status: receiptStatus,
            expiresAt: receiptStatus === 'not_requested' ? null : linkedStatus.receipt.expiresAt,
            confirmedAt: receiptStatus === 'confirmed' ? linkedStatus.completedAt : null,
          },
        }
        expect(parseDeliveryStatus(value).ok).toBe(true)
      }
    }
    expect(parseDeliveryStatus({ ...linkedStatus, status: 'delivered' })).toEqual(protocolError)
    expect(parseDeliveryStatus({ ...linkedStatus, operationId: operationId.toUpperCase() })).toEqual(protocolError)
    expect(parseDeliveryStatus({ ...linkedStatus, acceptedAt: '2026-02-30T00:00:00+00:00' })).toEqual(protocolError)
    expect(parseDeliveryStatus({ ...linkedStatus, acceptedAt: '2026-07-26' })).toEqual(protocolError)
    expect(parseDeliveryStatus({ ...linkedStatus, status: 'failed', error: { code: 'service_unknown', summary: 'safe' } })).toEqual(protocolError)
    expect(parseDeliveryStatus({ ...linkedStatus, status: 'unknown', error: { code: 'permission', summary: 'safe' } })).toEqual(protocolError)
    expect(parseDeliveryStatus({ ...linkedStatus, receipt: { ...linkedStatus.receipt, capability: 'receipt-secret-canary' } })).toEqual(protocolError)
    expect(parseDeliveryStatus({ ...linkedStatus, snapshot: { ...linkedStatus.snapshot, displayName: 'PII canary' } })).toEqual(protocolError)
  })

  // テストケース: Backendのsafe error envelopeを検証する。
  // 期待値: 公開fieldだけの文字列配列を受理し、秘密field・unknown key・wrong typeをprotocol errorにする。
  test('parses only safe exact error envelopes', () => {
    expect(parseErrorResponse({
      error: {
        code: 'validation_error',
        summary: '入力内容を確認してください。',
        fields: { channelId: ['入力値が不正です。'], receiptRequested: ['入力値が不正です。'] },
      },
    }).ok).toBe(true)
    expect(parseErrorResponse({ error: { code: 'owner_operation_blocked', summary: 'この操作は現在利用できません。' } }).ok).toBe(true)
    expect(parseErrorResponse({ error: { code: 'not_a_safe_code', summary: 'safe' } })).toEqual(protocolError)
    expect(parseErrorResponse({ error: { code: 'x', summary: 'safe', accessToken: 'token-canary' } })).toEqual(protocolError)
    expect(parseErrorResponse({ error: { code: 'x', summary: 'safe', fields: { channelSecret: ['secret-canary'] } } })).toEqual(protocolError)
    expect(parseErrorResponse({ error: { code: 'x', summary: 'safe', fields: { subject: 'wrong' } } })).toEqual(protocolError)
  })

  // テストケース: error envelopeとterminal statusをparseした後に元payloadを変更する。
  // 期待値: code・summary・fields・message配列を防御的に再構築し、画面へ渡したDTOは変化しない。
  test('defensively copies safe errors from untrusted payloads', () => {
    const envelope = {
      error: {
        code: 'validation_error',
        summary: '入力内容を確認してください。',
        fields: { subject: ['入力値が不正です。'] },
      },
    }
    const parsedEnvelope = parseErrorResponse(envelope)
    expect(parsedEnvelope.ok).toBe(true)
    envelope.error.code = 'unexpected'
    envelope.error.summary = 'mutated'
    envelope.error.fields.subject[0] = 'mutated'
    expect(parsedEnvelope).toEqual({
      ok: true,
      value: {
        code: 'validation_error',
        summary: '入力内容を確認してください。',
        fields: { subject: ['入力値が不正です。'] },
      },
    })

    const failedPayload = {
      ...linkedStatus,
      status: 'failed',
      error: {
        code: 'permission',
        summary: 'LINEチャネルの権限を確認してください。',
        fields: { message: ['安全な要約です。'] },
      },
    }
    const parsedStatus = parseDeliveryStatus(failedPayload)
    expect(parsedStatus.ok).toBe(true)
    failedPayload.error.code = 'unexpected'
    failedPayload.error.summary = 'mutated'
    failedPayload.error.fields.message.push('mutated')
    expect(parsedStatus.ok && parsedStatus.value.status === 'failed' && parsedStatus.value.error).toEqual({
      code: 'permission',
      summary: 'LINEチャネルの権限を確認してください。',
      fields: { message: ['安全な要約です。'] },
    })

    const unknownPayload = {
      ...linkedStatus,
      status: 'unknown',
      error: { code: 'service_unknown', summary: 'LINE側の状態を確認してください。' },
    }
    const parsedUnknown = parseDeliveryStatus(unknownPayload)
    expect(parsedUnknown.ok).toBe(true)
    unknownPayload.error.code = 'response_unknown'
    unknownPayload.error.summary = 'mutated'
    expect(parsedUnknown.ok && parsedUnknown.value.status === 'unknown' && parsedUnknown.value.error).toEqual({
      code: 'service_unknown',
      summary: 'LINE側の状態を確認してください。',
    })
  })
})
