import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import RichMenuOperationPanel from '../src/RichMenuOperationPanel'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const now = '2026-08-03T10:00:00+09:00'
const preview = { channelId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', channelLabel: '通知', templateId: 'jp-link-one', templateVersion: 1, fields: [{ displayName: '案内', uri: 'https://example.com' }], image: { contentType: 'image/png' as const, width: 2500, height: 843, digest: 'a'.repeat(64), base64: 'aA==' }, observation: { kind: 'external_default' as const, observedAt: now, fingerprint: 'b'.repeat(64), managedResourceId: null }, warnings: ['external_default_replaced' as const], confirmationToken: 'opaque', expiresAt: now }
const result = { operationId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', kind: 'apply' as const, status: 'succeeded' as const, stage: null, result: 'succeeded' as const, subjectOperationId: null, targetResourceId: null, acceptedAt: now, completedAt: now, nextAllowedActions: [] }
let container: HTMLDivElement; let root: Root
describe('RichMenuOperationPanel', () => {
  beforeEach(() => { container = document.createElement('div'); document.body.append(container); root = createRoot(container) })
  afterEach(async () => { await act(async () => root.unmount()); container.remove() })
  // テストケース: apply確認を表示し、再描画前に実行ボタンを二重clickする。
  // 期待値: 対象と置換影響を表示し、同じoperationのPOSTを一件だけ開始する。
  test('confirms the apply impact and prevents duplicate starts', async () => {
    const onApply = vi.fn()
    await act(async () => root.render(<RichMenuOperationPanel channelLabel="通知" preview={preview} currentDefault="アプリ外の既定" busy={false} result={null} onApply={onApply} />))
    expect(container.textContent).toContain('適用の最終確認')
    expect(container.textContent).toContain('アプリ外資源自体は削除しません')
    const button = container.querySelector('button')!
    await act(async () => { button.click(); button.click() })
    expect(onApply).toHaveBeenCalledTimes(1)
    await act(async () => root.render(<RichMenuOperationPanel channelLabel="通知" preview={preview} currentDefault="アプリ外の既定" busy={false} result={result} onApply={onApply} />))
    expect(container.textContent).toContain(result.operationId)
    expect(container.querySelector('button')?.disabled).toBe(true)
  })
})
