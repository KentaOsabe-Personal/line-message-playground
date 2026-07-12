import { parseDeliveryResult, parseErrorResponse, parsePreviewResponse } from './deliveryDto'
import type { DeliveryResult, Parsed, PreviewResponse, SafeError } from './deliveryDto'

export type PreviewRequest = { subject: string; body: string }
export type SendDeliveryRequest = PreviewRequest & { operationId: string; confirmationToken: string }

export class DeliveryApiError extends Error {
  constructor(public readonly error: SafeError, public readonly httpStatus?: number) {
    super(error.summary)
    this.name = 'DeliveryApiError'
  }
}

export interface DeliveryApiClient {
  preview(input: PreviewRequest): Promise<PreviewResponse>
  send(input: SendDeliveryRequest): Promise<DeliveryResult>
  checkStatus(operationId: string): Promise<DeliveryResult>
}

const networkError: SafeError = { code: 'network_error', summary: 'Backendに接続できません。' }

async function request<T>(url: string, parse: (value: unknown) => Parsed<T>, body?: unknown): Promise<T> {
  let response: Response
  const options: RequestInit = { method: 'POST' }
  if (body !== undefined) {
    options.headers = { 'Content-Type': 'application/json' }
    options.body = JSON.stringify(body)
  }
  try {
    response = await fetch(url, options)
  } catch {
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

export function createDeliveryApiClient(): DeliveryApiClient {
  return {
    preview: (input) => request('/api/deliveries/preview/', parsePreviewResponse, input),
    send: (input) => request('/api/deliveries/', parseDeliveryResult, input),
    checkStatus: (operationId) => request(`/api/deliveries/${encodeURIComponent(operationId)}/status/`, parseDeliveryResult),
  }
}
