import { describe, expect, test, vi } from 'vitest'

import { createRichMenuAdminApiClient, RichMenuAdminApiError } from '../src/richMenuAdminApi'
import { createProtectedHttpClient } from '../src/httpApi'
import type { ProtectedHttpClient } from '../src/httpApi'
import { parseRichMenuError } from '../src/richMenuAdminDto'
import currentFixtures from './fixtures/rich-menu-current.json'
import currentErrors from './fixtures/rich-menu-current-errors.json'

const channelId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const operationId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const recoveryId = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
const now = '2026-08-03T10:00:00+09:00'
const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const templates = {
  items: [
    {
      templateId: 'jp-link-one',
      version: 1,
      displayName: '1リンク',
      canvas: { width: 2500, height: 843 },
      areas: [
        { field: 'area1', description: '全面', bounds: { x: 0, y: 0, width: 2500, height: 843 } },
      ],
      requiredFields: ['area1'],
      limits: { displayName: 20, uri: 1000 },
    },
  ],
}
const observation = {
  kind: 'default_none',
  observedAt: now,
  fingerprint: 'a'.repeat(64),
  managedResourceId: null,
}
const state = {
  channelId,
  currentResource: null,
  blockingOperation: null,
  activeOperation: null,
  cleanupResources: [],
  latestObservation: observation,
  historySummary: { totalCount: 0, latestOperationId: null, latestStatus: null },
  nextAllowedActions: ['new_preview'],
  mode: 'enabled',
  effectiveActions: ['new_preview'],
  unavailableReason: null,
}
const operation = {
  operationId,
  kind: 'apply',
  status: 'succeeded',
  stage: null,
  result: 'succeeded',
  subjectOperationId: null,
  targetResourceId: null,
  acceptedAt: now,
  completedAt: now,
  nextAllowedActions: ['view_history'],
}
const history = { items: [], nextCursor: null, hasMore: false }
const deactivation = {
  channelId,
  channelActive: true,
  channelUpdatedAt: now,
  operationId,
  status: 'checking',
  reason: null,
  subjectOperationId: null,
  recoveryOperationId: null,
  nextAction: 'get_state',
  acceptedAt: now,
  updatedAt: now,
  completedAt: null,
}

describe('rich menu admin API', () => {
  // テストケース: template、preview、state、operation、history、無効化の全HTTP手順を呼ぶ。
  // 期待値: same-origin protected clientへ正しいpath・method・bodyを一回ずつ渡す。
  test('maps every owner procedure without retries', async () => {
    const signal = new AbortController().signal
    const preview = {
      channelId,
      channelLabel: '通知',
      templateId: 'jp-link-one',
      templateVersion: 1,
      fields: [{ displayName: '案内', uri: 'https://example.com' }],
      image: {
        contentType: 'image/png',
        width: 2500,
        height: 843,
        digest: 'b'.repeat(64),
        base64: 'aGVsbG8=',
      },
      observation,
      warnings: [],
      confirmationToken: 'opaque',
      expiresAt: now,
    }
    const request = vi
      .fn<ProtectedHttpClient['request']>()
      .mockResolvedValueOnce(response(templates))
      .mockResolvedValueOnce(response(preview))
      .mockResolvedValueOnce(response(state))
      .mockResolvedValueOnce(response(operation))
      .mockResolvedValueOnce(response(operation))
      .mockResolvedValueOnce(response(history))
      .mockResolvedValueOnce(response(deactivation))
      .mockResolvedValueOnce(response(deactivation))
      .mockResolvedValueOnce(response(deactivation))
    const client = createRichMenuAdminApiClient({ request })
    await client.listTemplates({ signal })
    await client.createPreview(channelId, {
      templateId: 'jp-link-one',
      templateVersion: 1,
      channelRevision: now,
      fields: { area1: { displayName: '案内', uri: 'https://example.com' } },
    })
    await client.getState(channelId, { signal })
    await client.startOperation(channelId, {
      kind: 'apply',
      operationId,
      channelRevision: now,
      confirmationToken: 'opaque',
      templateId: 'jp-link-one',
      templateVersion: 1,
      fields: { area1: { displayName: '案内', uri: 'https://example.com' } },
    })
    await client.getOperation(operationId, { signal })
    await client.getHistory(channelId, 'opaque cursor', { signal })
    await client.getDeactivation(channelId, { signal })
    await client.startDeactivation(channelId, { operationId, expectedUpdatedAt: now })
    await client.recheckDeactivation(channelId, {
      operationId,
      recoveryOperationId: recoveryId,
      expectedUpdatedAt: now,
    })
    expect(request).toHaveBeenCalledTimes(9)
    expect(request.mock.calls.map(([input]) => input)).toEqual([
      { path: '/api/line/rich-menus/templates/', method: 'GET', signal },
      {
        path: `/api/line/rich-menus/channels/${channelId}/preview/`,
        method: 'POST',
        body: expect.objectContaining({ templateId: 'jp-link-one' }) as unknown,
      },
      { path: `/api/line/rich-menus/channels/${channelId}/state/`, method: 'GET', signal },
      {
        path: `/api/line/rich-menus/channels/${channelId}/operations/`,
        method: 'POST',
        body: expect.objectContaining({ operationId }) as unknown,
      },
      { path: `/api/line/rich-menus/operations/${operationId}/`, method: 'GET', signal },
      {
        path: `/api/line/rich-menus/channels/${channelId}/history/?limit=20&cursor=opaque+cursor`,
        method: 'GET',
        signal,
      },
      { path: `/api/line/channels/${channelId}/deactivation/`, method: 'GET', signal },
      {
        path: `/api/line/channels/${channelId}/deactivation/`,
        method: 'POST',
        body: { operationId, expectedUpdatedAt: now },
      },
      {
        path: `/api/line/channels/${channelId}/deactivation/recheck/`,
        method: 'POST',
        body: { operationId, recoveryOperationId: recoveryId, expectedUpdatedAt: now },
      },
    ])
  })

  // テストケース: GETと秘密値を含むPOSTがnetwork failureになる。
  // 期待値: GETは古い表示を破棄するload_failed、POSTは結果を推測しないrefresh_requiredとなり秘密を保持しない。
  test('classifies GET and POST network uncertainty safely', async () => {
    const secret = 'must-not-survive'
    const client = createRichMenuAdminApiClient({
      request: vi.fn().mockRejectedValue(new Error(secret)),
    })
    await expect(client.getState(channelId)).rejects.toMatchObject({ outcome: 'load_failed' })
    let caught: unknown
    try {
      await client.startOperation(channelId, {
        kind: 'apply',
        operationId,
        channelRevision: now,
        confirmationToken: secret,
        templateId: 'jp-link-one',
        templateVersion: 1,
        fields: { area1: { displayName: '案内', uri: 'https://example.com' } },
      })
    } catch (error) {
      caught = error
    }
    expect(caught).toBeInstanceOf(RichMenuAdminApiError)
    expect(caught).toMatchObject({ outcome: 'refresh_required' })
    expect(JSON.stringify(caught)).not.toContain(secret)
    expect(String(caught)).not.toContain(secret)
  })
})

describe('現在閲覧API', () => {
  const current = currentFixtures.managed
  // テストケース: channelとtimezone付きrevisionを指定して現在表示を読む。
  // 期待値: AbortSignalを付けた単発GETだけを送信し、DTOを検証して返す。
  test('gets the current result once with revision and read signal', async () => {
    const signal = new AbortController().signal
    const request = vi.fn<ProtectedHttpClient['request']>().mockResolvedValue(response(current))
    const api = createRichMenuAdminApiClient({ request })
    await expect(api.getCurrent(channelId, current.channelRevision, { signal })).resolves.toEqual(
      current,
    )
    expect(request.mock.calls).toEqual([
      [
        {
          path: `/api/line/rich-menus/channels/${channelId}/current/?channelRevision=${encodeURIComponent(current.channelRevision)}`,
          method: 'GET',
          signal,
        },
      ],
    ])
  })

  // テストケース: 閲覧競合・stale・認証・認可・利用不能のHTTP応答を受ける。
  // 期待値: 安全なcodeとstatusを維持し、再試行や旧画面へのfallbackを行わない。
  test.each(currentErrors)(
    'classifies current HTTP $status without retry',
    async ({ status, body }) => {
      const request = vi
        .fn<ProtectedHttpClient['request']>()
        .mockResolvedValue(response(body, status))
      const api = createRichMenuAdminApiClient({ request })
      await expect(api.getCurrent(channelId, current.channelRevision)).rejects.toMatchObject({
        error: { code: body.error.code },
        httpStatus: status,
        outcome: 'load_failed',
      })
      expect(request).toHaveBeenCalledTimes(1)
    },
  )

  // テストケース: 閲覧限定のcurrent_changedを既存mutation用parserへ渡す。
  // 期待値: 既存mutation契約では未知codeとして拒否する。
  test('keeps current_changed scoped to reads', () => {
    expect(
      parseRichMenuError({ error: { code: 'current_changed', nextAllowedActions: [] } }).ok,
    ).toBe(false)
  })

  // テストケース: 401とread中止を既存protected clientを介して受ける。
  // 期待値: 401で失効通知を一回呼び、中止はabortedとして保持する。
  test('notifies invalid session and preserves abort classification', async () => {
    const onSessionInvalid = vi.fn()
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        response({ error: { code: 'authentication_required', nextAllowedActions: [] } }, 401),
      )
    const api = createRichMenuAdminApiClient(
      createProtectedHttpClient({ fetch: fetchMock, onSessionInvalid }),
    )
    await expect(api.getCurrent(channelId, current.channelRevision)).rejects.toMatchObject({
      error: { code: 'authentication_required' },
    })
    expect(onSessionInvalid).toHaveBeenCalledTimes(1)
    const controller = new AbortController()
    fetchMock.mockImplementationOnce(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('safe abort', 'AbortError')),
          )
        }),
    )
    const pending = api.getCurrent(channelId, current.channelRevision, {
      signal: controller.signal,
    })
    controller.abort()
    await expect(pending).rejects.toMatchObject({ error: { code: 'aborted' } })
    expect(onSessionInvalid).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  // テストケース: 不正な引数や別チャネルの応答・未知エラーを受ける。
  // 期待値: 入力不正は通信せず、応答不正は秘密を含まないprotocol errorへ変換する。
  test('validates input and response without retaining unsafe data', async () => {
    const request = vi
      .fn<ProtectedHttpClient['request']>()
      .mockResolvedValue(response({ ...current, channelId: operationId }))
    const api = createRichMenuAdminApiClient({ request })
    await expect(api.getCurrent('bad', current.channelRevision)).rejects.toMatchObject({
      error: { code: 'protocol_error' },
    })
    await expect(api.getCurrent(channelId, '2026-10-18T00:00:00')).rejects.toMatchObject({
      error: { code: 'protocol_error' },
    })
    expect(request).not.toHaveBeenCalled()
    await expect(api.getCurrent(channelId, current.channelRevision)).rejects.toMatchObject({
      error: { code: 'protocol_error' },
    })
    const secret = 'unsafe-image-url-canary'
    request.mockResolvedValueOnce(response({ error: { code: secret, detail: secret } }, 503))
    try {
      await api.getCurrent(channelId, current.channelRevision)
    } catch (error) {
      expect(JSON.stringify(error)).not.toContain(secret)
      expect(String(error)).not.toContain(secret)
      expect(error).toMatchObject({ error: { code: 'protocol_error' } })
    }
    expect(request).toHaveBeenCalledTimes(2)
  })
})
