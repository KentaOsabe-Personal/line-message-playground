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
