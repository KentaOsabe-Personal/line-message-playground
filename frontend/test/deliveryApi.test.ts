import { afterEach, describe, expect, test, vi } from 'vitest'

import { createDeliveryApiClient, DeliveryApiError } from '../src/deliveryApi'
import { createProtectedHttpClient } from '../src/httpApi'
import type { ProtectedHttpClient } from '../src/httpApi'

const jsonResponse = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json' },
})

const createClient = () => createDeliveryApiClient(createProtectedHttpClient({
  readCookie: () => 'csrftoken=csrf-value',
}))

describe('DeliveryApiClient', () => {
  afterEach(() => vi.restoreAllMocks())

  // テストケース: preview・send・statusを型付きJSONとして相対URLへ送る。
  // 期待値: 各公開endpointだけを呼び、妥当なDTOを返す。
  test('calls the delivery endpoints with JSON requests', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ formattedText: '【件名】\n\n本文', confirmationToken: 'token' }))
      .mockResolvedValueOnce(jsonResponse({ status: 'processing', operationId: 'id-1', acceptedAt: 'a', expiresAt: 'e' }, 202))
      .mockResolvedValueOnce(jsonResponse({ status: 'succeeded', operationId: 'id-1', acceptedAt: 'a', completedAt: 'c', lineRequestId: null }))
    const client = createClient()

    await client.preview({ subject: '件名', body: '本文' })
    await client.send({ subject: '件名', body: '本文', operationId: 'id-1', confirmationToken: 'token' })
    await client.checkStatus('id-1')

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/deliveries/preview/',
      '/api/deliveries/',
      '/api/deliveries/id-1/status/',
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
      .mockResolvedValueOnce(jsonResponse({ status: 'processing', operationId: 'id-1', acceptedAt: 'a', expiresAt: 'e' }, 202))
      .mockResolvedValueOnce(jsonResponse({ status: 'succeeded', operationId: 'id-1', acceptedAt: 'a', completedAt: 'c', lineRequestId: null }))
    const protectedClient: ProtectedHttpClient = { request }
    const client = createDeliveryApiClient(protectedClient)

    await client.preview({ subject: '件名', body: '本文' })
    await client.send({ subject: '件名', body: '本文', operationId: 'id-1', confirmationToken: 'token' })
    await client.checkStatus('id-1')

    expect(request).toHaveBeenNthCalledWith(1, {
      path: '/api/deliveries/preview/',
      method: 'POST',
      body: { subject: '件名', body: '本文' },
    })
    expect(request).toHaveBeenNthCalledWith(2, {
      path: '/api/deliveries/',
      method: 'POST',
      body: { subject: '件名', body: '本文', operationId: 'id-1', confirmationToken: 'token' },
    })
    expect(request).toHaveBeenNthCalledWith(3, {
      path: '/api/deliveries/id-1/status/',
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
})
