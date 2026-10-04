import type { Parsed, SafeApiError } from './authDto'
import {
  isChannelAdminDateTime,
  isChannelAdminUuid,
  parseChannelAdminError,
} from './channelAdminDto'
import { createProtectedHttpClient, ProtectedHttpClientError } from './httpApi'
import type { HttpMethod, ProtectedHttpClient, ReadRequestOptions } from './httpApi'
import {
  parseDeactivation,
  parseHistoryPage,
  parseOperation,
  parsePreview,
  parseRichMenuError,
  parseRichMenuState,
  parseTemplates,
} from './richMenuAdminDto'
import type {
  DeactivationView,
  HistoryPageView,
  OperationView,
  PreviewView,
  RichMenuStateView,
  TemplateDescriptor,
  TemplateField,
} from './richMenuAdminDto'

export type PreviewInput = {
  templateId: string
  templateVersion: number
  channelRevision: string
  fields: Record<string, TemplateField>
}
type OperationBase = { operationId: string; channelRevision: string }
export type RichMenuOperationInput =
  | (OperationBase & {
      kind: 'apply'
      confirmationToken: string
      templateId: string
      templateVersion: number
      fields: Record<string, TemplateField>
    })
  | (OperationBase & { kind: 'unlink' | 'release'; targetResourceId: string })
  | (OperationBase & { kind: 'recheck'; subjectOperationId: string })
  | (OperationBase & { kind: 'cleanup'; subjectOperationId: string; targetResourceId: string })
export type StartDeactivationInput = { operationId: string; expectedUpdatedAt: string }
export type RecheckDeactivationInput = StartDeactivationInput & { recoveryOperationId: string }

export interface RichMenuAdminApiClient {
  listTemplates(options?: ReadRequestOptions): Promise<TemplateDescriptor[]>
  createPreview(channelId: string, input: PreviewInput): Promise<PreviewView>
  getState(channelId: string, options?: ReadRequestOptions): Promise<RichMenuStateView>
  startOperation(channelId: string, input: RichMenuOperationInput): Promise<OperationView>
  getOperation(operationId: string, options?: ReadRequestOptions): Promise<OperationView>
  getHistory(
    channelId: string,
    cursor?: string,
    options?: ReadRequestOptions,
  ): Promise<HistoryPageView>
  getDeactivation(channelId: string, options?: ReadRequestOptions): Promise<DeactivationView | null>
  startDeactivation(channelId: string, input: StartDeactivationInput): Promise<DeactivationView>
  recheckDeactivation(channelId: string, input: RecheckDeactivationInput): Promise<DeactivationView>
}

export class RichMenuAdminApiError extends Error {
  constructor(
    public readonly error: SafeApiError,
    public readonly outcome: 'load_failed' | 'refresh_required',
    public readonly httpStatus?: number,
  ) {
    super(error.summary)
    this.name = 'RichMenuAdminApiError'
  }
}

const safeError = (
  code: string,
  summary: string,
  outcome: 'load_failed' | 'refresh_required',
  status?: number,
) => new RichMenuAdminApiError({ code, summary }, outcome, status)
const assertUuid = (value: string) => {
  if (!isChannelAdminUuid(value))
    throw safeError('protocol_error', '識別子を確認できません。', 'load_failed')
}
const assertDate = (value: string, outcome: 'load_failed' | 'refresh_required') => {
  if (!isChannelAdminDateTime(value))
    throw safeError('protocol_error', '更新時点を確認できません。', outcome)
}

async function requestOnce<T>(
  client: ProtectedHttpClient,
  input: { path: string; method: HttpMethod; body?: unknown; signal?: AbortSignal },
  parse: (value: unknown) => Parsed<T>,
): Promise<T> {
  const outcome = input.method === 'GET' ? 'load_failed' : 'refresh_required'
  let response: Response
  try {
    response = await client.request(input)
  } catch (error) {
    if (error instanceof ProtectedHttpClientError)
      throw safeError(error.code, error.summary, outcome)
    throw safeError('network_error', 'Backendに接続できません。', outcome)
  }
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw safeError('protocol_error', '応答形式を確認できません。', outcome, response.status)
  }
  if (!response.ok) {
    const rich = parseRichMenuError(payload)
    if (rich.ok) throw new RichMenuAdminApiError(rich.value, outcome, response.status)
    const channel = parseChannelAdminError(payload)
    throw new RichMenuAdminApiError(
      channel.ok ? channel.value : channel.error,
      outcome,
      response.status,
    )
  }
  const parsed = parse(payload)
  if (!parsed.ok) throw new RichMenuAdminApiError(parsed.error, outcome, response.status)
  return parsed.value
}

export function createRichMenuAdminApiClient(
  client: ProtectedHttpClient = createProtectedHttpClient(),
): RichMenuAdminApiClient {
  const richChannel = (channelId: string) => {
    assertUuid(channelId)
    return `/api/line/rich-menus/channels/${channelId}/`
  }
  const adminChannel = (channelId: string) => {
    assertUuid(channelId)
    return `/api/line/channels/${channelId}/`
  }
  return Object.freeze({
    listTemplates: (options: ReadRequestOptions = {}) =>
      requestOnce(
        client,
        { path: '/api/line/rich-menus/templates/', method: 'GET', ...options },
        parseTemplates,
      ),
    createPreview: (channelId: string, input: PreviewInput) => {
      assertDate(input.channelRevision, 'refresh_required')
      return requestOnce(
        client,
        { path: `${richChannel(channelId)}preview/`, method: 'POST', body: input },
        parsePreview,
      )
    },
    getState: (channelId: string, options: ReadRequestOptions = {}) =>
      requestOnce(
        client,
        { path: `${richChannel(channelId)}state/`, method: 'GET', ...options },
        parseRichMenuState,
      ),
    startOperation: (channelId: string, input: RichMenuOperationInput) => {
      assertUuid(input.operationId)
      assertDate(input.channelRevision, 'refresh_required')
      return requestOnce(
        client,
        { path: `${richChannel(channelId)}operations/`, method: 'POST', body: input },
        parseOperation,
      )
    },
    getOperation: (operationId: string, options: ReadRequestOptions = {}) => {
      assertUuid(operationId)
      return requestOnce(
        client,
        { path: `/api/line/rich-menus/operations/${operationId}/`, method: 'GET', ...options },
        parseOperation,
      )
    },
    getHistory: (channelId: string, cursor?: string, options: ReadRequestOptions = {}) => {
      const query = new URLSearchParams({ limit: '20' })
      if (cursor !== undefined) query.set('cursor', cursor)
      return requestOnce(
        client,
        {
          path: `${richChannel(channelId)}history/?${query.toString()}`,
          method: 'GET',
          ...options,
        },
        parseHistoryPage,
      )
    },
    getDeactivation: (channelId: string, options: ReadRequestOptions = {}) =>
      requestOnce(
        client,
        { path: `${adminChannel(channelId)}deactivation/`, method: 'GET', ...options },
        parseDeactivation,
      ),
    startDeactivation: (channelId: string, input: StartDeactivationInput) => {
      assertUuid(input.operationId)
      assertDate(input.expectedUpdatedAt, 'refresh_required')
      return requestOnce(
        client,
        { path: `${adminChannel(channelId)}deactivation/`, method: 'POST', body: input },
        (value) => {
          const parsed = parseDeactivation(value)
          return parsed.ok && parsed.value !== null
            ? { ok: true, value: parsed.value }
            : parsed.ok
              ? {
                  ok: false,
                  error: { code: 'protocol_error', summary: '応答形式を確認できません。' },
                }
              : parsed
        },
      )
    },
    recheckDeactivation: (channelId: string, input: RecheckDeactivationInput) => {
      assertUuid(input.operationId)
      assertUuid(input.recoveryOperationId)
      assertDate(input.expectedUpdatedAt, 'refresh_required')
      return requestOnce(
        client,
        { path: `${adminChannel(channelId)}deactivation/recheck/`, method: 'POST', body: input },
        (value) => {
          const parsed = parseDeactivation(value)
          return parsed.ok && parsed.value !== null
            ? { ok: true, value: parsed.value }
            : parsed.ok
              ? {
                  ok: false,
                  error: { code: 'protocol_error', summary: '応答形式を確認できません。' },
                }
              : parsed
        },
      )
    },
  })
}
