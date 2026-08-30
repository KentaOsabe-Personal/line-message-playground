import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import { DeliveryApiError, type LinkedDeliveryApiClient } from '../src/deliveryApi'
import type { LinkedDeliveryStatus, LinkedPreviewResponse } from '../src/deliveryDto'
import DeliveryForm from '../src/DeliveryForm'
import { createOwnerSessionStorage } from '../src/ownerSessionStorage'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const channelId = '11111111-1111-4111-8111-111111111111'
const recipientId = '22222222-2222-4222-8222-222222222222'
const operationId = '33333333-3333-4333-8333-333333333333'
const receiptExpiry = '2026-07-27T10:00:00+09:00'

const preview: LinkedPreviewResponse = {
  channelId,
  channelLabel: '通知チャネル',
  recipientId,
  recipientDisplayName: '受信者A',
  friendshipState: 'friend',
  formattedText: '【障害通知】\n\n復旧しました。',
  receiptRequested: true,
  receiptExpiresAt: receiptExpiry,
  confirmationToken: 'confirmation-secret-canary',
}

const status = (
  deliveryStatus: LinkedDeliveryStatus['status'],
  receiptStatus: 'pending' | 'confirmed' = 'pending',
): LinkedDeliveryStatus => {
  const common = {
    operationId,
    snapshot: {
      channelId,
      channelLabel: '通知チャネル',
      recipientId,
      channelActive: true,
      recipientEnabled: true,
      friendshipState: 'friend' as const,
    },
    acceptedAt: '2026-07-26T10:00:00+09:00',
    completedAt: deliveryStatus === 'processing' ? null : '2026-07-26T10:00:01+09:00',
    lineRequestId: deliveryStatus === 'succeeded' ? 'safe-request-id' : null,
    receipt: {
      requested: true,
      status: receiptStatus,
      expiresAt: receiptExpiry,
      confirmedAt: receiptStatus === 'confirmed' ? '2026-07-26T10:05:00+09:00' : null,
    },
  }
  if (deliveryStatus === 'failed' || deliveryStatus === 'unknown') {
    return {
      ...common,
      status: deliveryStatus,
      completedAt: common.completedAt!,
      error: {
        code: deliveryStatus === 'failed' ? 'line_unavailable' : 'timeout_unknown',
        summary: deliveryStatus === 'failed'
          ? 'LINEへの送信を完了できませんでした。'
          : 'LINEの受付結果を確認できませんでした。',
      },
    }
  }
  return { ...common, status: deliveryStatus }
}

const clientWith = (
  overrides: Partial<LinkedDeliveryApiClient> = {},
): LinkedDeliveryApiClient => ({
  listChannels: vi.fn().mockResolvedValue([{
    channelId,
    label: '通知チャネル',
    active: true,
    deliveryAvailable: true,
    unavailableReason: null,
  }]),
  listRecipients: vi.fn().mockResolvedValue([{
    recipientId,
    displayName: '受信者A',
    enabled: true,
    friendshipState: 'friend',
    deliveryAvailable: true,
    unavailableReason: null,
  }]),
  preview: vi.fn().mockResolvedValue(preview),
  send: vi.fn(),
  checkStatus: vi.fn(),
  ...overrides,
})

const memoryStorage = (): Storage => {
  const values = new Map<string, string>()
  return {
    get length() { return values.size },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => { values.delete(key) },
    setItem: (key, value) => { values.set(key, value) },
  }
}

let container: HTMLDivElement
let root: Root

const click = async (label: string) => {
  const button = [...container.querySelectorAll('button')]
    .find((candidate) => candidate.textContent === label)
  if (button === undefined) throw new Error(`button not found: ${label}`)
  await act(async () => button.click())
}

const select = async (name: string, value: string) => {
  const input = container.querySelector(`input[name="${name}"][value="${value}"]`) as HTMLInputElement
  await act(async () => input.click())
}

const enter = async (name: string, value: string) => {
  const element = container.querySelector(`[name="${name}"]`) as HTMLInputElement | HTMLTextAreaElement
  const prototype = element instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(prototype, 'value')?.set?.call(element, value)
  await act(async () => element.dispatchEvent(new Event('input', { bubbles: true })))
}

const preparePreview = async (client: LinkedDeliveryApiClient) => {
  await act(async () => root.render(
    <DeliveryForm
      linkedClient={client}
      createOperationId={() => operationId}
    />,
  ))
  await select('channelId', channelId)
  await select('recipientId', recipientId)
  await enter('subject', '障害通知')
  await enter('body', '復旧しました。')
  const receipt = container.querySelector('[name="receiptRequested"]') as HTMLInputElement
  await act(async () => receipt.click())
  await click('送信内容を確認')
}

describe('linked delivery preview and result UI', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  })

  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.restoreAllMocks()
    window.sessionStorage.clear()
  })

  // テストケース: 配信対象read中に配信画面をunmountする。
  // 期待値: target readだけをabortし、後着結果やerrorを新画面へ表示しない。
  test('6.3 and 6.4 abort target reads and fence late results at route lifetime end', async () => {
    let resolveChannels!: (value: []) => void
    const client = clientWith({
      listChannels: vi.fn().mockReturnValue(new Promise((resolve) => { resolveChannels = resolve })),
    })
    await act(async () => root.render(<DeliveryForm linkedClient={client} />))
    const signal = vi.mocked(client.listChannels).mock.calls[0]?.[0]?.signal as AbortSignal
    expect(signal).toBeInstanceOf(AbortSignal)

    await act(async () => root.render(<p>移動先</p>))
    expect(signal.aborted).toBe(true)
    await act(async () => resolveChannels([]))
    expect(container.textContent).toBe('移動先')
    expect(client.send).not.toHaveBeenCalled()
    expect(client.preview).not.toHaveBeenCalled()
  })

  // テストケース: tab-localにprocessing配信operation IDだけが保存されている。
  // 期待値: 入力やpreviewを復元せず、同じIDのstatus readだけで既存結果stateをhydrateする。
  test('6.5 hydrates a saved processing operation using only its status read', async () => {
    const storage = createOwnerSessionStorage(memoryStorage())
    storage.saveDeliveryOperationId(operationId)
    const client = clientWith({ checkStatus: vi.fn().mockResolvedValue(status('processing')) })

    await act(async () => root.render(<DeliveryForm linkedClient={client} ownerSessionStorage={storage} />))

    expect(client.checkStatus).toHaveBeenCalledTimes(1)
    expect(client.checkStatus).toHaveBeenCalledWith(operationId, { signal: expect.any(AbortSignal) })
    expect(client.listChannels).not.toHaveBeenCalled()
    expect(client.send).not.toHaveBeenCalled()
    expect(container.textContent).toContain('配信を処理中です')
    expect(storage.readDeliveryOperationId()).toBe(operationId)
    expect(container.querySelector('[name="subject"]')).toBeNull()
  })

  // テストケース: 保存operationがunknownまたは完了状態へhydrateされ、新しい配信を開始する。
  // 期待値: unknownではIDを保持し、完了後の明示操作だけでIDを削除して空入力へ戻る。
  test.each(['unknown', 'succeeded', 'failed'] as const)('6.5 converges saved %s operation without restoring content', async (resultStatus) => {
    const storage = createOwnerSessionStorage(memoryStorage())
    storage.saveDeliveryOperationId(operationId)
    const client = clientWith({ checkStatus: vi.fn().mockResolvedValue(status(resultStatus)) })
    await act(async () => root.render(<DeliveryForm linkedClient={client} ownerSessionStorage={storage} />))

    expect(storage.readDeliveryOperationId()).toBe(operationId)
    if (resultStatus === 'unknown') {
      expect(container.textContent).not.toContain('新しい配信')
      return
    }
    await click('新しい配信')
    expect(storage.readDeliveryOperationId()).toBeNull()
    expect(container.querySelector('[name="subject"]')).not.toBeNull()
    expect(client.listChannels).toHaveBeenCalledTimes(1)
  })

  // テストケース: 保存operation IDのstatusがnot-foundを返す。
  // 期待値: IDを削除し、安全なmessageと新規配信入力だけへ収束する。
  test('6.5 clears a missing saved operation and falls back safely', async () => {
    const storage = createOwnerSessionStorage(memoryStorage())
    storage.saveDeliveryOperationId(operationId)
    const client = clientWith({
      checkStatus: vi.fn().mockRejectedValue(new DeliveryApiError({ code: 'operation_not_found', summary: 'private' }, 404)),
    })
    await act(async () => root.render(<DeliveryForm linkedClient={client} ownerSessionStorage={storage} />))

    expect(storage.readDeliveryOperationId()).toBeNull()
    expect(container.textContent).toContain('以前の配信状態を確認できませんでした。新しい配信を開始できます。')
    expect(container.textContent).not.toContain('private')
    expect(container.querySelector('[name="subject"]')).not.toBeNull()
    expect(client.send).not.toHaveBeenCalled()
  })

  // テストケース: 保存operationのstatus確認で401後、再認証により配信pageを再mountする。
  // 期待値: 保存IDを維持して同じstatusだけを再確認し、新規sendを作らない。
  test('6.6 rechecks the same saved operation after session invalidation', async () => {
    const storage = createOwnerSessionStorage(memoryStorage())
    storage.saveDeliveryOperationId(operationId)
    const invalid = vi.fn()
    const checkStatus = vi.fn()
      .mockRejectedValueOnce(new DeliveryApiError({ code: 'authentication_required', summary: 'login' }, 401))
      .mockResolvedValueOnce(status('succeeded'))
    const client = clientWith({ checkStatus })

    await act(async () => root.render(<DeliveryForm linkedClient={client} ownerSessionStorage={storage} onSessionInvalid={invalid} />))
    expect(invalid).toHaveBeenCalledTimes(1)
    expect(storage.readDeliveryOperationId()).toBe(operationId)
    await act(async () => root.render(<p>login</p>))
    await act(async () => root.render(<DeliveryForm linkedClient={client} ownerSessionStorage={storage} onSessionInvalid={invalid} />))

    expect(checkStatus).toHaveBeenCalledTimes(2)
    expect(checkStatus.mock.calls.map((call) => call[0])).toEqual([operationId, operationId])
    expect(client.send).not.toHaveBeenCalled()
    expect(container.textContent).toContain('LINEに受け付けられました')
  })

  // テストケース: 5軸の入力から受取確認付きpreviewを表示し、入力へ戻る。
  // 期待値: 配信元・配信先・友だち状態・整形済み本文・受取確認期限を表示し、入力値を保持する。
  test('shows the complete preview summary and preserves input when going back', async () => {
    const client = clientWith()
    await preparePreview(client)

    expect(container.textContent).toContain('通知チャネル')
    expect(container.textContent).toContain('受信者A')
    expect(container.textContent).toContain('友だち')
    expect(container.textContent).toContain('【障害通知】\n\n復旧しました。')
    expect(container.textContent).toContain('受取確認あり')
    expect(container.querySelector(`time[datetime="${receiptExpiry}"]`)).not.toBeNull()
    expect(container.textContent).not.toContain(preview.confirmationToken)

    await click('入力へ戻る')
    expect((container.querySelector('[name="subject"]') as HTMLInputElement).value).toBe('障害通知')
    expect((container.querySelector('[name="body"]') as HTMLTextAreaElement).value).toBe('復旧しました。')
    expect((container.querySelector('[name="receiptRequested"]') as HTMLInputElement).checked).toBe(true)
  })

  // テストケース: 確認済み内容の送信ボタンを連打し、processingからsucceededへ状態確認する。
  // 期待値: 同じoperationを一度だけ送信し、LINE受付とpending受取確認を別行で表示する。
  test('sends once and separates LINE acceptance from pending receipt', async () => {
    let resolveSend!: (value: LinkedDeliveryStatus) => void
    const sendPromise = new Promise<LinkedDeliveryStatus>((resolve) => { resolveSend = resolve })
    const client = clientWith({
      send: vi.fn().mockReturnValue(sendPromise),
      checkStatus: vi.fn().mockResolvedValue(status('succeeded')),
    })
    await preparePreview(client)

    const sendButton = [...container.querySelectorAll('button')]
      .find((candidate) => candidate.textContent === '確認した内容を送信')!
    await act(async () => {
      sendButton.click()
      sendButton.click()
    })
    expect(client.send).toHaveBeenCalledTimes(1)
    expect(client.send).toHaveBeenCalledWith({
      channelId,
      recipientId,
      subject: '障害通知',
      body: '復旧しました。',
      receiptRequested: true,
      operationId,
      confirmationToken: preview.confirmationToken,
    })
    expect(container.textContent).toContain('LINEへ送信中です')
    expect(container.querySelector('button[disabled]')).not.toBeNull()

    await act(async () => resolveSend(status('processing')))
    await click('状態を再確認')
    expect(client.checkStatus).toHaveBeenCalledWith(operationId, { signal: expect.any(AbortSignal) })
    expect(client.send).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('LINEに受け付けられました')
    expect(container.textContent).toContain('配信状態')
    expect(container.textContent).toContain('受取確認')
    expect(container.textContent).toContain('確認待ち')
    expect(container.textContent).not.toContain('端末に到達')
    expect(container.textContent).not.toContain('既読')
  })

  // テストケース: LINE受付後に状態を再確認してrecipientの明示的受取確認を取得する。
  // 期待値: 配信状態を受付済みのまま維持し、受取確認だけを確認済み日時へ更新する。
  test('refreshes a succeeded delivery to show confirmed receipt independently', async () => {
    const client = clientWith({
      send: vi.fn().mockResolvedValue(status('succeeded')),
      checkStatus: vi.fn().mockResolvedValue(status('succeeded', 'confirmed')),
    })
    await preparePreview(client)
    await click('確認した内容を送信')
    await click('状態を再確認')

    expect(container.textContent).toContain('LINEに受け付けられました')
    expect(container.textContent).toContain('確認済み')
    expect(container.querySelector('time[datetime="2026-07-26T10:05:00+09:00"]')).not.toBeNull()
    expect(client.send).toHaveBeenCalledTimes(1)
  })

  // テストケース: LINE受付結果がunknownで受取確認だけが確定し、状態を再照会する。
  // 期待値: 成功・失敗を推測せずstatus確認だけを案内し、自動再送と秘密値の表示を行わない。
  test('shows safe unknown guidance without resend or secret values', async () => {
    const unknown = status('unknown', 'confirmed')
    const client = clientWith({
      send: vi.fn().mockResolvedValue(unknown),
      checkStatus: vi.fn().mockResolvedValue(unknown),
    })
    await preparePreview(client)
    await click('確認した内容を送信')

    expect(container.textContent).toContain('送信結果を確認できません')
    expect(container.textContent).toContain('LINEの受付結果を確認できませんでした。')
    expect(container.textContent).toContain('受取確認')
    expect(container.textContent).toContain('確認済み')
    expect(container.textContent).not.toContain('再送')
    expect(container.textContent).not.toContain(preview.confirmationToken)
    expect(container.textContent).not.toContain(channelId)
    expect(container.textContent).not.toContain(recipientId)

    await click('状態を再確認')
    expect(client.checkStatus).toHaveBeenCalledWith(operationId, { signal: expect.any(AbortSignal) })
    expect(client.send).toHaveBeenCalledTimes(1)
  })

  // テストケース: LINEが送信を拒否し、受取確認期限も過ぎた確定結果を表示する。
  // 期待値: safe summary、配信失敗、受取確認期限切れを分離し、再送操作を表示しない。
  test('shows failed delivery and expired receipt as separate safe outcomes', async () => {
    const failed = status('failed')
    failed.receipt.status = 'expired'
    const client = clientWith({
      send: vi.fn().mockResolvedValue(failed),
    })
    await preparePreview(client)
    await click('確認した内容を送信')

    expect(container.textContent).toContain('配信を完了できませんでした')
    expect(container.textContent).toContain('LINEへの送信を完了できませんでした。')
    expect(container.textContent).toContain('配信状態')
    expect(container.textContent).toContain('失敗')
    expect(container.textContent).toContain('受取確認')
    expect(container.textContent).toContain('期限切れ')
    expect(container.textContent).not.toContain('状態を再確認')
    expect(container.textContent).not.toContain('再送')
  })
})
