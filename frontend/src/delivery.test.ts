import { describe, expect, test } from 'vitest'

import { parseDeliveryResult, parseErrorResponse, parsePreviewResponse } from './deliveryDto'
import { initialDeliveryState, transitionDelivery } from './deliveryState'

describe('delivery DTO', () => {
  // テストケース: statusごとの必須値を持つ成功応答と欠落応答を判定する。
  // 期待値: 完全な応答だけ受理し、欠落はprotocol errorになる。
  test('validates required fields for each status', () => {
    expect(parseDeliveryResult({ status: 'processing', operationId: crypto.randomUUID(), acceptedAt: 'a', expiresAt: 'e' }).ok).toBe(true)
    expect(parseDeliveryResult({ status: 'processing', operationId: crypto.randomUUID(), acceptedAt: 'a' })).toEqual({ ok: false, error: { code: 'protocol_error', summary: '応答形式を確認できません。' } })
    expect(parsePreviewResponse({ formattedText: 'text', confirmationToken: 'token' }).ok).toBe(true)
    expect(parseErrorResponse({ error: { code: 'invalid_input', summary: '入力を確認してください。', fields: { subject: ['件名は必須です。'] } } }).ok).toBe(true)
    expect(parseErrorResponse({ error: { code: 'invalid_input', summary: '入力を確認してください。', fields: { subject: 'invalid' } } }).ok).toBe(false)
  })

  // テストケース: 宛先や秘密値を含む未知の応答を判定する。
  // 期待値: 未知shapeを信頼せずprotocol errorになる。
  test('rejects unknown response shapes', () => {
    expect(parseDeliveryResult({ status: 'succeeded', target: 'secret' }).ok).toBe(false)
  })
})

describe('delivery state', () => {
  // テストケース: preview後に入力を編集する。
  // 期待値: 入力は保持し、preview・token・operation IDを廃棄したeditingに戻る。
  test('editing invalidates preview', () => {
    const preview = transitionDelivery(initialDeliveryState, { type: 'previewed', subject: 's', body: 'b', formattedText: 'f', confirmationToken: 't' })
    expect(transitionDelivery(preview, { type: 'edited', subject: 'new', body: 'b' })).toEqual({ phase: 'editing', subject: 'new', body: 'b', errors: {} })
  })

  // テストケース: 確認済み状態から二重submitする。
  // 期待値: 操作IDは最初の1回だけ生成され、submitting中の追加submitは無視される。
  test('creates operation id once and rejects duplicate submit', () => {
    const preview = transitionDelivery(initialDeliveryState, { type: 'previewed', subject: 's', body: 'b', formattedText: 'f', confirmationToken: 't' })
    const submitting = transitionDelivery(preview, { type: 'submitted', operationId: 'id-1' })
    expect(transitionDelivery(submitting, { type: 'submitted', operationId: 'id-2' })).toBe(submitting)
  })

  // テストケース: submittingからprocessingへ遷移し、状態確認中に追加操作する。
  // 期待値: 受付・期限日時と同一IDを保持し、checking中のsubmitを拒否する。
  test('keeps operation while processing and checking', () => {
    const preview = transitionDelivery(initialDeliveryState, { type: 'previewed', subject: 's', body: 'b', formattedText: 'f', confirmationToken: 't' })
    const submitting = transitionDelivery(preview, { type: 'submitted', operationId: 'id-1' })
    const processing = transitionDelivery(submitting, { type: 'processing', result: { status: 'processing', operationId: 'id-1', acceptedAt: 'a', expiresAt: 'e' } })
    expect(processing.phase).toBe('processing')
    const checking = transitionDelivery(processing, { type: 'checkStarted' })
    expect(checking.phase).toBe('checking')
    expect(transitionDelivery(checking, { type: 'submitted', operationId: 'id-2' })).toBe(checking)
    expect(transitionDelivery(checking, { type: 'processing', result: { status: 'processing', operationId: 'other', acceptedAt: 'a', expiresAt: 'e' } })).toBe(checking)
  })

  // テストケース: status 404と別operationのterminal結果を不正な遷移元へ適用する。
  // 期待値: checking以外の404とoperation ID不一致の結果は状態を変更しない。
  test('rejects illegal status and mismatched operation transitions', () => {
    const preview = transitionDelivery(initialDeliveryState, { type: 'previewed', subject: 's', body: 'b', formattedText: 'f', confirmationToken: 't' })
    const submitting = transitionDelivery(preview, { type: 'submitted', operationId: 'id-1' })
    expect(transitionDelivery(submitting, { type: 'statusMissing' })).toBe(submitting)
    expect(transitionDelivery(submitting, { type: 'succeeded', result: { status: 'succeeded', operationId: 'other', acceptedAt: 'a', completedAt: 'c', lineRequestId: null } })).toBe(submitting)
  })

  // テストケース: network error後にstatus 404を確認し、terminal後に新規配信を開始する。
  // 期待値: 同一IDの再試行は404後だけ許可され、新規配信は空入力になる。
  test('allows same-operation retry only after 404 and clears terminal operation', () => {
    const preview = transitionDelivery(initialDeliveryState, { type: 'previewed', subject: 's', body: 'b', formattedText: 'f', confirmationToken: 't' })
    const submitting = transitionDelivery(preview, { type: 'submitted', operationId: 'id-1' })
    const uncertain = transitionDelivery(submitting, { type: 'networkFailed' })
    expect(uncertain.phase === 'uncertain' && uncertain.canRetrySameOperation).toBe(false)
    const checking = transitionDelivery(uncertain, { type: 'checkStarted' })
    const missing = transitionDelivery(checking, { type: 'statusMissing' })
    expect(missing.phase === 'uncertain' && missing.canRetrySameOperation).toBe(true)
    const failed = transitionDelivery(missing, { type: 'failed', result: { status: 'failed', operationId: 'id-1', acceptedAt: 'a', completedAt: 'c', error: { code: 'conflict', summary: 'x' }, lineRequestId: null } })
    expect(transitionDelivery(failed, { type: 'newDelivery' })).toEqual(initialDeliveryState)
  })
})
