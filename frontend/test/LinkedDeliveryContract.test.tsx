import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import type { LinkedDeliveryApiClient } from '../src/deliveryApi'
import type {
  DeliveryChannelChoice,
  DeliveryRecipientChoice,
  LinkedDeliveryStatus,
  LinkedPreviewResponse,
  ReceiptStatus,
} from '../src/deliveryDto'
import DeliveryForm from '../src/DeliveryForm'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const channelId = '11111111-1111-4111-8111-111111111111'
const inactiveChannelId = '22222222-2222-4222-8222-222222222222'
const recipientId = '33333333-3333-4333-8333-333333333333'
const unavailableRecipientId = '44444444-4444-4444-8444-444444444444'
const operationId = '55555555-5555-4555-8555-555555555555'
const receiptExpiry = '2026-07-29T10:00:00+09:00'
const receiptConfirmedAt = '2026-07-28T10:05:00+09:00'
const secretCanary = 'secret-token-canary'
const piiCanary = 'U-sensitive-line-subject'
const liveDisplayNameCanary = 'live-recipient-display-name-canary'

const channels: DeliveryChannelChoice[] = [
  {
    channelId,
    label: '通知チャネル',
    active: true,
    deliveryAvailable: true,
    unavailableReason: null,
  },
  {
    channelId: inactiveChannelId,
    label: '停止チャネル',
    active: false,
    deliveryAvailable: false,
    unavailableReason: 'channel_inactive',
  },
]

const recipients: DeliveryRecipientChoice[] = [
  {
    recipientId,
    displayName: '受信者A',
    enabled: true,
    friendshipState: 'friend',
    deliveryAvailable: true,
    unavailableReason: null,
  },
  {
    recipientId: unavailableRecipientId,
    displayName: '受信者B',
    enabled: false,
    friendshipState: 'not_friend',
    deliveryAvailable: false,
    unavailableReason: 'recipient_disabled',
  },
]

const preview: LinkedPreviewResponse = {
  channelId,
  channelLabel: '通知チャネル',
  recipientId,
  recipientDisplayName: '受信者A',
  friendshipState: 'friend',
  formattedText: '【障害通知】\n\n復旧しました。',
  receiptRequested: true,
  receiptExpiresAt: receiptExpiry,
  confirmationToken: 'opaque-confirmation',
}

const jsonResponse = (body: unknown, status = 200) => new Response(
  JSON.stringify(body),
  { status, headers: { 'Content-Type': 'application/json' } },
)

const statusFor = (
  deliveryStatus: LinkedDeliveryStatus['status'],
  receiptStatus: ReceiptStatus,
): LinkedDeliveryStatus => {
  const receiptRequested = receiptStatus !== 'not_requested'
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
    acceptedAt: '2026-07-28T10:00:00+09:00',
    completedAt: deliveryStatus === 'processing' ? null : '2026-07-28T10:00:01+09:00',
    lineRequestId: deliveryStatus === 'succeeded' ? 'safe-request-id' : null,
    receipt: {
      requested: receiptRequested,
      status: receiptStatus,
      expiresAt: receiptRequested ? receiptExpiry : null,
      confirmedAt: receiptStatus === 'confirmed' ? receiptConfirmedAt : null,
    },
  }
  if (deliveryStatus === 'failed' || deliveryStatus === 'unknown') {
    return {
      ...common,
      status: deliveryStatus,
      completedAt: common.completedAt!,
      error: {
        code: deliveryStatus === 'failed' ? 'permission' : 'timeout_unknown',
        summary: deliveryStatus === 'failed'
          ? 'LINEへの送信を完了できませんでした。'
          : 'LINEの受付結果を確認できませんでした。',
      },
    }
  }
  return { ...common, status: deliveryStatus }
}

const clientWithStatus = (result: LinkedDeliveryStatus): LinkedDeliveryApiClient => ({
  listChannels: vi.fn().mockResolvedValue([channels[0]]),
  listRecipients: vi.fn().mockResolvedValue([recipients[0]]),
  preview: vi.fn().mockResolvedValue({
    ...preview,
    receiptRequested: result.receipt.requested,
    receiptExpiresAt: result.receipt.expiresAt,
  }),
  send: vi.fn().mockResolvedValue(result),
  checkStatus: vi.fn(),
})

let container: HTMLDivElement
let root: Root

const clickButton = async (label: string) => {
  const button = [...container.querySelectorAll('button')]
    .find((candidate) => candidate.textContent === label)
  if (button === undefined) throw new Error(`button not found: ${label}`)
  await act(async () => button.click())
}

const clickInput = async (name: string, value?: string) => {
  const selector = value === undefined
    ? `input[name="${name}"]`
    : `input[name="${name}"][value="${value}"]`
  const input = container.querySelector(selector) as HTMLInputElement | null
  if (input === null) throw new Error(`input not found: ${selector}`)
  await act(async () => input.click())
}

const enterText = async (name: string, value: string) => {
  const element = container.querySelector(`[name="${name}"]`) as
    | HTMLInputElement
    | HTMLTextAreaElement
    | null
  if (element === null) throw new Error(`field not found: ${name}`)
  const prototype = element instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(prototype, 'value')?.set?.call(element, value)
  await act(async () => element.dispatchEvent(new Event('input', { bubbles: true })))
}

const enterPreview = async () => {
  await clickInput('channelId', channelId)
  await clickInput('recipientId', recipientId)
  await enterText('subject', '障害通知')
  await enterText('body', '復旧しました。')
  await clickInput('receiptRequested')
  await clickButton('送信内容を確認')
}

describe('linked delivery frontend contract', () => {
  beforeEach(() => {
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    document.cookie = 'csrftoken=csrf-contract; path=/'
  })

  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    document.cookie = 'csrftoken=; Max-Age=0; path=/'
    vi.restoreAllMocks()
  })

  // テストケース: 依存注入なしの配信画面でtarget選択から曖昧なsend結果のstatus確認まで進める。
  // 期待値: protected relative APIへ公開payloadだけを送り、sendは一回、statusは同じoperationで一回だけ実行する。
  test('runs the production composition without resending an ambiguous operation', async () => {
    const requestLog: Array<{ path: string; init?: RequestInit }> = []
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async (input: string | URL | Request, init?: RequestInit) => {
        const path = String(input)
        requestLog.push({ path, init })
        if (path === '/api/deliveries/targets/channels/') {
          return jsonResponse({ items: channels })
        }
        if (path === `/api/deliveries/targets/channels/${channelId}/recipients/`) {
          return jsonResponse({ items: recipients })
        }
        if (path === '/api/deliveries/preview/') return jsonResponse(preview)
        if (path === '/api/deliveries/') throw new TypeError(`${secretCanary}:${piiCanary}`)
        if (path === `/api/deliveries/${operationId}/status/`) {
          return jsonResponse(statusFor('unknown', 'confirmed'))
        }
        throw new Error(`unexpected path: ${path}`)
      },
    )

    await act(async () => root.render(
      <DeliveryForm createOperationId={() => operationId} />,
    ))

    expect(container.textContent).toContain('チャネルが無効です')
    expect((container.querySelector(
      `input[name="channelId"][value="${inactiveChannelId}"]`,
    ) as HTMLInputElement).disabled).toBe(true)
    expect(container.querySelectorAll('fieldset legend')[0]?.textContent).toBe('配信元チャネル')
    expect(container.querySelector('input[name="recipientId"]')).toBeNull()

    await enterPreview()
    const sendButton = [...container.querySelectorAll('button')]
      .find((candidate) => candidate.textContent === '確認した内容を送信')!
    await act(async () => {
      sendButton.click()
      sendButton.click()
    })

    const sendCalls = requestLog.filter(({ path }) => path === '/api/deliveries/')
    const statusCalls = requestLog.filter(
      ({ path }) => path === `/api/deliveries/${operationId}/status/`,
    )
    expect(sendCalls).toHaveLength(1)
    expect(statusCalls).toHaveLength(1)
    expect(JSON.parse(String(sendCalls[0]?.init?.body))).toEqual({
      channelId,
      recipientId,
      subject: '障害通知',
      body: '復旧しました。',
      receiptRequested: true,
      operationId,
      confirmationToken: preview.confirmationToken,
    })
    expect(sendCalls[0]?.init).toMatchObject({
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': 'csrf-contract',
      },
    })
    expect(fetchMock).toHaveBeenCalledTimes(5)
    expect(container.textContent).toContain('送信結果を確認できません')
    expect(container.textContent).toContain('状態だけを再確認してください')
    expect(container.textContent).toContain('確認済み')
    expect(container.textContent).not.toContain('同じ送信操作を再試行')
    expect(container.textContent).not.toContain(secretCanary)
    expect(container.textContent).not.toContain(piiCanary)
    expect(container.textContent).not.toContain(channelId)
    expect(container.textContent).not.toContain(recipientId)
    expect(container.querySelector('[aria-live="polite"]')).not.toBeNull()
  })

  // テストケース: 既定compositionのtarget応答に余剰secretとPII fieldが含まれる。
  // 期待値: strict DTO境界でsafe protocol errorへ変換し、生値や任意target操作を画面へ出さない。
  test('contains unsafe target payloads behind a safe accessible protocol error', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({
      items: [{
        ...channels[0],
        accessToken: secretCanary,
        lineSubject: piiCanary,
      }],
    }))

    await act(async () => root.render(<DeliveryForm />))

    const alert = container.querySelector('[role="alert"]')
    expect(alert?.textContent).toContain('応答形式を確認できません。')
    expect(alert?.textContent).toContain('チャネルを再読み込み')
    expect(container.textContent).not.toContain(secretCanary)
    expect(container.textContent).not.toContain(piiCanary)
    expect(container.querySelector('[name="lineUserId"]')).toBeNull()
    expect(container.querySelector('[name="accessToken"]')).toBeNull()
    expect((container.querySelector('button[type="submit"]') as HTMLButtonElement).disabled)
      .toBe(true)
  })

  // テストケース: live targetとpreviewに現在の表示名を返し、送信後はdisplay nameを持たないstatusへ遷移する。
  // 期待値: 選択・確認中だけ表示名canaryを表示し、永続監査相当の結果UIとconfirmation値には残さない。
  test('shows the live display name only before the delivery status result', async () => {
    const client: LinkedDeliveryApiClient = {
      listChannels: vi.fn().mockResolvedValue([channels[0]]),
      listRecipients: vi.fn().mockResolvedValue([{
        ...recipients[0],
        displayName: liveDisplayNameCanary,
      }]),
      preview: vi.fn().mockResolvedValue({
        ...preview,
        recipientDisplayName: liveDisplayNameCanary,
      }),
      send: vi.fn().mockResolvedValue(statusFor('succeeded', 'pending')),
      checkStatus: vi.fn(),
    }

    await act(async () => root.render(
      <DeliveryForm
        linkedClient={client}
        createOperationId={() => operationId}
      />,
    ))
    await clickInput('channelId', channelId)
    expect(container.textContent).toContain(liveDisplayNameCanary)
    await clickInput('recipientId', recipientId)
    await enterText('subject', '障害通知')
    await enterText('body', '復旧しました。')
    await clickInput('receiptRequested')
    await clickButton('送信内容を確認')
    expect(container.textContent).toContain(liveDisplayNameCanary)
    expect(container.textContent).not.toContain(preview.confirmationToken)

    await clickButton('確認した内容を送信')

    expect(container.textContent).toContain('LINEに受け付けられました')
    expect(container.textContent).not.toContain(liveDisplayNameCanary)
    expect(container.textContent).not.toContain(preview.confirmationToken)
    expect(client.send).toHaveBeenCalledTimes(1)
  })

  // テストケース: delivery四状態とreceipt四状態の全組合せを配信画面へ適用する。
  // 期待値: 配信結果と受取確認を別の表示領域で表し、LINE受付・結果不明・処理中の意味を混同しない。
  test('renders every delivery and receipt status orthogonally', async () => {
    const deliveryStates = ['processing', 'succeeded', 'failed', 'unknown'] as const
    const receiptStates = ['not_requested', 'pending', 'confirmed', 'expired'] as const
    const deliveryLabels = {
      processing: '配信を処理中です',
      succeeded: 'LINEに受け付けられました',
      failed: '配信を完了できませんでした',
      unknown: '送信結果を確認できません',
    } as const
    const receiptLabels = {
      not_requested: '受取確認なし',
      pending: '確認待ち',
      confirmed: '確認済み',
      expired: '期限切れ',
    } as const

    for (const deliveryStatus of deliveryStates) {
      for (const receiptStatus of receiptStates) {
        await act(async () => root.unmount())
        root = createRoot(container)
        const result = statusFor(deliveryStatus, receiptStatus)
        await act(async () => root.render(
          <DeliveryForm
            linkedClient={clientWithStatus(result)}
            createOperationId={() => operationId}
          />,
        ))
        await clickInput('channelId', channelId)
        await clickInput('recipientId', recipientId)
        await enterText('subject', '障害通知')
        await enterText('body', '復旧しました。')
        if (result.receipt.requested) await clickInput('receiptRequested')
        await clickButton('送信内容を確認')
        await clickButton('確認した内容を送信')

        const receiptSummary = container.querySelector('.receipt-summary')
        expect(container.textContent).toContain(deliveryLabels[deliveryStatus])
        expect(receiptSummary?.textContent).toContain(receiptLabels[receiptStatus])
        expect(receiptSummary?.querySelector('strong')?.textContent).toBe('受取確認')
        expect(container.textContent).not.toContain('端末に到達')
        expect(container.textContent).not.toContain('既読')
        if (receiptStatus === 'confirmed') {
          expect(receiptSummary?.querySelector(
            `time[datetime="${receiptConfirmedAt}"]`,
          )).not.toBeNull()
        }
        if (deliveryStatus === 'processing') {
          expect(container.querySelector('input[name="subject"]')).toBeNull()
          expect(container.querySelector('input[name="channelId"]')).toBeNull()
          expect(container.querySelector('button[type="button"]')?.textContent)
            .toBe('状態を再確認')
        }
        if (deliveryStatus === 'unknown') {
          expect(container.textContent).toContain('状態だけを再確認してください')
          expect(container.textContent).not.toContain('新しい配信')
        }
      }
    }
  })
})
