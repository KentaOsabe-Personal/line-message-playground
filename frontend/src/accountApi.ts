import {
  isCanonicalUuid,
  parseChannelLink,
  parseChannelList,
  parseUnlinkExecution,
  parseUnlinkPreview,
} from './accountDto'
import type { ChannelLink, UnlinkExecution, UnlinkPreview } from './accountDto'
import { parseSafeApiError } from './authDto'
import type { Parsed, SafeApiError } from './authDto'
import type { ProtectedHttpClient } from './httpApi'

export type UnlinkExecutionInput = {
  confirmationToken?: string
  userAccessToken?: string
}

export interface AccountApiClient {
  listChannels(): Promise<ChannelLink[]>
  registerRecipient(channelId: string, accessToken?: string): Promise<ChannelLink>
  setRecipientEnabled(recipientId: string, enabled: boolean): Promise<ChannelLink>
  unlinkRecipient(recipientId: string): Promise<void>
  previewUnlink(): Promise<UnlinkPreview>
  executeUnlink(input: UnlinkExecutionInput): Promise<UnlinkExecution>
}

export class AccountApiError extends Error {
  constructor(public readonly error: SafeApiError, public readonly httpStatus?: number) {
    super(error.summary)
    this.name = 'AccountApiError'
  }
}

const invalidRequest = () => new AccountApiError({
  code: 'invalid_request',
  summary: '操作対象を確認できません。',
})

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    throw new AccountApiError({ code: 'protocol_error', summary: '応答形式を確認できません。' }, response.status)
  }
}

async function parseJsonResponse<T>(response: Response, parser: (value: unknown) => Parsed<T>): Promise<T> {
  const payload = await readJson(response)
  if (!response.ok) {
    const parsedError = parseSafeApiError(payload)
    throw new AccountApiError(parsedError.ok ? parsedError.value : parsedError.error, response.status)
  }
  const parsed = parser(payload)
  if (!parsed.ok) throw new AccountApiError(parsed.error, response.status)
  return parsed.value
}

async function parseEmptyResponse(response: Response): Promise<void> {
  if (!response.ok) {
    const payload = await readJson(response)
    const parsedError = parseSafeApiError(payload)
    throw new AccountApiError(parsedError.ok ? parsedError.value : parsedError.error, response.status)
  }
  if (response.status !== 204 || (await response.text()).length !== 0) {
    throw new AccountApiError({ code: 'protocol_error', summary: '応答形式を確認できません。' }, response.status)
  }
}

export function createAccountApiClient(http: ProtectedHttpClient): AccountApiClient {
  return Object.freeze({
    listChannels: async () => parseJsonResponse(
      await http.request({ path: '/api/account/channels/', method: 'GET' }),
      parseChannelList,
    ),
    registerRecipient: async (channelId: string, accessToken?: string) => {
      if (!isCanonicalUuid(channelId) || (accessToken !== undefined && accessToken.length === 0)) throw invalidRequest()
      return parseJsonResponse(await http.request({
        path: '/api/account/recipients/',
        method: 'POST',
        body: accessToken === undefined ? { channelId } : { channelId, accessToken },
      }), parseChannelLink)
    },
    setRecipientEnabled: async (recipientId: string, enabled: boolean) => {
      if (!isCanonicalUuid(recipientId) || typeof enabled !== 'boolean') throw invalidRequest()
      return parseJsonResponse(await http.request({
        path: `/api/account/recipients/${recipientId}/`,
        method: 'PATCH',
        body: { enabled },
      }), parseChannelLink)
    },
    unlinkRecipient: async (recipientId: string) => {
      if (!isCanonicalUuid(recipientId)) throw invalidRequest()
      await parseEmptyResponse(await http.request({
        path: `/api/account/recipients/${recipientId}/`,
        method: 'DELETE',
      }))
    },
    previewUnlink: async () => parseJsonResponse(
      await http.request({ path: '/api/account/unlink-preview/', method: 'POST' }),
      parseUnlinkPreview,
    ),
    executeUnlink: async (input: UnlinkExecutionInput) => {
      const keys = Object.keys(input)
      if (keys.some((key) => key !== 'confirmationToken' && key !== 'userAccessToken')) throw invalidRequest()
      if (
        (input.confirmationToken !== undefined && input.confirmationToken.length === 0) ||
        (input.userAccessToken !== undefined && input.userAccessToken.length === 0)
      ) throw invalidRequest()
      return parseJsonResponse(await http.request({
        path: '/api/account/unlink/',
        method: 'POST',
        body: { ...input },
      }), parseUnlinkExecution)
    },
  })
}
