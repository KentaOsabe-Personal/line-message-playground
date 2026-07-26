import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import { DeliveryApiError } from '../src/deliveryApi'
import type { LinkedDeliveryApiClient } from '../src/deliveryApi'
import DeliveryForm from '../src/DeliveryForm'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const channelOne = '11111111-1111-4111-8111-111111111111'
const channelTwo = '22222222-2222-4222-8222-222222222222'
const recipientOne = '33333333-3333-4333-8333-333333333333'
const recipientTwo = '44444444-4444-4444-8444-444444444444'

let container: HTMLDivElement
let root: Root

const renderForm = async (client: LinkedDeliveryApiClient) => {
  await act(async () => {
    root.render(<DeliveryForm linkedClient={client} />)
  })
}

const clientWith = (
  overrides: Partial<LinkedDeliveryApiClient> = {},
): LinkedDeliveryApiClient => ({
  listChannels: vi.fn().mockResolvedValue([]),
  listRecipients: vi.fn().mockResolvedValue([]),
  preview: vi.fn(),
  send: vi.fn(),
  checkStatus: vi.fn(),
  ...overrides,
})

const clickRadio = async (name: string, value: string) => {
  const radio = container.querySelector(
    `input[name="${name}"][value="${value}"]`,
  ) as HTMLInputElement | null
  if (radio === null) throw new Error(`radio not found: ${name}`)
  await act(async () => radio.click())
}

const clickButton = async (label: string) => {
  const button = [...container.querySelectorAll('button')]
    .find((candidate) => candidate.textContent === label)
  if (button === undefined) throw new Error(`button not found: ${label}`)
  await act(async () => button.click())
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

const channel = (
  channelId: string,
  label: string,
  deliveryAvailable = true,
) => ({
  channelId,
  label,
  active: deliveryAvailable,
  deliveryAvailable,
  unavailableReason: deliveryAvailable ? null : 'channel_inactive' as const,
})

const recipient = (
  recipientId: string,
  displayName: string,
  overrides: {
    enabled?: boolean
    friendshipState?: 'friend' | 'not_friend' | 'unknown'
    deliveryAvailable?: boolean
    unavailableReason?: 'channel_inactive' | 'recipient_disabled' | 'not_friend' | 'friendship_unknown' | null
  } = {},
) => ({
  recipientId,
  displayName,
  enabled: overrides.enabled ?? true,
  friendshipState: overrides.friendshipState ?? 'friend' as const,
  deliveryAvailable: overrides.deliveryAvailable ?? true,
  unavailableReason: overrides.unavailableReason ?? null,
})

const deferred = <T,>() => {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

describe('linked delivery target selection', () => {
  beforeEach(() => {
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  })

  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.restoreAllMocks()
  })

  // テストケース: 配信画面を開いて登録済みチャネルを取得する。
  // 期待値: safe labelだけの選択肢を表示し、チャネルIDや秘密情報を本文へ表示しない。
  test('loads safe channel summaries', async () => {
    const client = clientWith({
      listChannels: vi.fn().mockResolvedValue([
        channel(channelOne, '通知チャネル'),
        channel(channelTwo, '停止チャネル', false),
      ]),
    })

    await renderForm(client)

    expect(client.listChannels).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('通知チャネル')
    expect(container.textContent).toContain('チャネルが無効です')
    expect((container.querySelector(
      `input[value="${channelTwo}"]`,
    ) as HTMLInputElement).disabled).toBe(true)
    expect(container.textContent).not.toContain(channelOne)
    expect(container.textContent).not.toContain('LINE_CHANNEL_ACCESS_TOKEN')
  })

  // テストケース: 有効なチャネルを選び、状態の異なるrecipient一覧を取得する。
  // 期待値: 配信不能理由を表示して該当radioを無効化し、有効recipientだけを選べる。
  test('distinguishes recipient availability and selects only a deliverable target', async () => {
    const client = clientWith({
      listChannels: vi.fn().mockResolvedValue([channel(channelOne, '通知チャネル')]),
      listRecipients: vi.fn().mockResolvedValue([
        recipient(recipientOne, '受信者A'),
        recipient(recipientTwo, '受信者B', {
          enabled: false,
          deliveryAvailable: false,
          unavailableReason: 'recipient_disabled',
        }),
        recipient('55555555-5555-4555-8555-555555555555', '受信者C', {
          friendshipState: 'not_friend',
          deliveryAvailable: false,
          unavailableReason: 'not_friend',
        }),
        recipient('66666666-6666-4666-8666-666666666666', '受信者D', {
          friendshipState: 'unknown',
          deliveryAvailable: false,
          unavailableReason: 'friendship_unknown',
        }),
      ]),
    })
    await renderForm(client)
    await clickRadio('channelId', channelOne)

    expect(client.listRecipients).toHaveBeenCalledWith(channelOne)
    expect(container.textContent).toContain('recipientが無効です')
    expect(container.textContent).toContain('友だち状態ではありません')
    expect(container.textContent).toContain('友だち状態を確認できません')
    expect((container.querySelector(
      `input[value="${recipientTwo}"]`,
    ) as HTMLInputElement).disabled).toBe(true)

    await clickRadio('recipientId', recipientOne)
    await enterText('subject', '件名')
    await enterText('body', '本文')

    expect((container.querySelector(
      'button[type="submit"]',
    ) as HTMLButtonElement).disabled).toBe(false)
    expect(container.querySelector('[name="lineUserId"]')).toBeNull()
    expect(container.querySelector('[name="target"]')).toBeNull()
  })

  // テストケース: 選択チャネルにrecipientがなく、別チャネルには配信不能recipientだけがある。
  // 期待値: 登録・状態確認の案内を表示し、preview操作を無効のまま維持する。
  test('blocks preview for empty and unavailable recipient lists', async () => {
    const client = clientWith({
      listChannels: vi.fn().mockResolvedValue([
        channel(channelOne, '空チャネル'),
        channel(channelTwo, '状態確認チャネル'),
      ]),
      listRecipients: vi.fn()
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([
          recipient(recipientTwo, '受信者B', {
            friendshipState: 'unknown',
            deliveryAvailable: false,
            unavailableReason: 'friendship_unknown',
          }),
        ]),
    })
    await renderForm(client)
    await clickRadio('channelId', channelOne)

    expect(container.textContent).toContain('登録済みrecipientがありません')
    expect((container.querySelector(
      'button[type="submit"]',
    ) as HTMLButtonElement).disabled).toBe(true)

    await clickRadio('channelId', channelTwo)
    expect(container.textContent).toContain('配信可能なrecipientがありません')
    expect((container.querySelector(
      'button[type="submit"]',
    ) as HTMLButtonElement).disabled).toBe(true)
  })

  // テストケース: recipient取得中に配信元チャネルを切り替え、旧応答が後から完了する。
  // 期待値: recipient選択を解除し、新しいチャネルの応答だけを画面へ反映する。
  test('ignores stale recipient responses after changing the channel', async () => {
    const oldRequest = deferred<ReturnType<typeof recipient>[]>()
    const newRequest = deferred<ReturnType<typeof recipient>[]>()
    const client = clientWith({
      listChannels: vi.fn().mockResolvedValue([
        channel(channelOne, '旧チャネル'),
        channel(channelTwo, '新チャネル'),
      ]),
      listRecipients: vi.fn()
        .mockReturnValueOnce(oldRequest.promise)
        .mockReturnValueOnce(newRequest.promise),
    })
    await renderForm(client)
    await clickRadio('channelId', channelOne)
    await clickRadio('channelId', channelTwo)
    await act(async () => newRequest.resolve([recipient(recipientTwo, '新しい受信者')]))
    await act(async () => oldRequest.resolve([recipient(recipientOne, '古い受信者')]))

    expect(container.textContent).toContain('新しい受信者')
    expect(container.textContent).not.toContain('古い受信者')
    expect(container.querySelector('input[name="recipientId"]:checked')).toBeNull()
  })

  // テストケース: target一覧取得が一度失敗した後、ownerが再読み込みする。
  // 期待値: safe errorと再試行buttonを表示し、成功後は選択肢へ回復する。
  test('shows loading and retries safe target errors', async () => {
    const channelRequest = deferred<ReturnType<typeof channel>[]>()
    const listChannels = vi.fn()
      .mockReturnValueOnce(channelRequest.promise)
      .mockResolvedValueOnce([channel(channelOne, '復旧チャネル')])
    const client = clientWith({ listChannels })
    await renderForm(client)

    expect(container.textContent).toContain('チャネルを読み込んでいます')
    await act(async () => channelRequest.reject(
      new DeliveryApiError({ code: 'storage_unavailable', summary: '一覧を取得できません。' }),
    ))
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('一覧を取得できません。')

    await clickButton('チャネルを再読み込み')
    expect(listChannels).toHaveBeenCalledTimes(2)
    expect(container.textContent).toContain('復旧チャネル')
  })

  // テストケース: 選択チャネルのrecipient取得が失敗した後、ownerが再読み込みする。
  // 期待値: safe errorから同じチャネルの一覧取得だけを再試行し、選択可能状態へ回復する。
  test('retries recipient loading for the selected channel', async () => {
    const listRecipients = vi.fn()
      .mockRejectedValueOnce(new DeliveryApiError({
        code: 'storage_unavailable',
        summary: 'recipient一覧を取得できません。',
      }))
      .mockResolvedValueOnce([recipient(recipientOne, '復旧した受信者')])
    const client = clientWith({
      listChannels: vi.fn().mockResolvedValue([channel(channelOne, '通知チャネル')]),
      listRecipients,
    })
    await renderForm(client)
    await clickRadio('channelId', channelOne)

    expect(container.querySelector('[role="alert"]')?.textContent)
      .toContain('recipient一覧を取得できません。')
    await clickButton('recipientを再読み込み')

    expect(listRecipients).toHaveBeenCalledTimes(2)
    expect(listRecipients).toHaveBeenLastCalledWith(channelOne)
    expect(container.textContent).toContain('復旧した受信者')
  })

  // テストケース: preview要求中にチャネルを変更し、旧preview応答が後から完了する。
  // 期待値: 古い確認を表示せず、recipient再選択が必要なediting状態を維持する。
  test('discards preview and ignores its stale response after upstream selection changes', async () => {
    const previewRequest = deferred<Awaited<ReturnType<LinkedDeliveryApiClient['preview']>>>()
    const client = clientWith({
      listChannels: vi.fn().mockResolvedValue([
        channel(channelOne, '旧チャネル'),
        channel(channelTwo, '新チャネル'),
      ]),
      listRecipients: vi.fn().mockImplementation((channelId: string) =>
        Promise.resolve(channelId === channelOne
          ? [recipient(recipientOne, '旧受信者')]
          : [recipient(recipientTwo, '新受信者')]),
      ),
      preview: vi.fn().mockReturnValue(previewRequest.promise),
    })
    await renderForm(client)
    await clickRadio('channelId', channelOne)
    await clickRadio('recipientId', recipientOne)
    await enterText('subject', '件名')
    await enterText('body', '本文')
    await clickButton('送信内容を確認')
    await clickRadio('channelId', channelTwo)
    await act(async () => previewRequest.resolve({
      channelId: channelOne,
      channelLabel: '旧チャネル',
      recipientId: recipientOne,
      recipientDisplayName: '旧受信者',
      friendshipState: 'friend',
      formattedText: '【件名】\n\n本文',
      receiptRequested: false,
      receiptExpiresAt: null,
      confirmationToken: 'secret-confirmation',
    }))

    expect(container.textContent).not.toContain('実際に送信する内容')
    expect(container.querySelector('input[name="recipientId"]:checked')).toBeNull()
    expect(container.textContent).not.toContain('secret-confirmation')
  })

  // テストケース: target選択領域を支援技術で操作する。
  // 期待値: fieldsetの名称、進捗status、再試行buttonが明示される。
  test('exposes labelled target groups and status controls', async () => {
    const pending = deferred<ReturnType<typeof channel>[]>()
    const client = clientWith({ listChannels: vi.fn().mockReturnValue(pending.promise) })
    await renderForm(client)

    const legends = [...container.querySelectorAll('legend')].map((item) => item.textContent)
    expect(legends).toEqual(['配信元チャネル', '配信先recipient'])
    expect(container.querySelector('[role="status"]')?.textContent).toContain('読み込んでいます')
    expect((container.querySelector('button[type="submit"]') as HTMLButtonElement).disabled).toBe(true)
  })
})
