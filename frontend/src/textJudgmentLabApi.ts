import { parseJudgmentResponse, parseLabAccessResponse } from './textJudgmentLabDto'
import type { JudgmentRequest, JudgmentResponse, LabAccessResponse, Parsed } from './textJudgmentLabTypes'


export type LabHttpFailureCode =
  | 'reauthentication_required'
  | 'wrong_channel'
  | 'not_allowed'
  | 'rate_limited'
  | 'access_unavailable'
  | 'judgment_failed'
  | 'protocol_error'

export class LabHttpError extends Error {
  constructor(public readonly code: LabHttpFailureCode) {
    super('文章判定ラボの通信を完了できませんでした。')
    this.name = 'LabHttpError'
  }
}

export interface LabHttpClient {
  checkAccess(idToken: string, signal?: AbortSignal): Promise<LabAccessResponse>
  judge(idToken: string, request: JudgmentRequest, signal?: AbortSignal): Promise<JudgmentResponse>
}

type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>

const errorCode = async (response: Response): Promise<string | null> => {
  try {
    const payload: unknown = await response.json()
    if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return null
    const error = (payload as Record<string, unknown>).error
    if (typeof error !== 'object' || error === null || Array.isArray(error)) return null
    const code = (error as Record<string, unknown>).code
    return typeof code === 'string' ? code : null
  } catch {
    return null
  }
}

const mapFailure = async (response: Response, operation: 'access' | 'judgment'): Promise<LabHttpError> => {
  const backendCode = await errorCode(response)
  if (response.status === 401) return new LabHttpError('reauthentication_required')
  if (response.status === 403 && backendCode === 'wrong_channel') return new LabHttpError('wrong_channel')
  if (response.status === 403 && backendCode === 'not_allowed') return new LabHttpError('not_allowed')
  if (response.status === 429) return new LabHttpError('rate_limited')
  if (response.status === 502 || response.status === 504) return new LabHttpError('judgment_failed')
  if (response.status === 503) return new LabHttpError('access_unavailable')
  if (operation === 'judgment' && response.status >= 500) return new LabHttpError('judgment_failed')
  return new LabHttpError('protocol_error')
}

const parseSuccess = async <T>(response: Response, operation: 'access' | 'judgment', parser: (value: unknown) => Parsed<T>): Promise<T> => {
  if (!response.ok) throw await mapFailure(response, operation)
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new LabHttpError('protocol_error')
  }
  const parsed = parser(payload)
  if (!parsed.ok) throw new LabHttpError('protocol_error')
  return parsed.value
}

const authorizationHeaders = (idToken: string): Record<string, string> => {
  if (!idToken || /[\r\n\0]/.test(idToken)) throw new LabHttpError('reauthentication_required')
  return { Authorization: `Bearer ${idToken}`, 'Content-Type': 'application/json' }
}

export function createLabHttpClient(fetcher: Fetcher = fetch): LabHttpClient {
  const post = async <T>(
    path: string,
    idToken: string,
    body: object,
    operation: 'access' | 'judgment',
    parser: (value: unknown) => Parsed<T>,
    signal?: AbortSignal,
  ): Promise<T> => {
    let response: Response
    try {
      response = await fetcher(path, {
        method: 'POST',
        credentials: 'omit',
        cache: 'no-store',
        redirect: 'error',
        headers: authorizationHeaders(idToken),
        body: JSON.stringify(body),
        signal,
      })
    } catch (error) {
      if (error instanceof LabHttpError) throw error
      throw new LabHttpError(operation === 'access' ? 'access_unavailable' : 'judgment_failed')
    }
    return parseSuccess(response, operation, parser)
  }

  return Object.freeze({
    checkAccess: (idToken: string, signal?: AbortSignal) => post(
      '/api/labs/text-judgment/access', idToken, {}, 'access', parseLabAccessResponse, signal,
    ),
    judge: (idToken: string, request: JudgmentRequest, signal?: AbortSignal) => {
      const sentRequest = structuredClone(request)
      return post(
        '/api/labs/text-judgment/judgments', idToken, sentRequest, 'judgment',
        value => parseJudgmentResponse(value, sentRequest), signal,
      )
    },
  })
}
