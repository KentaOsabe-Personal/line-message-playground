import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import ChannelEditor from '../src/ChannelEditor'
import type { ChannelAdminItem } from '../src/channelAdminDto'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root
const item: ChannelAdminItem = {
  channelId: '123e4567-e89b-42d3-a456-426614174000', label: '既存', messagingApiChannelId: '123',
  botUserId: `U${'b'.repeat(32)}`, providerId: '456', active: true,
  credentialsState: 'configured', credentialsUpdatedAt: '2026-07-29T10:00:00Z',
  createdAt: '2026-07-29T10:00:00Z', updatedAt: '2026-07-29T10:00:00Z',
  webhookUrl: 'https://example.com/api/line/webhooks/123e4567-e89b-42d3-a456-426614174000/',
}

beforeEach(() => { container = document.createElement('div'); document.body.append(container); root = createRoot(container) })
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks() })

test('keeps edit credentials uncontrolled and resets them after a failed submit', async () => {
  const onSubmit = vi.fn().mockRejectedValue(new Error('secret must not be shown'))
  await act(async () => root.render(<ChannelEditor mode="edit" item={item} onSubmit={onSubmit} />))
  const token = container.querySelector<HTMLInputElement>('input[name="accessToken"]')!
  const secret = container.querySelector<HTMLInputElement>('input[name="channelSecret"]')!
  for (const input of [token, secret]) {
    expect(input.getAttribute('value')).toBeNull()
    expect(input.getAttribute('placeholder')).toBeNull()
    expect([...input.attributes].some((attribute) => attribute.name.startsWith('data-'))).toBe(false)
  }
  token.value = 'temporary-token'
  secret.value = 'temporary-secret'
  const form = container.querySelector('form')!
  await act(async () => form.dispatchEvent(new SubmitEvent('submit', { bubbles: true, cancelable: true })))
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ accessToken: 'temporary-token', channelSecret: 'temporary-secret' }))
  expect(token.value).toBe('')
  expect(secret.value).toBe('')
  expect(container.textContent).not.toContain('temporary-token')
  expect(container.textContent).not.toContain('temporary-secret')
  expect(container.textContent).not.toContain('secret must not be shown')
})

test('rejects a one-sided credential pair before calling the mutation', async () => {
  const onSubmit = vi.fn()
  await act(async () => root.render(<ChannelEditor mode="edit" item={item} onSubmit={onSubmit} />))
  container.querySelector<HTMLInputElement>('input[name="accessToken"]')!.value = 'temporary-token'
  await act(async () => container.querySelector('form')!.dispatchEvent(new SubmitEvent('submit', { bubbles: true, cancelable: true })))
  expect(onSubmit).not.toHaveBeenCalled()
  expect(container.textContent).toContain('資格情報は完全なペアで入力してください')
  expect(container.querySelector<HTMLInputElement>('input[name="accessToken"]')!.value).toBe('')
})

// テストケース: 新規登録で完全な資格情報pairを一度だけ送信する
// 期待値: 成功後は全fieldをresetし、秘密値をDOMへ保持しない
test('submits a complete create pair once and clears every form field after success', async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  await act(async () => root.render(<ChannelEditor mode="create" onSubmit={onSubmit} />))
  const values: Record<string, string> = {
    label: '新規', messagingApiChannelId: '789', botUserId: `U${'d'.repeat(32)}`,
    providerId: '456', accessToken: 'create-token-canary', channelSecret: 'create-secret-canary',
  }
  for (const [name, value] of Object.entries(values)) {
    container.querySelector<HTMLInputElement>(`input[name="${name}"]`)!.value = value
  }
  container.querySelector<HTMLInputElement>('input[name="active"]')!.checked = true

  await act(async () => container.querySelector('form')!.dispatchEvent(
    new SubmitEvent('submit', { bubbles: true, cancelable: true }),
  ))

  expect(onSubmit).toHaveBeenCalledTimes(1)
  expect(onSubmit).toHaveBeenCalledWith({ ...values, active: true })
  for (const name of Object.keys(values)) {
    expect(container.querySelector<HTMLInputElement>(`input[name="${name}"]`)!.value).toBe('')
  }
  expect(container.textContent).not.toContain('create-token-canary')
  expect(container.textContent).not.toContain('create-secret-canary')
})

// テストケース: 保存済み資格情報を空欄のままmetadataだけ更新する
// 期待値: 秘密fieldと固定providerをrequestへ含めず、非秘密項目だけを送る
test('omits blank credentials from a metadata-only edit', async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined)
  await act(async () => root.render(<ChannelEditor mode="edit" item={item} onSubmit={onSubmit} />))
  container.querySelector<HTMLInputElement>('input[name="label"]')!.value = 'metadata only'

  await act(async () => container.querySelector('form')!.dispatchEvent(
    new SubmitEvent('submit', { bubbles: true, cancelable: true }),
  ))

  expect(onSubmit).toHaveBeenCalledWith({
    expectedUpdatedAt: item.updatedAt,
    label: 'metadata only',
    messagingApiChannelId: item.messagingApiChannelId,
    botUserId: item.botUserId,
  })
})
