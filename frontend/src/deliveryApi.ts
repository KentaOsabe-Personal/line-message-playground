import {
  parseDeliveryChannelChoices,
  parseDeliveryRecipientChoices,
  parseErrorResponse,
  parseLinkedDeliveryStatus,
  parseLinkedPreviewResponse,
} from './deliveryDto'
import type {
  DeliveryChannelChoice,
  DeliveryRecipientChoice,
  LinkedDeliveryStatus,
  LinkedPreviewResponse,
  Parsed,
  SafeError,
} from './deliveryDto'
import { createProtectedHttpClient, ProtectedHttpClientError } from './httpApi'
import type { ProtectedHttpClient } from './httpApi'

export class DeliveryApiError extends Error {
  constructor(public readonly error: SafeError, public readonly httpStatus?: number) {
    super(error.summary)
    this.name = 'DeliveryApiError'
  }
}

export type LinkedPreviewRequest = {
  channelId: string
  recipientId: string
  subject: string
  body: string
  receiptRequested: boolean
}
export type LinkedSendDeliveryRequest = LinkedPreviewRequest & {
  operationId: string
  confirmationToken: string
}

export interface LinkedDeliveryApiClient {
  listChannels(): Promise<DeliveryChannelChoice[]>
  listRecipients(channelId: string): Promise<DeliveryRecipientChoice[]>
  preview(input: LinkedPreviewRequest): Promise<LinkedPreviewResponse>
  send(input: LinkedSendDeliveryRequest): Promise<LinkedDeliveryStatus>
  checkStatus(operationId: string): Promise<LinkedDeliveryStatus>
}

const canonicalUuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const linkedRequestError = () => new DeliveryApiError({
  code: 'protocol_error',
  summary: '要求形式を確認できません。',
})

function hasExactInputKeys(input: object, keys: readonly string[]): boolean {
  const actual = Object.keys(input).sort()
  const expected = [...keys].sort()
  return actual.length === expected.length && actual.every((key, index) => key === expected[index])
}

function assertCanonicalUuid(value: string): void {
  if (!canonicalUuidPattern.test(value)) throw linkedRequestError()
}

function assertLinkedPreviewRequest(input: LinkedPreviewRequest): void {
  if (
    typeof input !== 'object' ||
    input === null ||
    !hasExactInputKeys(input, ['channelId', 'recipientId', 'subject', 'body', 'receiptRequested']) ||
    typeof input.channelId !== 'string' ||
    !canonicalUuidPattern.test(input.channelId) ||
    typeof input.recipientId !== 'string' ||
    !canonicalUuidPattern.test(input.recipientId) ||
    typeof input.subject !== 'string' ||
    typeof input.body !== 'string' ||
    typeof input.receiptRequested !== 'boolean'
  ) throw linkedRequestError()
}

function assertLinkedSendRequest(input: LinkedSendDeliveryRequest): void {
  if (
    typeof input !== 'object' ||
    input === null ||
    !hasExactInputKeys(input, [
      'channelId',
      'recipientId',
      'subject',
      'body',
      'receiptRequested',
      'operationId',
      'confirmationToken',
    ])
  ) throw linkedRequestError()
  const previewInput: LinkedPreviewRequest = {
    channelId: input.channelId,
    recipientId: input.recipientId,
    subject: input.subject,
    body: input.body,
    receiptRequested: input.receiptRequested,
  }
  assertLinkedPreviewRequest(previewInput)
  if (
    typeof input.operationId !== 'string' ||
    !canonicalUuidPattern.test(input.operationId) ||
    typeof input.confirmationToken !== 'string' ||
    input.confirmationToken.length === 0
  ) throw linkedRequestError()
}

export function createLinkedDeliveryApiClient(
  protectedClient: ProtectedHttpClient = createProtectedHttpClient(),
): LinkedDeliveryApiClient {
  const request = <T>(
    path: string,
    method: 'GET' | 'POST',
    parse: (value: unknown) => Parsed<T>,
    body?: unknown,
  ) => requestProtected(protectedClient, path, method, parse, body)
  const checkStatus = async (operationId: string) => {
    assertCanonicalUuid(operationId)
    return await request(
      `/api/deliveries/${operationId}/status/`,
      'POST',
      parseLinkedDeliveryStatus,
    )
  }
  return Object.freeze({
    listChannels: () => request(
      '/api/deliveries/targets/channels/',
      'GET',
      parseDeliveryChannelChoices,
    ),
    listRecipients: async (channelId: string) => {
      assertCanonicalUuid(channelId)
      return await request(
        `/api/deliveries/targets/channels/${channelId}/recipients/`,
        'GET',
        parseDeliveryRecipientChoices,
      )
    },
    preview: async (input: LinkedPreviewRequest) => {
      assertLinkedPreviewRequest(input)
      return await request('/api/deliveries/preview/', 'POST', parseLinkedPreviewResponse, input)
    },
    send: async (input: LinkedSendDeliveryRequest) => {
      assertLinkedSendRequest(input)
      try {
        return await request('/api/deliveries/', 'POST', parseLinkedDeliveryStatus, input)
      } catch (error) {
        if (
          error instanceof DeliveryApiError &&
          error.error.code === 'network_error' &&
          error.httpStatus === undefined
        ) {
          return checkStatus(input.operationId)
        }
        throw error
      }
    },
    checkStatus,
  })
}

const networkError: SafeError = { code: 'network_error', summary: 'Backendに接続できません。' }

async function requestProtected<T>(
  client: ProtectedHttpClient,
  url: string,
  method: 'GET' | 'POST',
  parse: (value: unknown) => Parsed<T>,
  body?: unknown,
): Promise<T> {
  let response: Response
  try {
    response = await client.request(
      body === undefined
        ? { path: url, method }
        : { path: url, method, body },
    )
  } catch (error) {
    if (error instanceof ProtectedHttpClientError) {
      throw new DeliveryApiError({ code: error.code, summary: error.summary })
    }
    throw new DeliveryApiError(networkError)
  }

  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new DeliveryApiError({ code: 'protocol_error', summary: '応答形式を確認できません。' }, response.status)
  }

  if (!response.ok) {
    const parsedError = parseErrorResponse(payload)
    throw new DeliveryApiError(parsedError.ok ? parsedError.value : parsedError.error, response.status)
  }
  const parsed = parse(payload)
  if (!parsed.ok) throw new DeliveryApiError(parsed.error, response.status)
  return parsed.value
}
