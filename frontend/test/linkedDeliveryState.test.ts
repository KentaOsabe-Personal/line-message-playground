import { describe, expect, test } from 'vitest'

import type { LinkedDeliveryStatus, LinkedPreviewResponse, SafeError } from '../src/deliveryDto'
import {
  initialLinkedDeliveryState,
  transitionLinkedDelivery,
} from '../src/deliveryState'

const channelOne = '11111111-1111-4111-8111-111111111111'
const channelTwo = '22222222-2222-4222-8222-222222222222'
const recipientOne = '33333333-3333-4333-8333-333333333333'
const operationOne = '44444444-4444-4444-8444-444444444444'

const previewResponse = (overrides: Partial<LinkedPreviewResponse> = {}): LinkedPreviewResponse => ({
  channelId: channelOne,
  channelLabel: '通知チャネル',
  recipientId: recipientOne,
  recipientDisplayName: '受信者',
  friendshipState: 'friend',
  formattedText: '【件名】\n\n本文',
  receiptRequested: false,
  receiptExpiresAt: null,
  confirmationToken: 'confirmation',
  ...overrides,
})

const editingWithTarget = () => {
  let state = transitionLinkedDelivery(initialLinkedDeliveryState, {
    type: 'channelChanged',
    channelId: channelOne,
  })
  state = transitionLinkedDelivery(state, { type: 'recipientChanged', recipientId: recipientOne })
  state = transitionLinkedDelivery(state, { type: 'subjectChanged', subject: '件名' })
  return transitionLinkedDelivery(state, { type: 'bodyChanged', body: '本文' })
}

const confirmed = () => {
  const previewing = transitionLinkedDelivery(editingWithTarget(), {
    type: 'previewStarted',
    requestId: 'preview-1',
  })
  return transitionLinkedDelivery(previewing, {
    type: 'previewSucceeded',
    requestId: 'preview-1',
    preview: previewResponse(),
  })
}

const submitted = () => transitionLinkedDelivery(confirmed(), {
  type: 'submitted',
  operationId: operationOne,
})

const status = (
  deliveryStatus: LinkedDeliveryStatus['status'],
): LinkedDeliveryStatus => {
  const common = {
    operationId: operationOne,
    snapshot: {
      channelId: channelOne,
      channelLabel: '通知チャネル',
      recipientId: recipientOne,
      channelActive: true,
      recipientEnabled: true,
      friendshipState: 'friend' as const,
    },
    acceptedAt: '2026-07-26T10:00:00+09:00',
    lineRequestId: null,
    receipt: {
      requested: false,
      status: 'not_requested' as const,
      expiresAt: null,
      confirmedAt: null,
    },
  }
  if (deliveryStatus === 'processing') {
    return { ...common, status: deliveryStatus, completedAt: null }
  }
  if (deliveryStatus === 'succeeded') {
    return {
      ...common,
      status: deliveryStatus,
      completedAt: '2026-07-26T10:00:01+09:00',
    }
  }
  return {
    ...common,
    status: deliveryStatus,
    completedAt: '2026-07-26T10:00:01+09:00',
    error: {
      code: deliveryStatus === 'failed' ? 'invalid_request' : 'timeout_unknown',
      summary: deliveryStatus === 'failed' ? '送信できませんでした。' : '状態を確認してください。',
    },
  }
}

describe('linked delivery state', () => {
  // テストケース: channel選択後にrecipientを選び、channelを変更する。
  // 期待値: channel変更だけがrecipientを解除し、更新前のstate objectは変更しない。
  test('clears the downstream recipient immutably when channel changes', () => {
    const selected = editingWithTarget()
    const changed = transitionLinkedDelivery(selected, {
      type: 'channelChanged',
      channelId: channelTwo,
    })

    expect(changed).toEqual({
      phase: 'editing',
      input: {
        channelId: channelTwo,
        recipientId: null,
        subject: '件名',
        body: '本文',
        receiptRequested: false,
      },
      errors: {},
    })
    expect(selected.phase === 'editing' && selected.input.recipientId).toBe(recipientOne)
  })

  // テストケース: 確認後にtarget・件名・本文・受取確認の各軸を個別に変更する。
  // 期待値: どの変更もconfirmationを保持せず、変更済み入力だけを持つeditingへ戻る。
  test('invalidates confirmation for every editing axis', () => {
    const preview = confirmed()
    const events = [
      { type: 'channelChanged', channelId: channelTwo } as const,
      { type: 'recipientChanged', recipientId: null } as const,
      { type: 'subjectChanged', subject: '別件名' } as const,
      { type: 'bodyChanged', body: '別本文' } as const,
      { type: 'receiptChanged', receiptRequested: true } as const,
    ]

    for (const event of events) {
      const changed = transitionLinkedDelivery(preview, event)
      expect(changed.phase).toBe('editing')
      expect('preview' in changed).toBe(false)
      expect('confirmationToken' in changed).toBe(false)
    }
  })

  // テストケース: previewから入力へ戻る。
  // 期待値: 5つの入力軸を変更せず、confirmationだけを破棄したeditingへ戻る。
  test('keeps all input when returning from preview', () => {
    const preview = confirmed()
    const editing = transitionLinkedDelivery(preview, { type: 'backToEditing' })

    expect(editing).toEqual({ phase: 'editing', input: preview.input, errors: {} })
    expect(editing).not.toBe(preview)
  })

  // テストケース: preview開始後に入力を変更し、遅れて旧requestの応答を受け取る。
  // 期待値: 入力変更でrequest identityを破棄し、stale responseを適用しない。
  test('ignores stale preview responses by request identity', () => {
    const previewing = transitionLinkedDelivery(editingWithTarget(), {
      type: 'previewStarted',
      requestId: 'preview-old',
    })
    const edited = transitionLinkedDelivery(previewing, { type: 'bodyChanged', body: '更新後' })
    const stale = transitionLinkedDelivery(edited, {
      type: 'previewSucceeded',
      requestId: 'preview-old',
      preview: previewResponse(),
    })

    expect(stale).toBe(edited)
    expect(stale.phase === 'editing' && stale.input.body).toBe('更新後')
  })

  // テストケース: 現在のrequest IDに対して別targetを示すpreview応答を受け取る。
  // 期待値: confirmationを保存せず、安全な再確認案内を持つeditingへ戻る。
  test('rejects a preview that does not match the selected target', () => {
    const previewing = transitionLinkedDelivery(editingWithTarget(), {
      type: 'previewStarted',
      requestId: 'preview-1',
    })
    const mismatched = transitionLinkedDelivery(previewing, {
      type: 'previewSucceeded',
      requestId: 'preview-1',
      preview: previewResponse({ channelId: channelTwo }),
    })

    expect(mismatched).toEqual({
      phase: 'editing',
      input: previewing.input,
      errors: { message: '確認結果が選択内容と一致しません。もう一度確認してください。' },
    })
  })

  // テストケース: 同一previewでsubmitを連打し、別operationの配信結果を受け取る。
  // 期待値: 最初のoperationだけを保持し、追加submitと別operationの結果を無視する。
  test('suppresses double submit and stale delivery results', () => {
    const first = submitted()
    const duplicate = transitionLinkedDelivery(first, {
      type: 'submitted',
      operationId: crypto.randomUUID(),
    })
    const staleResult: LinkedDeliveryStatus = {
      operationId: crypto.randomUUID(),
      snapshot: {
        channelId: channelOne,
        channelLabel: '通知チャネル',
        recipientId: recipientOne,
        channelActive: true,
        recipientEnabled: true,
        friendshipState: 'friend',
      },
      status: 'succeeded',
      acceptedAt: '2026-07-26T10:00:00+09:00',
      completedAt: '2026-07-26T10:00:01+09:00',
      lineRequestId: null,
      receipt: {
        requested: false,
        status: 'not_requested',
        expiresAt: null,
        confirmedAt: null,
      },
    }

    expect(duplicate).toBe(first)
    expect(transitionLinkedDelivery(first, { type: 'deliveryUpdated', result: staleResult })).toBe(first)
  })

  // テストケース: submittingとprocessing中に5つの入力変更と追加submitを行う。
  // 期待値: 取り消せない外部作用の進行中は全編集と二重submitを同じstateのまま拒否する。
  test('disables editing and submit while an operation is active', () => {
    const submitting = submitted()
    const processing = transitionLinkedDelivery(submitting, {
      type: 'deliveryUpdated',
      result: status('processing'),
    })
    const editEvents = [
      { type: 'channelChanged', channelId: channelTwo } as const,
      { type: 'recipientChanged', recipientId: null } as const,
      { type: 'subjectChanged', subject: '変更' } as const,
      { type: 'bodyChanged', body: '変更' } as const,
      { type: 'receiptChanged', receiptRequested: true } as const,
    ]

    for (const state of [submitting, processing]) {
      for (const event of editEvents) expect(transitionLinkedDelivery(state, event)).toBe(state)
      expect(transitionLinkedDelivery(state, {
        type: 'submitted',
        operationId: crypto.randomUUID(),
      })).toBe(state)
    }
  })

  // テストケース: 同じ確認済み操作へprocessing・succeeded・failed・unknownの各応答を適用する。
  // 期待値: operation ID一致時だけ各閉じた配信状態へ決定的に遷移する。
  test('represents every linked delivery status deterministically', () => {
    for (const expected of ['processing', 'succeeded', 'failed', 'unknown'] as const) {
      const result = status(expected)
      const first = transitionLinkedDelivery(submitted(), { type: 'deliveryUpdated', result })
      const second = transitionLinkedDelivery(submitted(), { type: 'deliveryUpdated', result })

      expect(first.phase).toBe(expected)
      expect(second).toEqual(first)
      expect(first).not.toBe(second)
    }
  })

  // テストケース: sendの通信結果が曖昧になった後、同一operationのstatus結果を受け取る。
  // 期待値: 自動再送を許可せずstatus確認へ進み、deliveryとreceiptの直交状態を保存する。
  test('reconciles network ambiguity only through matching status', () => {
    const uncertain = transitionLinkedDelivery(submitted(), { type: 'networkFailed' })
    expect(uncertain.phase === 'uncertain' && uncertain.canRetrySameOperation).toBe(false)
    const checking = transitionLinkedDelivery(uncertain, { type: 'checkStarted' })
    const result: LinkedDeliveryStatus = {
      operationId: operationOne,
      snapshot: {
        channelId: channelOne,
        channelLabel: '通知チャネル',
        recipientId: recipientOne,
        channelActive: true,
        recipientEnabled: true,
        friendshipState: 'friend',
      },
      status: 'unknown',
      acceptedAt: '2026-07-26T10:00:00+09:00',
      completedAt: '2026-07-26T10:00:02+09:00',
      lineRequestId: null,
      receipt: {
        requested: true,
        status: 'confirmed',
        expiresAt: '2026-07-27T10:00:00+09:00',
        confirmedAt: '2026-07-26T10:00:01+09:00',
      },
      error: { code: 'timeout_unknown', summary: '状態を確認してください。' },
    }
    const reconciled = transitionLinkedDelivery(checking, { type: 'deliveryUpdated', result })

    expect(reconciled.phase).toBe('unknown')
    expect(reconciled.phase === 'unknown' && reconciled.result.receipt.status).toBe('confirmed')
  })

  // テストケース: succeededの受取確認を再照会中に通信失敗する。
  // 期待値: 既に確定したLINE受付結果を失わず、送信や状態を推測し直さない。
  test('preserves a succeeded result when receipt refresh fails', () => {
    const succeeded = transitionLinkedDelivery(submitted(), {
      type: 'deliveryUpdated',
      result: status('succeeded'),
    })
    const checking = transitionLinkedDelivery(succeeded, { type: 'checkStarted' })
    const restored = transitionLinkedDelivery(checking, { type: 'networkFailed' })

    expect(restored).toEqual(succeeded)
  })

  // テストケース: preview APIが安全なfield errorを返し、呼出元のerror objectを後で変更する。
  // 期待値: reducerは公開fieldだけのcopyを保持し、生errorや後続mutationをstateへ混入させない。
  test('copies safe preview errors into editing state', () => {
    const error: SafeError = {
      code: 'validation_error',
      summary: '入力を確認してください。',
      fields: { subject: ['件名は必須です。'] },
    }
    const previewing = transitionLinkedDelivery(editingWithTarget(), {
      type: 'previewStarted',
      requestId: 'preview-1',
    })
    const rejected = transitionLinkedDelivery(previewing, {
      type: 'previewRejected',
      requestId: 'preview-1',
      error,
    })
    error.fields!.subject![0] = 'secret canary'

    expect(rejected).toEqual({
      phase: 'editing',
      input: previewing.input,
      errors: { subject: '件名は必須です。', message: '入力を確認してください。' },
    })
  })
})
