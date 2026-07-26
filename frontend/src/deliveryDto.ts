export type DeliveryFailureCode =
  | 'configuration'
  | 'invalid_request'
  | 'authentication'
  | 'permission'
  | 'conflict'
  | 'rate_limited'
  | 'service_unavailable'
  | 'service_unknown'
  | 'timeout_unknown'
  | 'response_unknown'
  | 'processing_expired'
  | 'target_changed'
  | 'storage_unavailable'
  | 'unexpected'
export type SafeErrorCode =
  | DeliveryFailureCode
  | 'authentication_required'
  | 'invalid_line_proof'
  | 'owner_not_allowed'
  | 'owner_operation_blocked'
  | 'csrf_failed'
  | 'validation_error'
  | 'channel_not_found'
  | 'recipient_not_found'
  | 'stale_confirmation'
  | 'unlink_in_progress'
  | 'unlink_attempt_stale'
  | 'provider_mismatch'
  | 'channel_unavailable'
  | 'line_rate_limited'
  | 'line_unavailable'
  | 'confirmation_required'
  | 'confirmation_stale'
  | 'confirmation_expired'
  | 'operation_id_reused'
  | 'delivery_in_progress'
  | 'operation_not_found'
  | 'target_not_available'
  | 'target_not_deliverable'
  | 'protocol_error'
  | 'network_error'
  | 'csrf_missing'
export type SafeError = { code: SafeErrorCode; summary: string; fields?: Record<string, string[]> }

export type PreviewResponse = { formattedText: string; confirmationToken: string }
export type DeliveryProcessing = { status: 'processing'; operationId: string; acceptedAt: string; expiresAt: string }
export type DeliverySuccess = { status: 'succeeded'; operationId: string; acceptedAt: string; completedAt: string; lineRequestId: string | null }
export type DeliveryFailure = { status: 'failed' | 'unknown'; operationId: string; acceptedAt: string; completedAt: string; error: SafeError; lineRequestId: string | null }
export type DeliveryResult = DeliveryProcessing | DeliverySuccess | DeliveryFailure
export type Parsed<T> = { ok: true; value: T } | { ok: false; error: SafeError }

export type FriendshipState = 'friend' | 'not_friend' | 'unknown'
export type ChannelUnavailableReason = 'channel_inactive'
export type RecipientUnavailableReason =
  | ChannelUnavailableReason
  | 'recipient_disabled'
  | 'not_friend'
  | 'friendship_unknown'

export type DeliveryChannelChoice = {
  channelId: string
  label: string
  active: boolean
  deliveryAvailable: boolean
  unavailableReason: ChannelUnavailableReason | null
}

export type DeliveryRecipientChoice = {
  recipientId: string
  displayName: string
  enabled: boolean
  friendshipState: FriendshipState
  deliveryAvailable: boolean
  unavailableReason: RecipientUnavailableReason | null
}

export type LinkedPreviewResponse = {
  channelId: string
  channelLabel: string
  recipientId: string
  recipientDisplayName: string
  friendshipState: FriendshipState
  formattedText: string
  receiptRequested: boolean
  receiptExpiresAt: string | null
  confirmationToken: string
}

export type DeliverySnapshot = {
  channelId: string
  channelLabel: string
  recipientId: string
  channelActive: boolean
  recipientEnabled: boolean
  friendshipState: FriendshipState
}

export type ReceiptStatus = 'not_requested' | 'pending' | 'confirmed' | 'expired'
export type ReceiptState = {
  requested: boolean
  status: ReceiptStatus
  expiresAt: string | null
  confirmedAt: string | null
}
export type LinkedDeliveryStatus =
  | {
      operationId: string
      snapshot: DeliverySnapshot
      status: 'processing' | 'succeeded'
      acceptedAt: string
      completedAt: string | null
      lineRequestId: string | null
      receipt: ReceiptState
    }
  | {
      operationId: string
      snapshot: DeliverySnapshot
      status: 'failed' | 'unknown'
      acceptedAt: string
      completedAt: string
      lineRequestId: string | null
      receipt: ReceiptState
      error: SafeError
    }
export type DeliveryStatus = LinkedDeliveryStatus

const protocolError = (): Parsed<never> => ({ ok: false, error: { code: 'protocol_error', summary: '応答形式を確認できません。' } })
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const hasExactKeys = (value: Record<string, unknown>, keys: readonly string[]) => {
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  return actual.length === expected.length && actual.every((key, index) => key === expected[index])
}
const isString = (value: unknown): value is string => typeof value === 'string'
const isNonEmptyString = (value: unknown): value is string => isString(value) && value.length > 0
const isNullableString = (value: unknown): value is string | null => value === null || isString(value)
const canonicalUuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const isCanonicalUuid = (value: unknown): value is string => isString(value) && canonicalUuidPattern.test(value)
const timezoneDateTimePattern =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-](\d{2}):(\d{2}))$/
const isLeapYear = (year: number) => year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
const daysInMonth = (year: number, month: number) => [
  31,
  isLeapYear(year) ? 29 : 28,
  31,
  30,
  31,
  30,
  31,
  31,
  30,
  31,
  30,
  31,
][month - 1] ?? 0
const isTimezoneDateTime = (value: unknown): value is string => {
  if (!isString(value)) return false
  const match = timezoneDateTimePattern.exec(value)
  if (match === null) return false
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, offsetHourText, offsetMinuteText] = match
  const year = Number(yearText)
  const month = Number(monthText)
  const day = Number(dayText)
  const hour = Number(hourText)
  const minute = Number(minuteText)
  const second = Number(secondText)
  const offsetHour = offsetHourText === undefined ? 0 : Number(offsetHourText)
  const offsetMinute = offsetMinuteText === undefined ? 0 : Number(offsetMinuteText)
  return year >= 1 && year <= 9999 &&
    month >= 1 && month <= 12 &&
    day >= 1 && day <= daysInMonth(year, month) &&
    hour <= 23 &&
    minute <= 59 &&
    second <= 59 &&
    offsetHour <= 23 &&
    offsetMinute <= 59
}
const isNullableDateTime = (value: unknown): value is string | null => value === null || isTimezoneDateTime(value)
const isFriendshipState = (value: unknown): value is FriendshipState =>
  value === 'friend' || value === 'not_friend' || value === 'unknown'
const publicErrorFields = new Set([
  'channelId',
  'recipientId',
  'subject',
  'body',
  'receiptRequested',
  'operationId',
  'confirmationToken',
  'message',
])
const safeErrorCodes = new Set<string>([
  'configuration',
  'invalid_request',
  'authentication',
  'permission',
  'conflict',
  'rate_limited',
  'service_unavailable',
  'service_unknown',
  'timeout_unknown',
  'response_unknown',
  'processing_expired',
  'target_changed',
  'storage_unavailable',
  'unexpected',
  'authentication_required',
  'invalid_line_proof',
  'owner_not_allowed',
  'owner_operation_blocked',
  'csrf_failed',
  'validation_error',
  'channel_not_found',
  'recipient_not_found',
  'stale_confirmation',
  'unlink_in_progress',
  'unlink_attempt_stale',
  'provider_mismatch',
  'channel_unavailable',
  'line_rate_limited',
  'line_unavailable',
  'confirmation_required',
  'confirmation_stale',
  'confirmation_expired',
  'operation_id_reused',
  'delivery_in_progress',
  'operation_not_found',
  'target_not_available',
  'target_not_deliverable',
  'protocol_error',
  'network_error',
  'csrf_missing',
])
const isSafeErrorCode = (value: unknown): value is SafeErrorCode =>
  isString(value) && safeErrorCodes.has(value)
const failedDeliveryCodes = new Set<string>([
  'configuration',
  'invalid_request',
  'authentication',
  'permission',
  'conflict',
  'rate_limited',
  'service_unavailable',
  'target_changed',
  'storage_unavailable',
  'unexpected',
])
const unknownDeliveryCodes = new Set<string>([
  'service_unknown',
  'timeout_unknown',
  'response_unknown',
  'processing_expired',
])
const isFields = (value: unknown): value is Record<string, string[]> => {
  if (!isRecord(value)) return false
  return Object.entries(value).every(([key, messages]) =>
    publicErrorFields.has(key) &&
    Array.isArray(messages) &&
    messages.length > 0 &&
    messages.every(isNonEmptyString),
  )
}
const isSafeError = (value: unknown): value is SafeError => {
  if (!isRecord(value)) return false
  const keys = 'fields' in value ? ['code', 'summary', 'fields'] : ['code', 'summary']
  return hasExactKeys(value, keys) &&
    isSafeErrorCode(value.code) &&
    isNonEmptyString(value.summary) &&
    (!('fields' in value) || isFields(value.fields))
}
const copySafeError = (error: SafeError): SafeError => {
  if (error.fields === undefined) return { code: error.code, summary: error.summary }
  return {
    code: error.code,
    summary: error.summary,
    fields: Object.fromEntries(
      Object.entries(error.fields).map(([field, messages]) => [field, [...messages]]),
    ),
  }
}

function parseItemEnvelope<T>(
  value: unknown,
  parseItem: (item: unknown) => Parsed<T>,
): Parsed<T[]> {
  if (!isRecord(value) || !hasExactKeys(value, ['items']) || !Array.isArray(value.items)) return protocolError()
  const items: T[] = []
  for (const item of value.items) {
    const parsed = parseItem(item)
    if (!parsed.ok) return parsed
    items.push(parsed.value)
  }
  return { ok: true, value: items }
}

const parseDeliveryChannelChoice = (value: unknown): Parsed<DeliveryChannelChoice> => {
  if (!isRecord(value) || !hasExactKeys(value, [
    'channelId',
    'label',
    'active',
    'deliveryAvailable',
    'unavailableReason',
  ])) return protocolError()
  if (
    !isCanonicalUuid(value.channelId) ||
    !isNonEmptyString(value.label) ||
    typeof value.active !== 'boolean' ||
    typeof value.deliveryAvailable !== 'boolean' ||
    (value.unavailableReason !== null && value.unavailableReason !== 'channel_inactive')
  ) return protocolError()
  if (
    (value.deliveryAvailable && (!value.active || value.unavailableReason !== null)) ||
    (!value.deliveryAvailable && (value.active || value.unavailableReason !== 'channel_inactive'))
  ) return protocolError()
  return { ok: true, value: {
    channelId: value.channelId,
    label: value.label,
    active: value.active,
    deliveryAvailable: value.deliveryAvailable,
    unavailableReason: value.unavailableReason,
  } }
}

const parseDeliveryRecipientChoice = (value: unknown): Parsed<DeliveryRecipientChoice> => {
  if (!isRecord(value) || !hasExactKeys(value, [
    'recipientId',
    'displayName',
    'enabled',
    'friendshipState',
    'deliveryAvailable',
    'unavailableReason',
  ])) return protocolError()
  const isUnavailableReason = (reason: unknown): reason is RecipientUnavailableReason | null =>
    reason === null ||
    reason === 'channel_inactive' ||
    reason === 'recipient_disabled' ||
    reason === 'not_friend' ||
    reason === 'friendship_unknown'
  if (
    !isCanonicalUuid(value.recipientId) ||
    !isNonEmptyString(value.displayName) ||
    typeof value.enabled !== 'boolean' ||
    !isFriendshipState(value.friendshipState) ||
    typeof value.deliveryAvailable !== 'boolean' ||
    !isUnavailableReason(value.unavailableReason)
  ) return protocolError()
  if (value.deliveryAvailable && (!value.enabled || value.friendshipState !== 'friend' || value.unavailableReason !== null)) {
    return protocolError()
  }
  if (!value.deliveryAvailable && value.unavailableReason === null) return protocolError()
  if (
    value.unavailableReason === 'recipient_disabled' && value.enabled ||
    value.unavailableReason === 'not_friend' && (!value.enabled || value.friendshipState !== 'not_friend') ||
    value.unavailableReason === 'friendship_unknown' && (!value.enabled || value.friendshipState !== 'unknown')
  ) return protocolError()
  return { ok: true, value: {
    recipientId: value.recipientId,
    displayName: value.displayName,
    enabled: value.enabled,
    friendshipState: value.friendshipState,
    deliveryAvailable: value.deliveryAvailable,
    unavailableReason: value.unavailableReason,
  } }
}

export function parseDeliveryChannelChoices(value: unknown): Parsed<DeliveryChannelChoice[]> {
  return parseItemEnvelope(value, parseDeliveryChannelChoice)
}

export function parseDeliveryRecipientChoices(value: unknown): Parsed<DeliveryRecipientChoice[]> {
  return parseItemEnvelope(value, parseDeliveryRecipientChoice)
}

export function parseLinkedPreviewResponse(value: unknown): Parsed<LinkedPreviewResponse> {
  if (!isRecord(value) || !hasExactKeys(value, [
    'channelId',
    'channelLabel',
    'recipientId',
    'recipientDisplayName',
    'friendshipState',
    'formattedText',
    'receiptRequested',
    'receiptExpiresAt',
    'confirmationToken',
  ])) return protocolError()
  if (
    !isCanonicalUuid(value.channelId) ||
    !isNonEmptyString(value.channelLabel) ||
    !isCanonicalUuid(value.recipientId) ||
    !isNonEmptyString(value.recipientDisplayName) ||
    !isFriendshipState(value.friendshipState) ||
    !isString(value.formattedText) ||
    typeof value.receiptRequested !== 'boolean' ||
    !isNullableDateTime(value.receiptExpiresAt) ||
    !isNonEmptyString(value.confirmationToken)
  ) return protocolError()
  if (value.receiptRequested !== (value.receiptExpiresAt !== null)) return protocolError()
  return { ok: true, value: {
    channelId: value.channelId,
    channelLabel: value.channelLabel,
    recipientId: value.recipientId,
    recipientDisplayName: value.recipientDisplayName,
    friendshipState: value.friendshipState,
    formattedText: value.formattedText,
    receiptRequested: value.receiptRequested,
    receiptExpiresAt: value.receiptExpiresAt,
    confirmationToken: value.confirmationToken,
  } }
}

const parseDeliverySnapshot = (value: unknown): Parsed<DeliverySnapshot> => {
  if (!isRecord(value) || !hasExactKeys(value, [
    'channelId',
    'channelLabel',
    'recipientId',
    'channelActive',
    'recipientEnabled',
    'friendshipState',
  ])) return protocolError()
  if (
    !isCanonicalUuid(value.channelId) ||
    !isNonEmptyString(value.channelLabel) ||
    !isCanonicalUuid(value.recipientId) ||
    typeof value.channelActive !== 'boolean' ||
    typeof value.recipientEnabled !== 'boolean' ||
    !isFriendshipState(value.friendshipState)
  ) return protocolError()
  return { ok: true, value: {
    channelId: value.channelId,
    channelLabel: value.channelLabel,
    recipientId: value.recipientId,
    channelActive: value.channelActive,
    recipientEnabled: value.recipientEnabled,
    friendshipState: value.friendshipState,
  } }
}

const parseReceiptState = (value: unknown): Parsed<ReceiptState> => {
  if (!isRecord(value) || !hasExactKeys(value, ['requested', 'status', 'expiresAt', 'confirmedAt'])) return protocolError()
  if (
    typeof value.requested !== 'boolean' ||
    !isNullableDateTime(value.expiresAt) ||
    !isNullableDateTime(value.confirmedAt)
  ) return protocolError()
  const status = value.status
  if (status === 'not_requested' && value.requested === false && value.expiresAt === null && value.confirmedAt === null) {
    return { ok: true, value: { requested: false, status, expiresAt: null, confirmedAt: null } }
  }
  if (status === 'pending' && value.requested === true && value.expiresAt !== null && value.confirmedAt === null) {
    return { ok: true, value: { requested: true, status, expiresAt: value.expiresAt, confirmedAt: null } }
  }
  if (status === 'confirmed' && value.requested === true && value.expiresAt !== null && value.confirmedAt !== null) {
    return { ok: true, value: { requested: true, status, expiresAt: value.expiresAt, confirmedAt: value.confirmedAt } }
  }
  if (status === 'expired' && value.requested === true && value.expiresAt !== null && value.confirmedAt === null) {
    return { ok: true, value: { requested: true, status, expiresAt: value.expiresAt, confirmedAt: null } }
  }
  return protocolError()
}

export function parseLinkedDeliveryStatus(value: unknown): Parsed<LinkedDeliveryStatus> {
  if (!isRecord(value) || !isString(value.status)) return protocolError()
  const terminalFailure = value.status === 'failed' || value.status === 'unknown'
  const keys = terminalFailure
    ? ['operationId', 'snapshot', 'status', 'acceptedAt', 'completedAt', 'lineRequestId', 'receipt', 'error']
    : ['operationId', 'snapshot', 'status', 'acceptedAt', 'completedAt', 'lineRequestId', 'receipt']
  if (!hasExactKeys(value, keys) || !isCanonicalUuid(value.operationId) || !isTimezoneDateTime(value.acceptedAt)) {
    return protocolError()
  }
  const snapshot = parseDeliverySnapshot(value.snapshot)
  const receipt = parseReceiptState(value.receipt)
  if (!snapshot.ok || !receipt.ok || !isNullableString(value.lineRequestId)) return protocolError()
  if (
    value.status === 'processing' &&
    value.completedAt === null &&
    value.lineRequestId === null
  ) return { ok: true, value: {
    operationId: value.operationId,
    snapshot: snapshot.value,
    status: 'processing',
    acceptedAt: value.acceptedAt,
    completedAt: null,
    lineRequestId: null,
    receipt: receipt.value,
  } }
  if (
    value.status === 'succeeded' &&
    isTimezoneDateTime(value.completedAt)
  ) return { ok: true, value: {
    operationId: value.operationId,
    snapshot: snapshot.value,
    status: 'succeeded',
    acceptedAt: value.acceptedAt,
    completedAt: value.completedAt,
    lineRequestId: value.lineRequestId,
    receipt: receipt.value,
  } }
  if (
    value.status === 'failed' &&
    isTimezoneDateTime(value.completedAt) &&
    isSafeError(value.error) &&
    failedDeliveryCodes.has(value.error.code)
  ) return { ok: true, value: {
    operationId: value.operationId,
    snapshot: snapshot.value,
    status: 'failed',
    acceptedAt: value.acceptedAt,
    completedAt: value.completedAt,
    lineRequestId: value.lineRequestId,
    receipt: receipt.value,
    error: copySafeError(value.error),
  } }
  if (
    value.status === 'unknown' &&
    isTimezoneDateTime(value.completedAt) &&
    isSafeError(value.error) &&
    unknownDeliveryCodes.has(value.error.code)
  ) return { ok: true, value: {
    operationId: value.operationId,
    snapshot: snapshot.value,
    status: 'unknown',
    acceptedAt: value.acceptedAt,
    completedAt: value.completedAt,
    lineRequestId: value.lineRequestId,
    receipt: receipt.value,
    error: copySafeError(value.error),
  } }
  return protocolError()
}

export const parseDeliveryStatus = parseLinkedDeliveryStatus

// 既存fixed配信の互換DTO。linked API adapterは上の専用parserを利用する。
export function parsePreviewResponse(value: unknown): Parsed<PreviewResponse> {
  if (!isRecord(value) || !hasExactKeys(value, ['formattedText', 'confirmationToken']) || !isString(value.formattedText) || !isString(value.confirmationToken)) return protocolError()
  return { ok: true, value: { formattedText: value.formattedText, confirmationToken: value.confirmationToken } }
}

export function parseErrorResponse(value: unknown): Parsed<SafeError> {
  if (!isRecord(value) || !hasExactKeys(value, ['error']) || !isSafeError(value.error)) return protocolError()
  return { ok: true, value: copySafeError(value.error) }
}

export function parseDeliveryResult(value: unknown): Parsed<DeliveryResult> {
  if (!isRecord(value) || !isString(value.status) || !isCanonicalUuid(value.operationId) || !isString(value.acceptedAt)) return protocolError()
  if (value.status === 'processing' && hasExactKeys(value, ['status', 'operationId', 'acceptedAt', 'expiresAt']) && isString(value.expiresAt)) {
    return { ok: true, value: { status: value.status, operationId: value.operationId, acceptedAt: value.acceptedAt, expiresAt: value.expiresAt } }
  }
  if (value.status === 'succeeded' && hasExactKeys(value, ['status', 'operationId', 'acceptedAt', 'completedAt', 'lineRequestId']) && isString(value.completedAt) && isNullableString(value.lineRequestId)) {
    return { ok: true, value: { status: value.status, operationId: value.operationId, acceptedAt: value.acceptedAt, completedAt: value.completedAt, lineRequestId: value.lineRequestId } }
  }
  if (
    (value.status === 'failed' || value.status === 'unknown') &&
    hasExactKeys(value, ['status', 'operationId', 'acceptedAt', 'completedAt', 'error', 'lineRequestId']) &&
    isString(value.completedAt) &&
    isSafeError(value.error) &&
    isNullableString(value.lineRequestId) &&
    (
      value.status === 'failed'
        ? failedDeliveryCodes.has(value.error.code)
        : unknownDeliveryCodes.has(value.error.code)
    )
  ) return { ok: true, value: {
    status: value.status,
    operationId: value.operationId,
    acceptedAt: value.acceptedAt,
    completedAt: value.completedAt,
    error: copySafeError(value.error),
    lineRequestId: value.lineRequestId,
  } }
  return protocolError()
}
