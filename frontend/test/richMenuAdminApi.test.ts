import { describe, expect, test, vi } from 'vitest'

import { createRichMenuAdminApiClient, RichMenuAdminApiError } from '../src/richMenuAdminApi'
import type { ProtectedHttpClient } from '../src/httpApi'

const channelId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const operationId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const recoveryId = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
const now = '2026-08-03T10:00:00+09:00'
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const templates = { items: [{
  templateId: 'jp-link-one', version: 1, displayName: '1リンク', canvas: { width: 2500, height: 843 },
  areas: [{ field: 'area1', description: '全面', bounds: { x: 0, y: 0, width: 2500, height: 843 } }],
  requiredFields: ['area1'], limits: { displayName: 20, uri: 1000 },
}] }
const observation = { kind: 'default_none', observedAt: now, fingerprint: 'a'.repeat(64), managedResourceId: null }
const state = { channelId, currentResource: null, blockingOperation: null, activeOperation: null, cleanupResources: [], latestObservation: observation, historySummary: { totalCount: 0, latestOperationId: null, latestStatus: null }, nextAllowedActions: ['new_preview'], mode: 'enabled', effectiveActions: ['new_preview'], unavailableReason: null }
const operation = { operationId, kind: 'apply', status: 'succeeded', stage: null, result: 'succeeded', subjectOperationId: null, targetResourceId: null, acceptedAt: now, completedAt: now, nextAllowedActions: ['view_history'] }
const history = { items: [], nextCursor: null, hasMore: false }
const deactivation = { channelId, channelActive: true, channelUpdatedAt: now, operationId, status: 'checking', reason: null, subjectOperationId: null, recoveryOperationId: null, nextAction: 'get_state', acceptedAt: now, updatedAt: now, completedAt: null }

describe('rich menu admin API', () => {
  // テストケース: template、preview、state、operation、history、無効化の全HTTP手順を呼ぶ。
  // 期待値: same-origin protected clientへ正しいpath・method・bodyを一回ずつ渡す。
  test('maps every owner procedure without retries', async () => {
    const signal = new AbortController().signal
    const preview = { channelId, channelLabel: '通知', templateId: 'jp-link-one', templateVersion: 1, fields: [{ displayName: '案内', uri: 'https://example.com' }], image: { contentType: 'image/png', width: 2500, height: 843, digest: 'b'.repeat(64), base64: 'aGVsbG8=' }, observation, warnings: [], confirmationToken: 'opaque', expiresAt: now }
    const request = vi.fn()
      .mockResolvedValueOnce(response(templates)).mockResolvedValueOnce(response(preview))
      .mockResolvedValueOnce(response(state)).mockResolvedValueOnce(response(operation))
      .mockResolvedValueOnce(response(operation)).mockResolvedValueOnce(response(history))
      .mockResolvedValueOnce(response(deactivation)).mockResolvedValueOnce(response(deactivation))
      .mockResolvedValueOnce(response(deactivation))
    const client = createRichMenuAdminApiClient({ request } as ProtectedHttpClient)
    await client.listTemplates({ signal })
    await client.createPreview(channelId, { templateId: 'jp-link-one', templateVersion: 1, channelRevision: now, fields: { area1: { displayName: '案内', uri: 'https://example.com' } } })
    await client.getState(channelId, { signal })
    await client.startOperation(channelId, { kind: 'apply', operationId, channelRevision: now, confirmationToken: 'opaque', templateId: 'jp-link-one', templateVersion: 1, fields: { area1: { displayName: '案内', uri: 'https://example.com' } } })
    await client.getOperation(operationId, { signal })
    await client.getHistory(channelId, 'opaque cursor', { signal })
    await client.getDeactivation(channelId, { signal })
    await client.startDeactivation(channelId, { operationId, expectedUpdatedAt: now })
    await client.recheckDeactivation(channelId, { operationId, recoveryOperationId: recoveryId, expectedUpdatedAt: now })
    expect(request).toHaveBeenCalledTimes(9)
    expect(request.mock.calls.map(([input]) => input)).toEqual([
      { path: '/api/line/rich-menus/templates/', method: 'GET', signal },
      { path: `/api/line/rich-menus/channels/${channelId}/preview/`, method: 'POST', body: expect.objectContaining({ templateId: 'jp-link-one' }) },
      { path: `/api/line/rich-menus/channels/${channelId}/state/`, method: 'GET', signal },
      { path: `/api/line/rich-menus/channels/${channelId}/operations/`, method: 'POST', body: expect.objectContaining({ operationId }) },
      { path: `/api/line/rich-menus/operations/${operationId}/`, method: 'GET', signal },
      { path: `/api/line/rich-menus/channels/${channelId}/history/?limit=20&cursor=opaque+cursor`, method: 'GET', signal },
      { path: `/api/line/channels/${channelId}/deactivation/`, method: 'GET', signal },
      { path: `/api/line/channels/${channelId}/deactivation/`, method: 'POST', body: { operationId, expectedUpdatedAt: now } },
      { path: `/api/line/channels/${channelId}/deactivation/recheck/`, method: 'POST', body: { operationId, recoveryOperationId: recoveryId, expectedUpdatedAt: now } },
    ])
  })

  // テストケース: GETと秘密値を含むPOSTがnetwork failureになる。
  // 期待値: GETは古い表示を破棄するload_failed、POSTは結果を推測しないrefresh_requiredとなり秘密を保持しない。
  test('classifies GET and POST network uncertainty safely', async () => {
    const secret = 'must-not-survive'
    const client = createRichMenuAdminApiClient({ request: vi.fn().mockRejectedValue(new Error(secret)) } as unknown as ProtectedHttpClient)
    await expect(client.getState(channelId)).rejects.toMatchObject({ outcome: 'load_failed' })
    let caught: unknown
    try { await client.startOperation(channelId, { kind: 'apply', operationId, channelRevision: now, confirmationToken: secret, templateId: 'jp-link-one', templateVersion: 1, fields: { area1: { displayName: '案内', uri: 'https://example.com' } } }) } catch (error) { caught = error }
    expect(caught).toBeInstanceOf(RichMenuAdminApiError)
    expect(caught).toMatchObject({ outcome: 'refresh_required' })
    expect(JSON.stringify(caught)).not.toContain(secret)
    expect(String(caught)).not.toContain(secret)
  })
})
