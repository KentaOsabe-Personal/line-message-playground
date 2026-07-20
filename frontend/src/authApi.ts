import { parseSafeApiError, parseSessionStatus } from './authDto'
import type { SafeApiError, SessionStatus } from './authDto'
import type { ProtectedHttpClient } from './httpApi'

export interface AuthApiClient {
  bootstrap(): Promise<SessionStatus>
  login(idToken: string): Promise<SessionStatus>
  logout(): Promise<SessionStatus>
}

export class AuthApiError extends Error {
  constructor(public readonly error: SafeApiError, public readonly httpStatus?: number) {
    super(error.summary)
    this.name = 'AuthApiError'
  }
}

async function parseResponse(response: Response): Promise<SessionStatus> {
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    throw new AuthApiError({ code: 'protocol_error', summary: '応答形式を確認できません。' }, response.status)
  }

  if (!response.ok) {
    const parsed = parseSafeApiError(payload)
    throw new AuthApiError(parsed.ok ? parsed.value : parsed.error, response.status)
  }
  const parsed = parseSessionStatus(payload)
  if (!parsed.ok) throw new AuthApiError(parsed.error, response.status)
  return parsed.value
}

export function createAuthApiClient(http: ProtectedHttpClient): AuthApiClient {
  return Object.freeze({
    bootstrap: async () => parseResponse(await http.request({ path: '/api/account/session/', method: 'GET' })),
    login: async (idToken: string) => parseResponse(await http.request({
      path: '/api/account/session/line/',
      method: 'POST',
      body: { idToken },
    })),
    logout: async () => parseResponse(await http.request({ path: '/api/account/session/', method: 'DELETE' })),
  })
}
