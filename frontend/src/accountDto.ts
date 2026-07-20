import type { Parsed } from './authDto'

export type ChannelState = 'active' | 'inactive'
export type LinkState = 'unlinked' | 'linked_enabled' | 'linked_disabled'
export type FriendshipState = 'friend' | 'not_friend' | 'unknown'

export type ChannelLink = {
  channelId: string
  channelLabel: string
  channelState: ChannelState
  linkState: LinkState
  friendshipState: FriendshipState
  deliveryAvailable: boolean
  recipientId: string | null
}

export type UnlinkPreview = {
  displayName: string
  recipientCount: number
  channelLabels: string[]
  deliveryAuditRetained: true
  confirmationToken: string
  expiresAt: string
}

export type UnlinkExecution =
  | { state: 'completed' }
  | {
      state: 'pending'
      stage: 'deauthorization_pending' | 'local_deletion_pending'
      retryAction: 'reauthenticate' | 'retry_local_delete'
    }

const protocolError = (): Parsed<never> => ({
  ok: false,
  error: { code: 'protocol_error', summary: '応答形式を確認できません。' },
})

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const hasExactKeys = (value: Record<string, unknown>, keys: readonly string[]) => {
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  return actual.length === expected.length && actual.every((key, index) => key === expected[index])
}

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

export const isCanonicalUuid = (value: unknown): value is string =>
  typeof value === 'string' && uuidPattern.test(value)

const isNonEmptyString = (value: unknown): value is string =>
  typeof value === 'string' && value.length > 0

const isTimezoneDateTime = (value: unknown): value is string =>
  typeof value === 'string' && (value.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(value)) && !Number.isNaN(Date.parse(value))

export function parseChannelLink(value: unknown): Parsed<ChannelLink> {
  if (!isRecord(value) || !hasExactKeys(value, [
    'channelId', 'channelLabel', 'channelState', 'linkState', 'friendshipState',
    'deliveryAvailable', 'recipientId',
  ])) return protocolError()

  const channelState = value.channelState
  const linkState = value.linkState
  const friendshipState = value.friendshipState
  if (
    !isCanonicalUuid(value.channelId) || !isNonEmptyString(value.channelLabel) ||
    (channelState !== 'active' && channelState !== 'inactive') ||
    (linkState !== 'unlinked' && linkState !== 'linked_enabled' && linkState !== 'linked_disabled') ||
    (friendshipState !== 'friend' && friendshipState !== 'not_friend' && friendshipState !== 'unknown') ||
    typeof value.deliveryAvailable !== 'boolean'
  ) return protocolError()

  if (linkState === 'unlinked' ? value.recipientId !== null : !isCanonicalUuid(value.recipientId)) {
    return protocolError()
  }
  const expectedDelivery = channelState === 'active' && linkState === 'linked_enabled' && friendshipState === 'friend'
  if (value.deliveryAvailable !== expectedDelivery) return protocolError()

  return { ok: true, value: {
    channelId: value.channelId,
    channelLabel: value.channelLabel,
    channelState,
    linkState,
    friendshipState,
    deliveryAvailable: value.deliveryAvailable,
    recipientId: value.recipientId as string | null,
  } }
}

export function parseChannelList(value: unknown): Parsed<ChannelLink[]> {
  if (!isRecord(value) || !hasExactKeys(value, ['items']) || !Array.isArray(value.items)) return protocolError()
  const items: ChannelLink[] = []
  for (const item of value.items) {
    const parsed = parseChannelLink(item)
    if (!parsed.ok) return parsed
    items.push(parsed.value)
  }
  return { ok: true, value: items }
}

export function parseUnlinkPreview(value: unknown): Parsed<UnlinkPreview> {
  if (!isRecord(value) || !hasExactKeys(value, [
    'displayName', 'recipientCount', 'channelLabels', 'deliveryAuditRetained',
    'confirmationToken', 'expiresAt',
  ])) return protocolError()
  if (
    !isNonEmptyString(value.displayName) ||
    !Number.isSafeInteger(value.recipientCount) || (value.recipientCount as number) < 0 ||
    !Array.isArray(value.channelLabels) || value.channelLabels.some((label) => !isNonEmptyString(label)) ||
    value.deliveryAuditRetained !== true ||
    !isNonEmptyString(value.confirmationToken) ||
    !isTimezoneDateTime(value.expiresAt)
  ) return protocolError()
  return { ok: true, value: {
    displayName: value.displayName,
    recipientCount: value.recipientCount as number,
    channelLabels: [...value.channelLabels] as string[],
    deliveryAuditRetained: true,
    confirmationToken: value.confirmationToken,
    expiresAt: value.expiresAt,
  } }
}

export function parseUnlinkExecution(value: unknown): Parsed<UnlinkExecution> {
  if (!isRecord(value) || typeof value.state !== 'string') return protocolError()
  if (value.state === 'completed' && hasExactKeys(value, ['state'])) {
    return { ok: true, value: { state: 'completed' } }
  }
  if (value.state !== 'pending' || !hasExactKeys(value, ['state', 'stage', 'retryAction'])) return protocolError()
  if (value.stage === 'deauthorization_pending' && value.retryAction === 'reauthenticate') {
    return { ok: true, value: { state: 'pending', stage: value.stage, retryAction: value.retryAction } }
  }
  if (value.stage === 'local_deletion_pending' && value.retryAction === 'retry_local_delete') {
    return { ok: true, value: { state: 'pending', stage: value.stage, retryAction: value.retryAction } }
  }
  return protocolError()
}
