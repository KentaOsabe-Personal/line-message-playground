import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import ChannelActions from '../src/ChannelActions'
import type { ChannelAdminItem } from '../src/channelAdminDto'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root
const item: ChannelAdminItem = {
  channelId: '123e4567-e89b-42d3-a456-426614174000', label: '操作対象', messagingApiChannelId: '123',
  botUserId: `U${'c'.repeat(32)}`, providerId: '456', active: false,
  credentialsState: 'repair_required', credentialsUpdatedAt: null,
  createdAt: '2026-07-29T10:00:00Z', updatedAt: '2026-07-29T10:00:00Z',
  webhookUrl: 'https://example.com/api/line/webhooks/123e4567-e89b-42d3-a456-426614174000/',
}

beforeEach(() => { container = document.createElement('div'); document.body.append(container); root = createRoot(container) })
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks() })

test('copies the displayed webhook URL and shows a safe notification', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
  await act(async () => root.render(<ChannelActions item={item} onSetState={vi.fn()} onDelete={vi.fn()} onCheck={vi.fn()} />))
  const copy = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Webhook URLをコピー')
  await act(async () => copy?.click())
  expect(writeText).toHaveBeenCalledWith(item.webhookUrl)
  expect(container.textContent).toContain('Webhook URLをコピーしました')
})

test('shows connection scope only after an explicit check', async () => {
  const onCheck = vi.fn().mockResolvedValue({
    channelId: item.channelId, status: 'connected', checkedAt: '2026-07-29T12:00:00Z',
    scope: 'access_token_and_bot_identity_only',
  })
  await act(async () => root.render(<ChannelActions item={item} onSetState={vi.fn()} onDelete={vi.fn()} onCheck={onCheck} />))
  expect(onCheck).not.toHaveBeenCalled()
  expect(container.textContent).not.toContain('access token と bot identity の確認のみ')
  const check = [...container.querySelectorAll('button')].find((button) => button.textContent === '接続を確認')
  await act(async () => check?.click())
  expect(onCheck).toHaveBeenCalledTimes(1)
  expect(container.textContent).toContain('接続できました')
  expect(container.textContent).toContain('access token と bot identity の確認のみ')
})

test('discards a temporary connection result when the channel revision changes', async () => {
  const onCheck = vi.fn().mockResolvedValue({
    channelId: item.channelId, status: 'connected', checkedAt: '2026-07-29T12:00:00Z',
    scope: 'access_token_and_bot_identity_only',
  })
  await act(async () => root.render(<ChannelActions item={item} onSetState={vi.fn()} onDelete={vi.fn()} onCheck={onCheck} />))
  const check = [...container.querySelectorAll('button')].find((button) => button.textContent === '接続を確認')
  await act(async () => check?.click())
  expect(container.textContent).toContain('接続できました')
  await act(async () => root.render(<ChannelActions item={{ ...item, updatedAt: '2026-07-29T12:30:00Z' }} onSetState={vi.fn()} onDelete={vi.fn()} onCheck={onCheck} />))
  expect(container.textContent).not.toContain('接続できました')
  expect(container.textContent).not.toContain('access token と bot identity の確認のみ')
})

test('requires an irreversible deletion confirmation with channel identity', async () => {
  const onDelete = vi.fn().mockResolvedValue(undefined)
  await act(async () => root.render(<ChannelActions item={item} onSetState={vi.fn()} onDelete={onDelete} onCheck={vi.fn()} />))
  const start = [...container.querySelectorAll('button')].find((button) => button.textContent === '削除')
  await act(async () => start?.click())
  const dialog = container.querySelector('[role="dialog"]')!
  expect(dialog.textContent).toContain(item.label)
  expect(dialog.textContent).toContain(item.channelId)
  expect(dialog.textContent).toContain('取り消せません')
  expect(onDelete).not.toHaveBeenCalled()
  const confirm = [...dialog.querySelectorAll('button')].find((button) => button.textContent === '削除を確定')
  await act(async () => confirm?.click())
  expect(onDelete).toHaveBeenCalledTimes(1)
})

// テストケース: owner clickごとに接続確認の全6分類を返す
// 期待値: 自動確認せず、各safe分類を対応する一時表示へ写像する
test('renders every safe connection classification with no automatic recheck', async () => {
  const labels = {
    connected: '接続できました',
    credential_unavailable: '資格情報を安全に利用できません',
    authentication_failed: 'アクセストークンが拒否されました',
    identity_mismatch: 'bot identity が一致しません',
    rate_limited: 'LINE の利用制限中です',
    line_unavailable: 'LINE の確認結果を確定できません',
  } as const
  for (const [status, label] of Object.entries(labels)) {
    const onCheck = vi.fn().mockResolvedValue({
      channelId: item.channelId, status, checkedAt: '2026-07-29T12:00:00Z',
      scope: 'access_token_and_bot_identity_only',
    })
    await act(async () => root.render(
      <ChannelActions key={status} item={item} onSetState={vi.fn()} onDelete={vi.fn()} onCheck={onCheck} />,
    ))
    expect(onCheck).not.toHaveBeenCalled()
    const check = [...container.querySelectorAll('button')].find((button) => button.textContent === '接続を確認')
    await act(async () => check?.click())
    expect(onCheck).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain(label)
  }
})

// テストケース: repair_requiredチャネルを完全pair修復と同時に有効化する
// 期待値: 一操作だけを送り、完了後は秘密値をDOMへ保持しない
test('repairs credentials during enable and clears secret DOM values after completion', async () => {
  const onSetState = vi.fn().mockResolvedValue(undefined)
  await act(async () => root.render(
    <ChannelActions item={item} onSetState={onSetState} onDelete={vi.fn()} onCheck={vi.fn()} />,
  ))
  const enable = [...container.querySelectorAll('button')].find((button) => button.textContent === '有効化')
  await act(async () => enable?.click())
  const token = container.querySelector<HTMLInputElement>('input[name="accessToken"]')!
  const secret = container.querySelector<HTMLInputElement>('input[name="channelSecret"]')!
  for (const input of [token, secret]) {
    expect(input.getAttribute('value')).toBeNull()
    expect(input.getAttribute('placeholder')).toBeNull()
    expect([...input.attributes].some((attribute) => attribute.name.startsWith('data-'))).toBe(false)
  }
  token.value = 'repair-token-canary'
  secret.value = 'repair-secret-canary'
  await act(async () => container.querySelector('form')!.dispatchEvent(
    new SubmitEvent('submit', { bubbles: true, cancelable: true }),
  ))

  expect(onSetState).toHaveBeenCalledWith(true, {
    accessToken: 'repair-token-canary', channelSecret: 'repair-secret-canary',
  })
  expect(container.querySelector('[role="dialog"]')).toBeNull()
  expect(container.textContent).not.toContain('repair-token-canary')
  expect(container.textContent).not.toContain('repair-secret-canary')
})
