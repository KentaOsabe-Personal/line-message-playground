import type { Parsed, SafeApiError } from './authDto'

export type CredentialsState = 'configured' | 'repair_required'
export type ConnectionStatus =
  | 'connected'
  | 'credential_unavailable'
  | 'authentication_failed'
  | 'identity_mismatch'
  | 'rate_limited'
  | 'line_unavailable'

export type ChannelAdminItem = {
  channelId: string
  label: string
  messagingApiChannelId: string
  botUserId: string
  providerId: string | null
  active: boolean
  credentialsState: CredentialsState
  credentialsUpdatedAt: string | null
  createdAt: string
  updatedAt: string
  webhookUrl: string
}

export type DeletedChannel = { channelId: string; label: string; deleted: true }
export type ConnectionCheck = {
  channelId: string
  status: ConnectionStatus
  checkedAt: string
  scope: 'access_token_and_bot_identity_only'
}

const protocolError = (): Parsed<never> => ({
  ok: false,
  error: { code: 'protocol_error', summary: '応答形式を確認できません。' },
})
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
const hasExactKeys = (value: Record<string, unknown>, keys: readonly string[]): boolean => {
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  return actual.length === expected.length && actual.every((key, index) => key === expected[index])
}
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
export const isChannelAdminUuid = (value: unknown): value is string =>
  typeof value === 'string' && uuidPattern.test(value)
const timezoneDateTimePattern =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-](\d{2}):(\d{2}))$/
const isLeapYear = (year: number) => year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
const daysInMonth = (year: number, month: number) => [
  31, isLeapYear(year) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31,
][month - 1] ?? 0
export const isChannelAdminDateTime = (value: unknown): value is string => {
  if (typeof value !== 'string') return false
  const match = timezoneDateTimePattern.exec(value)
  if (match === null) return false
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, offsetHourText, offsetMinuteText] = match
  const year = Number(yearText)
  const month = Number(monthText)
  const day = Number(dayText)
  return year >= 1 && year <= 9999 && month >= 1 && month <= 12 &&
    day >= 1 && day <= daysInMonth(year, month) && Number(hourText) <= 23 &&
    Number(minuteText) <= 59 && Number(secondText) <= 59 &&
    (offsetHourText === undefined || Number(offsetHourText) <= 23) &&
    (offsetMinuteText === undefined || Number(offsetMinuteText) <= 59)
}

const isWebhookUrl = (value: unknown, channelId: string): value is string => {
  if (typeof value !== 'string') return false
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && url.username === '' && url.password === '' &&
      url.search === '' && url.hash === '' &&
      url.pathname === `/api/line/webhooks/${channelId}/`
  } catch {
    return false
  }
}

export function parseChannelAdminItem(value: unknown): Parsed<ChannelAdminItem> {
  if (!isRecord(value) || !hasExactKeys(value, [
    'channelId', 'label', 'messagingApiChannelId', 'botUserId', 'providerId', 'active',
    'credentialsState', 'credentialsUpdatedAt', 'createdAt', 'updatedAt', 'webhookUrl',
  ])) return protocolError()
  if (
    !isChannelAdminUuid(value.channelId) ||
    typeof value.label !== 'string' || value.label.trim().length === 0 || value.label.length > 255 ||
    typeof value.messagingApiChannelId !== 'string' || !/^[0-9]{1,64}$/.test(value.messagingApiChannelId) ||
    typeof value.botUserId !== 'string' || !/^U[0-9a-f]{32}$/.test(value.botUserId) ||
    !(value.providerId === null || (typeof value.providerId === 'string' && /^[0-9]{1,64}$/.test(value.providerId))) ||
    typeof value.active !== 'boolean' ||
    (value.credentialsState !== 'configured' && value.credentialsState !== 'repair_required') ||
    !(value.credentialsUpdatedAt === null || isChannelAdminDateTime(value.credentialsUpdatedAt)) ||
    !isChannelAdminDateTime(value.createdAt) || !isChannelAdminDateTime(value.updatedAt) ||
    !isWebhookUrl(value.webhookUrl, value.channelId)
  ) return protocolError()
  return { ok: true, value: {
    channelId: value.channelId,
    label: value.label,
    messagingApiChannelId: value.messagingApiChannelId,
    botUserId: value.botUserId,
    providerId: value.providerId,
    active: value.active,
    credentialsState: value.credentialsState,
    credentialsUpdatedAt: value.credentialsUpdatedAt,
    createdAt: value.createdAt,
    updatedAt: value.updatedAt,
    webhookUrl: value.webhookUrl,
  } }
}

export function parseChannelAdminList(value: unknown): Parsed<ChannelAdminItem[]> {
  if (!isRecord(value) || !hasExactKeys(value, ['items']) || !Array.isArray(value.items)) return protocolError()
  const items: ChannelAdminItem[] = []
  for (const candidate of value.items) {
    const parsed = parseChannelAdminItem(candidate)
    if (!parsed.ok) return parsed
    items.push(parsed.value)
  }
  return { ok: true, value: items }
}

export function parseDeletedChannel(value: unknown): Parsed<DeletedChannel> {
  if (!isRecord(value) || !hasExactKeys(value, ['channelId', 'label', 'deleted']) ||
    !isChannelAdminUuid(value.channelId) || typeof value.label !== 'string' ||
    value.label.trim().length === 0 || value.deleted !== true) return protocolError()
  return { ok: true, value: { channelId: value.channelId, label: value.label, deleted: true } }
}

const connectionStatuses = new Set<ConnectionStatus>([
  'connected', 'credential_unavailable', 'authentication_failed',
  'identity_mismatch', 'rate_limited', 'line_unavailable',
])
export function parseConnectionCheck(value: unknown): Parsed<ConnectionCheck> {
  if (!isRecord(value) || !hasExactKeys(value, ['channelId', 'status', 'checkedAt', 'scope']) ||
    !isChannelAdminUuid(value.channelId) || typeof value.status !== 'string' ||
    !connectionStatuses.has(value.status as ConnectionStatus) || !isChannelAdminDateTime(value.checkedAt) ||
    value.scope !== 'access_token_and_bot_identity_only') return protocolError()
  return { ok: true, value: {
    channelId: value.channelId,
    status: value.status as ConnectionStatus,
    checkedAt: value.checkedAt,
    scope: value.scope,
  } }
}

const safeCodes = new Set([
  'validation_error', 'authentication_required', 'csrf_failed', 'owner_operation_blocked',
  'channel_not_found', 'duplicate_channel', 'stale_channel', 'channel_referenced',
  'provider_mismatch', 'provider_immutable', 'credential_unavailable',
  'storage_retryable', 'storage_unavailable',
])
const safeFields = new Set([
  'request', 'channelId', 'expectedUpdatedAt', 'label', 'messagingApiChannelId',
  'botUserId', 'providerId', 'active', 'credentialPair', 'message',
])
export function parseChannelAdminError(value: unknown): Parsed<SafeApiError> {
  if (!isRecord(value) || !hasExactKeys(value, ['error']) || !isRecord(value.error)) return protocolError()
  const error = value.error
  const keys = 'fields' in error ? ['code', 'summary', 'fields'] : ['code', 'summary']
  if (!hasExactKeys(error, keys) || typeof error.code !== 'string' || !safeCodes.has(error.code) ||
    typeof error.summary !== 'string' || error.summary.length === 0) return protocolError()
  if (!('fields' in error)) return { ok: true, value: { code: error.code, summary: error.summary } }
  if (!isRecord(error.fields)) return protocolError()
  const fields: Record<string, string[]> = {}
  for (const [field, messages] of Object.entries(error.fields)) {
    if (!safeFields.has(field) || !Array.isArray(messages) || messages.length === 0 ||
      messages.some((message) => typeof message !== 'string' || message.length === 0)) return protocolError()
    fields[field] = [...messages] as string[]
  }
  return { ok: true, value: { code: error.code, summary: error.summary, fields } }
}
