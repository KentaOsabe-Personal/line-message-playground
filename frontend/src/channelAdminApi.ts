import type { Parsed, SafeApiError } from './authDto'
import {
  isChannelAdminUuid,
  parseChannelAdminError,
  parseChannelAdminItem,
  parseChannelAdminList,
  parseConnectionCheck,
  parseDeletedChannel,
} from './channelAdminDto'
import type { ChannelAdminItem, ConnectionCheck, DeletedChannel } from './channelAdminDto'
import { createProtectedHttpClient, ProtectedHttpClientError } from './httpApi'
import type { HttpMethod, ProtectedHttpClient } from './httpApi'

export type CreateChannelInput = {
  label: string
  messagingApiChannelId: string
  botUserId: string
  providerId: string
  accessToken: string
  channelSecret: string
  active: boolean
}
export type UpdateChannelInput = {
  expectedUpdatedAt: string
  label?: string
  messagingApiChannelId?: string
  botUserId?: string
  providerId?: string
  accessToken?: string
  channelSecret?: string
}
export type SetChannelStateInput = {
  expectedUpdatedAt: string
  active: boolean
  accessToken?: string
  channelSecret?: string
}

export interface ChannelAdminApiClient {
  listChannels(): Promise<ChannelAdminItem[]>
  getChannel(channelId: string): Promise<ChannelAdminItem>
  register(input: CreateChannelInput): Promise<ChannelAdminItem>
  update(channelId: string, input: UpdateChannelInput): Promise<ChannelAdminItem>
  setState(channelId: string, input: SetChannelStateInput): Promise<ChannelAdminItem>
  delete(channelId: string, expectedUpdatedAt: string): Promise<DeletedChannel>
  checkConnection(channelId: string): Promise<ConnectionCheck>
}

export class ChannelAdminApiError extends Error {
  constructor(public readonly error: SafeApiError, public readonly httpStatus?: number) {
    super(error.summary)
    this.name = 'ChannelAdminApiError'
  }
}

const protocolError = (httpStatus?: number) => new ChannelAdminApiError({
  code: 'protocol_error', summary: '応答形式を確認できません。',
}, httpStatus)
const assertChannelId = (channelId: string) => {
  if (!isChannelAdminUuid(channelId)) throw protocolError()
}

async function requestOnce<T>(
  client: ProtectedHttpClient,
  input: { path: string; method: HttpMethod; body?: unknown },
  parse: (value: unknown) => Parsed<T>,
): Promise<T> {
  let response: Response
  try {
    response = await client.request(input)
  } catch (error) {
    if (error instanceof ProtectedHttpClientError) {
      throw new ChannelAdminApiError({ code: error.code, summary: error.summary })
    }
    throw new ChannelAdminApiError({ code: 'network_error', summary: 'Backendに接続できません。' })
  }
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw protocolError(response.status)
  }
  if (!response.ok) {
    const parsed = parseChannelAdminError(payload)
    throw new ChannelAdminApiError(parsed.ok ? parsed.value : parsed.error, response.status)
  }
  const parsed = parse(payload)
  if (!parsed.ok) throw new ChannelAdminApiError(parsed.error, response.status)
  return parsed.value
}

export function createChannelAdminApiClient(
  client: ProtectedHttpClient = createProtectedHttpClient(),
): ChannelAdminApiClient {
  const channelPath = (channelId: string) => {
    assertChannelId(channelId)
    return `/api/line/channels/${channelId}/`
  }
  return Object.freeze({
    listChannels: () => requestOnce(client, { path: '/api/line/channels/', method: 'GET' }, parseChannelAdminList),
    getChannel: (channelId: string) => requestOnce(client, { path: channelPath(channelId), method: 'GET' }, parseChannelAdminItem),
    register: (input: CreateChannelInput) => requestOnce(client, {
      path: '/api/line/channels/', method: 'POST', body: input,
    }, parseChannelAdminItem),
    update: (channelId: string, input: UpdateChannelInput) => requestOnce(client, {
      path: channelPath(channelId), method: 'PATCH', body: input,
    }, parseChannelAdminItem),
    setState: (channelId: string, input: SetChannelStateInput) => requestOnce(client, {
      path: `${channelPath(channelId)}state/`, method: 'POST', body: input,
    }, parseChannelAdminItem),
    delete: (channelId: string, expectedUpdatedAt: string) => requestOnce(client, {
      path: channelPath(channelId), method: 'DELETE', body: { expectedUpdatedAt },
    }, parseDeletedChannel),
    checkConnection: (channelId: string) => requestOnce(client, {
      path: `${channelPath(channelId)}connection-check/`, method: 'POST', body: {},
    }, parseConnectionCheck),
  })
}
