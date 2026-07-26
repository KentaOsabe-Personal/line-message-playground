import { afterEach, describe, expect, test, vi } from 'vitest'

import {
  createDeliveryApiClient,
  createLinkedDeliveryApiClient,
  DeliveryApiError,
} from '../src/deliveryApi'
import { createProtectedHttpClient } from '../src/httpApi'
import type { ProtectedHttpClient } from '../src/httpApi'

const jsonResponse = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json' },
})

const createClient = () => createDeliveryApiClient(createProtectedHttpClient({
  readCookie: () => 'csrftoken=csrf-value',
}))
const operationId = '323e4567-e89b-12d3-a456-426614174000'
const channelId = '123e4567-e89b-12d3-a456-426614174000'
const recipientId = '223e4567-e89b-12d3-a456-426614174000'
const acceptedAt = '2026-07-26T12:00:00+09:00'
const linkedStatus = {
  operationId,
  snapshot: {
    channelId,
    channelLabel: '配信チャネル',
    recipientId,
    channelActive: true,
    recipientEnabled: true,
    friendshipState: 'friend',
  },
  status: 'processing',
  acceptedAt,
  completedAt: null,
  lineRequestId: null,
  receipt: {
    requested: false,
    status: 'not_requested',
    expiresAt: null,
    confirmedAt: null,
  },
}
const linkedPreview = {
  channelId,
  channelLabel: '配信チャネル',
  recipientId,
  recipientDisplayName: '受信者',
  friendshipState: 'friend',
  formattedText: '【件名】\n\n本文',
  receiptRequested: false,
  receiptExpiresAt: null,
  confirmationToken: 'opaque-confirmation',
}
const linkedInput = {
  channelId,
  recipientId,
  subject: '件名',
  body: '本文',
  receiptRequested: false,
}

describe('DeliveryApiClient', () => {
  afterEach(() => vi.restoreAllMocks())

  // テストケース: preview・send・statusを型付きJSONとして相対URLへ送る。
  // 期待値: 各公開endpointだけを呼び、妥当なDTOを返す。
  test('calls the delivery endpoints with JSON requests', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ formattedText: '【件名】\n\n本文', confirmationToken: 'token' }))
      .mockResolvedValueOnce(jsonResponse({ status: 'processing', operationId, acceptedAt: 'a', expiresAt: 'e' }, 202))
      .mockResolvedValueOnce(jsonResponse({ status: 'succeeded', operationId, acceptedAt: 'a', completedAt: 'c', lineRequestId: null }))
    const client = createClient()

    await client.preview({ subject: '件名', body: '本文' })
    await client.send({ subject: '件名', body: '本文', operationId, confirmationToken: 'token' })
    await client.checkStatus(operationId)

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/deliveries/preview/',
      '/api/deliveries/',
      `/api/deliveries/${operationId}/status/`,
    ])
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': 'csrf-value' },
    })
    expect(fetchMock.mock.calls[2]?.[1]).toMatchObject({ method: 'POST' })
    expect(fetchMock.mock.calls[2]?.[1]).not.toHaveProperty('body')
    expect(fetchMock.mock.calls[2]?.[1]).toMatchObject({
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': 'csrf-value' },
    })
  })

  // テストケース: 配信3操作を共通の保護HTTP clientへ委譲する。
  // 期待値: unsafe要求が相対path・POST・bodyを保持してCSRF/session保護境界を通る。
  test('routes every delivery operation through the protected HTTP client', async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ formattedText: '【件名】\n\n本文', confirmationToken: 'token' }))
      .mockResolvedValueOnce(jsonResponse({ status: 'processing', operationId, acceptedAt: 'a', expiresAt: 'e' }, 202))
      .mockResolvedValueOnce(jsonResponse({ status: 'succeeded', operationId, acceptedAt: 'a', completedAt: 'c', lineRequestId: null }))
    const protectedClient: ProtectedHttpClient = { request }
    const client = createDeliveryApiClient(protectedClient)

    await client.preview({ subject: '件名', body: '本文' })
    await client.send({ subject: '件名', body: '本文', operationId, confirmationToken: 'token' })
    await client.checkStatus(operationId)

    expect(request).toHaveBeenNthCalledWith(1, {
      path: '/api/deliveries/preview/',
      method: 'POST',
      body: { subject: '件名', body: '本文' },
    })
    expect(request).toHaveBeenNthCalledWith(2, {
      path: '/api/deliveries/',
      method: 'POST',
      body: { subject: '件名', body: '本文', operationId, confirmationToken: 'token' },
    })
    expect(request).toHaveBeenNthCalledWith(3, {
      path: `/api/deliveries/${operationId}/status/`,
      method: 'POST',
      body: undefined,
    })
  })

  // テストケース: 非2xxの共通errorと、成功statusだが未知shapeの応答を受け取る。
  // 期待値: 前者は安全なAPI error、後者はprotocol errorとして拒否する。
  test('maps error envelopes and invalid response shapes safely', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ error: { code: 'validation_error', summary: '入力内容を確認してください。', fields: { subject: ['入力値が不正です。'] } } }, 400))
      .mockResolvedValueOnce(jsonResponse({ status: 'succeeded', target: 'secret' }))
    const client = createClient()

    await expect(client.preview({ subject: '', body: '本文' })).rejects.toEqual(new DeliveryApiError({ code: 'validation_error', summary: '入力内容を確認してください。', fields: { subject: ['入力値が不正です。'] } }, 400))
    await expect(client.send({ subject: '件名', body: '本文', operationId: 'id-1', confirmationToken: 'token' })).rejects.toMatchObject({ error: { code: 'protocol_error' } })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  // テストケース: sendがnetwork errorになった後に状態を確認する。
  // 期待値: 同じoperation IDのstatusだけを呼び、sendの自動再送を行わない。
  test('checks status without automatically retrying send after a network error', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockRejectedValueOnce(new TypeError('network'))
      .mockResolvedValueOnce(jsonResponse({ error: { code: 'operation_not_found', summary: '送信操作を確認できませんでした。' } }, 404))
    const client = createClient()
    const request = { subject: '件名', body: '本文', operationId: 'id-1', confirmationToken: 'token' }

    await expect(client.send(request)).rejects.toMatchObject({ error: { code: 'network_error' } })
    await expect(client.checkStatus(request.operationId)).rejects.toMatchObject({ error: { code: 'operation_not_found' }, httpStatus: 404 })

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/deliveries/id-1/status/')
  })

  // テストケース: linked配信のtarget取得・preview・send・statusを保護HTTP境界へ委譲する。
  // 期待値: GETは相対path、unsafe操作は正確な公開request DTOで呼ばれ、strict parser済みDTOだけを返す。
  test('calls every linked delivery endpoint with strict relative requests', async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [{
        channelId,
        label: '配信チャネル',
        active: true,
        deliveryAvailable: true,
        unavailableReason: null,
      }] }))
      .mockResolvedValueOnce(jsonResponse({ items: [{
        recipientId,
        displayName: '受信者',
        enabled: true,
        friendshipState: 'friend',
        deliveryAvailable: true,
        unavailableReason: null,
      }] }))
      .mockResolvedValueOnce(jsonResponse(linkedPreview))
      .mockResolvedValueOnce(jsonResponse(linkedStatus, 202))
      .mockResolvedValueOnce(jsonResponse(linkedStatus))
    const client = createLinkedDeliveryApiClient({ request } as ProtectedHttpClient)

    await expect(client.listChannels()).resolves.toHaveLength(1)
    await expect(client.listRecipients(channelId)).resolves.toHaveLength(1)
    await expect(client.preview(linkedInput)).resolves.toEqual(linkedPreview)
    await expect(client.send({
      ...linkedInput,
      operationId,
      confirmationToken: 'opaque-confirmation',
    })).resolves.toEqual(linkedStatus)
    await expect(client.checkStatus(operationId)).resolves.toEqual(linkedStatus)

    expect(request.mock.calls).toEqual([
      [{ path: '/api/deliveries/targets/channels/', method: 'GET' }],
      [{ path: `/api/deliveries/targets/channels/${channelId}/recipients/`, method: 'GET' }],
      [{ path: '/api/deliveries/preview/', method: 'POST', body: linkedInput }],
      [{
        path: '/api/deliveries/',
        method: 'POST',
        body: { ...linkedInput, operationId, confirmationToken: 'opaque-confirmation' },
      }],
      [{ path: `/api/deliveries/${operationId}/status/`, method: 'POST' }],
    ])
  })

  // テストケース: linked target GETとpreview POSTを既存protected clientで実行する。
  // 期待値: 両方がsame-origin cookie境界を使い、unsafe POSTだけにexact CSRF headerを付与する。
  test('reuses same-origin credentials and exact CSRF protection for linked requests', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(jsonResponse(linkedPreview))
    const client = createLinkedDeliveryApiClient(createProtectedHttpClient({
      readCookie: () => 'csrftoken=csrf-value',
    }))

    await client.listChannels()
    await client.preview(linkedInput)

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/deliveries/targets/channels/', {
      method: 'GET',
      credentials: 'same-origin',
    })
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/deliveries/preview/', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': 'csrf-value',
      },
      body: JSON.stringify(linkedInput),
    })
  })

  // テストケース: linked sendの通信が曖昧な結果になった。
  // 期待値: sendを再実行せず、同じoperation IDのstatusを一度だけ確認して保存済み結果を返す。
  test('resolves an ambiguous linked send through status without retrying send', async () => {
    const request = vi.fn()
      .mockRejectedValueOnce(new Error('secret transport detail'))
      .mockResolvedValueOnce(jsonResponse(linkedStatus))
    const client = createLinkedDeliveryApiClient({ request } as ProtectedHttpClient)

    await expect(client.send({
      ...linkedInput,
      operationId,
      confirmationToken: 'opaque-confirmation',
    })).resolves.toEqual(linkedStatus)

    expect(request).toHaveBeenCalledTimes(2)
    expect(request.mock.calls.map(([input]) => input)).toEqual([
      {
        path: '/api/deliveries/',
        method: 'POST',
        body: { ...linkedInput, operationId, confirmationToken: 'opaque-confirmation' },
      },
      { path: `/api/deliveries/${operationId}/status/`, method: 'POST' },
    ])
  })

  // テストケース: linked sendがHTTP非2xxのsafe network_error envelopeを返す。
  // 期待値: transport ambiguityとは扱わず元のHTTP errorを返し、send再実行もstatus確認も行わない。
  test('does not check status for an HTTP network error envelope', async () => {
    const request = vi.fn().mockResolvedValueOnce(jsonResponse({
      error: {
        code: 'network_error',
        summary: '要求を処理できませんでした。',
      },
    }, 503))
    const client = createLinkedDeliveryApiClient({ request } as ProtectedHttpClient)

    await expect(client.send({
      ...linkedInput,
      operationId,
      confirmationToken: 'opaque-confirmation',
    })).rejects.toEqual(new DeliveryApiError({
      code: 'network_error',
      summary: '要求を処理できませんでした。',
    }, 503))

    expect(request).toHaveBeenCalledTimes(1)
    expect(request).toHaveBeenCalledWith({
      path: '/api/deliveries/',
      method: 'POST',
      body: { ...linkedInput, operationId, confirmationToken: 'opaque-confirmation' },
    })
  })

  // テストケース: request識別子が非canonical、または余剰の秘密fieldを含む入力を渡す。
  // 期待値: networkへ送らずprotocol errorへ縮約し、入力値や秘密canaryをerrorへ露出しない。
  test('rejects invalid linked request DTOs before protected transport', async () => {
    const request = vi.fn()
    const client = createLinkedDeliveryApiClient({ request } as ProtectedHttpClient)
    const invalid = { ...linkedInput, accessToken: 'token-canary' }

    await expect(client.preview(invalid)).rejects.toEqual(new DeliveryApiError({
      code: 'protocol_error',
      summary: '要求形式を確認できません。',
    }))
    await expect(client.listRecipients(channelId.toUpperCase())).rejects.toMatchObject({
      error: { code: 'protocol_error' },
    })
    expect(request).not.toHaveBeenCalled()
  })

  // テストケース: 成功応答に秘密canary、非2xx応答に不正なerror shapeが含まれる。
  // 期待値: どちらもsafe protocol errorとなり、生payloadは例外message・公開errorへ残らない。
  test('converts unsafe linked response payloads into protocol errors', async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ ...linkedPreview, channelSecret: 'secret-canary' }))
      .mockResolvedValueOnce(jsonResponse({ error: { code: 'unknown', summary: 'token-canary', accessToken: 'token-canary' } }, 500))
    const client = createLinkedDeliveryApiClient({ request } as ProtectedHttpClient)

    const previewError = await client.preview(linkedInput).catch((error: unknown) => error)
    const statusError = await client.checkStatus(operationId).catch((error: unknown) => error)

    expect(previewError).toEqual(new DeliveryApiError({
      code: 'protocol_error',
      summary: '応答形式を確認できません。',
    }, 200))
    expect(statusError).toEqual(new DeliveryApiError({
      code: 'protocol_error',
      summary: '応答形式を確認できません。',
    }, 500))
    expect(JSON.stringify([previewError, statusError])).not.toContain('token-canary')
  })
})
