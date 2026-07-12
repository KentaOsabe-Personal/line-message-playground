export type SafeError = { code: string; summary: string; fields?: Record<string, string[]> }

export type PreviewResponse = { formattedText: string; confirmationToken: string }
export type DeliveryProcessing = { status: 'processing'; operationId: string; acceptedAt: string; expiresAt: string }
export type DeliverySuccess = { status: 'succeeded'; operationId: string; acceptedAt: string; completedAt: string; lineRequestId: string | null }
export type DeliveryFailure = { status: 'failed' | 'unknown'; operationId: string; acceptedAt: string; completedAt: string; error: SafeError; lineRequestId: string | null }
export type DeliveryResult = DeliveryProcessing | DeliverySuccess | DeliveryFailure
export type Parsed<T> = { ok: true; value: T } | { ok: false; error: SafeError }

const protocolError = (): Parsed<never> => ({ ok: false, error: { code: 'protocol_error', summary: '応答形式を確認できません。' } })
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const hasExactKeys = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).length === keys.length && keys.every((key) => key in value)
const isString = (value: unknown): value is string => typeof value === 'string'
const isNullableString = (value: unknown): value is string | null => value === null || isString(value)
const isStringArray = (value: unknown): value is string[] => Array.isArray(value) && value.every(isString)
const isFields = (value: unknown): value is Record<string, string[]> => isRecord(value) && Object.values(value).every(isStringArray)
const isSafeError = (value: unknown): value is SafeError => {
  if (!isRecord(value) || !isString(value.code) || !isString(value.summary)) return false
  const keys = Object.keys(value)
  return keys.every((key) => ['code', 'summary', 'fields'].includes(key)) && keys.includes('code') && keys.includes('summary') && (!('fields' in value) || isFields(value.fields))
}

export function parsePreviewResponse(value: unknown): Parsed<PreviewResponse> {
  if (!isRecord(value) || !hasExactKeys(value, ['formattedText', 'confirmationToken']) || !isString(value.formattedText) || !isString(value.confirmationToken)) return protocolError()
  return { ok: true, value: { formattedText: value.formattedText, confirmationToken: value.confirmationToken } }
}

export function parseErrorResponse(value: unknown): Parsed<SafeError> {
  if (!isRecord(value) || !hasExactKeys(value, ['error']) || !isSafeError(value.error)) return protocolError()
  return { ok: true, value: value.error }
}

export function parseDeliveryResult(value: unknown): Parsed<DeliveryResult> {
  if (!isRecord(value) || !isString(value.status) || !isString(value.operationId) || !isString(value.acceptedAt)) return protocolError()
  if (value.status === 'processing' && hasExactKeys(value, ['status', 'operationId', 'acceptedAt', 'expiresAt']) && isString(value.expiresAt)) return { ok: true, value: value as DeliveryProcessing }
  if (value.status === 'succeeded' && hasExactKeys(value, ['status', 'operationId', 'acceptedAt', 'completedAt', 'lineRequestId']) && isString(value.completedAt) && isNullableString(value.lineRequestId)) return { ok: true, value: value as DeliverySuccess }
  if ((value.status === 'failed' || value.status === 'unknown') && hasExactKeys(value, ['status', 'operationId', 'acceptedAt', 'completedAt', 'error', 'lineRequestId']) && isString(value.completedAt) && isSafeError(value.error) && isNullableString(value.lineRequestId)) return { ok: true, value: value as DeliveryFailure }
  return protocolError()
}
