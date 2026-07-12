import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import type { DeliveryApiClient } from './deliveryApi'
import { DeliveryApiError } from './deliveryApi'
import DeliveryForm from './DeliveryForm'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

const click = async (label: string) => {
  const button = [...container.querySelectorAll('button')].find((item) => item.textContent === label)
  if (!button) throw new Error(`button not found: ${label}`)
  await act(async () => button.click())
}

const input = async (name: string, value: string) => {
  const element = container.querySelector(`[name="${name}"]`) as HTMLInputElement | HTMLTextAreaElement | null
  if (!element) throw new Error(`input not found: ${name}`)
  const setter = Object.getOwnPropertyDescriptor(element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value')?.set
  setter?.call(element, value)
  await act(async () => element.dispatchEvent(new Event('input', { bubbles: true })))
}

const renderForm = async (client: DeliveryApiClient) => {
  await act(async () => root.render(<DeliveryForm client={client} createOperationId={() => 'operation-1'} />))
}

describe('DeliveryForm', () => {
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

  // テストケース: 件名と改行付き本文を確認し、入力へ戻って編集する。
  // 期待値: Backendの整形テキストを表示し、入力を保持したまま古い確認を無効化する。
  test('previews backend text and invalidates confirmation after editing', async () => {
    const client: DeliveryApiClient = {
      preview: vi.fn().mockResolvedValue({ formattedText: '【件名】\n\n1行目\n2行目', confirmationToken: 'token' }),
      send: vi.fn(),
      checkStatus: vi.fn(),
    }
    await renderForm(client)
    await input('subject', '件名')
    await input('body', '1行目\n2行目')
    await click('送信内容を確認')

    expect(container.textContent).toContain('【件名】\n\n1行目\n2行目')
    expect(container.textContent).toContain('確認した内容を送信')
    await click('入力へ戻る')
    expect((container.querySelector('[name="subject"]') as HTMLInputElement).value).toBe('件名')
    await input('subject', '変更後')
    expect(container.textContent).not.toContain('確認した内容を送信')
  })

  // テストケース: 最終送信がprocessingになり、状態確認で成功する。
  // 期待値: 処理中は送信操作を無効化し、照会はsendを増やさず成功結果と確認内容を表示する。
  test('disables duplicate submission and checks processing status', async () => {
    const client: DeliveryApiClient = {
      preview: vi.fn().mockResolvedValue({ formattedText: '【件名】\n\n本文', confirmationToken: 'token' }),
      send: vi.fn().mockResolvedValue({ status: 'processing', operationId: 'operation-1', acceptedAt: 'a', expiresAt: 'e' }),
      checkStatus: vi.fn().mockResolvedValue({ status: 'succeeded', operationId: 'operation-1', acceptedAt: 'a', completedAt: 'c', lineRequestId: null }),
    }
    await renderForm(client)
    await input('subject', '件名')
    await input('body', '本文')
    await click('送信内容を確認')
    await click('確認した内容を送信')

    expect(container.textContent).toContain('配信を処理中です')
    expect(client.send).toHaveBeenCalledTimes(1)
    await click('状態を再確認')
    expect(client.checkStatus).toHaveBeenCalledWith('operation-1')
    expect(client.send).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('LINEに受け付けられました')
    expect(container.textContent).toContain('【件名】\n\n本文')
  })

  // テストケース: sendのnetwork error後、status 404を経て同一操作を明示的に再試行する。
  // 期待値: 404前は再試行を表示せず、404後だけ元のID・内容・tokenでsendする。
  test('allows explicit same-operation retry only after status 404', async () => {
    const send = vi.fn()
      .mockRejectedValueOnce(new DeliveryApiError({ code: 'network_error', summary: 'Backendに接続できません。' }))
      .mockResolvedValueOnce({ status: 'failed', operationId: 'operation-1', acceptedAt: 'a', completedAt: 'c', error: { code: 'configuration', summary: 'Backendの配信設定を確認してください。' }, lineRequestId: null })
    const client: DeliveryApiClient = {
      preview: vi.fn().mockResolvedValue({ formattedText: '【件名】\n\n本文', confirmationToken: 'token' }),
      send,
      checkStatus: vi.fn().mockRejectedValue(new DeliveryApiError({ code: 'operation_not_found', summary: '送信操作を確認できませんでした。' }, 404)),
    }
    await renderForm(client)
    await input('subject', '件名')
    await input('body', '本文')
    await click('送信内容を確認')
    await click('確認した内容を送信')

    expect(container.textContent).not.toContain('同じ送信操作を再試行')
    await click('状態を再確認')
    expect(container.textContent).toContain('同じ送信操作を再試行')
    await click('同じ送信操作を再試行')
    expect(send).toHaveBeenLastCalledWith({ subject: '件名', body: '本文', operationId: 'operation-1', confirmationToken: 'token' })
    expect(container.textContent).toContain('送信成功として確定していません')
  })

  // テストケース: sendがBackendの確定した非2xx safe errorを返す。
  // 期待値: 安全な概要と確認済み内容を失敗として表示し、状態確認や再試行へ誘導しない。
  test('shows a confirmed backend rejection without treating it as a network error', async () => {
    const client: DeliveryApiClient = {
      preview: vi.fn().mockResolvedValue({ formattedText: '【件名】\n\n本文', confirmationToken: 'token' }),
      send: vi.fn().mockRejectedValue(new DeliveryApiError({ code: 'operation_id_reused', summary: 'この送信操作IDは別の内容に使用済みです。' }, 409)),
      checkStatus: vi.fn(),
    }
    await renderForm(client)
    await input('subject', '件名')
    await input('body', '本文')
    await click('送信内容を確認')
    await click('確認した内容を送信')

    expect(container.textContent).toContain('この送信操作IDは別の内容に使用済みです。')
    expect(container.textContent).toContain('【件名】\n\n本文')
    expect(container.textContent).not.toContain('状態を再確認')
    expect(container.textContent).not.toContain('同じ送信操作を再試行')
  })
})
