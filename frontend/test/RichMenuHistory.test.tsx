import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import RichMenuHistory from '../src/RichMenuHistory'
import type { HistoryEntryView } from '../src/richMenuAdminDto'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const now = '2026-08-03T10:00:00+09:00'; const channelId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'; const operationId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const item = (label: string): HistoryEntryView => ({ operation: { operationId, kind: 'apply', status: 'succeeded', stage: null, result: 'succeeded', subjectOperationId: null, targetResourceId: null, acceptedAt: now, completedAt: now, nextAllowedActions: [] }, channelId, channelLabel: label, configuration: { templateId: 'jp-link-one', templateVersion: 1, fields: [{ displayName: '案内', uri: 'https://example.com' }] }, transitions: ['accepted', 'succeeded'], defaultRelation: 'became_default', cleanupRelation: 'not_required' })
let container: HTMLDivElement; let root: Root
describe('RichMenuHistory', () => {
  beforeEach(() => { container = document.createElement('div'); document.body.append(container); root = createRoot(container) })
  afterEach(async () => { await act(async () => root.unmount()); container.remove() })
  test('appends one bounded cursor page only after an explicit click', async () => {
    const loadNext = vi.fn().mockResolvedValue({ items: [item('次ページ')], nextCursor: null, hasMore: false })
    await act(async () => root.render(<RichMenuHistory initialPage={{ items: [item('先頭')], nextCursor: 'cursor', hasMore: true }} readOnly loadNext={loadNext} />))
    expect(container.textContent).toContain('先頭')
    expect(container.textContent).toContain('accepted → succeeded')
    expect(container.textContent).toContain('完了時点')
    expect(loadNext).not.toHaveBeenCalled()
    const button = container.querySelector('button')!
    await act(async () => { button.click(); button.click() })
    expect(loadNext).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('次ページ')
    expect(container.textContent).not.toContain('rollback')
  })
  test('keeps prior pages but marks them incomplete after failure', async () => {
    const loadNext = vi.fn().mockRejectedValue(new Error('private'))
    await act(async () => root.render(<RichMenuHistory initialPage={{ items: [item('保存済み')], nextCursor: 'cursor', hasMore: true }} readOnly={false} loadNext={loadNext} />))
    await act(async () => container.querySelector('button')!.click())
    expect(container.textContent).toContain('保存済み')
    expect(container.textContent).toContain('完全・最新とは確認できません')
    expect(container.textContent).not.toContain('private')
  })
})
