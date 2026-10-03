export type HttpMethod = 'GET' | 'POST' | 'PATCH' | 'DELETE'
export type ReadRequestOptions = Readonly<{ signal?: AbortSignal }>

export interface ProtectedHttpClient {
  request(input: {
    path: string
    method: HttpMethod
    body?: unknown
    signal?: AbortSignal
  }): Promise<Response>
}

export type ProtectedHttpClientOptions = {
  onSessionInvalid?: () => void
  fetch?: typeof fetch
  readCookie?: () => string
}

export class ProtectedHttpClientError extends Error {
  readonly summary: string

  constructor(public readonly code: 'csrf_missing' | 'network_error' | 'aborted') {
    super(code)
    this.name = 'ProtectedHttpClientError'
    this.summary =
      code === 'csrf_missing'
        ? '安全な送信準備を確認できません。'
        : code === 'aborted'
          ? '読み込みを中止しました。'
          : 'Backendに接続できません。'
  }
}

const unsafeMethods = new Set<HttpMethod>(['POST', 'PATCH', 'DELETE'])

function readCookieValue(cookie: string, name: string): string | null {
  const prefix = `${name}=`
  const item = cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
  if (!item) return null
  try {
    const value = decodeURIComponent(item.slice(prefix.length))
    return value.length > 0 ? value : null
  } catch {
    return null
  }
}

export function createProtectedHttpClient(
  options: ProtectedHttpClientOptions = {},
): ProtectedHttpClient {
  const fetchRequest = options.fetch ?? globalThis.fetch
  const readCookie = options.readCookie ?? (() => document.cookie)

  return Object.freeze({
    async request(input: {
      path: string
      method: HttpMethod
      body?: unknown
      signal?: AbortSignal
    }) {
      const headers: Record<string, string> = {}
      const request: RequestInit = {
        method: input.method,
        credentials: 'same-origin',
      }
      if (input.signal !== undefined) request.signal = input.signal

      if (unsafeMethods.has(input.method)) {
        const csrfToken = readCookieValue(readCookie(), 'csrftoken')
        if (csrfToken === null) throw new ProtectedHttpClientError('csrf_missing')
        headers['X-CSRFToken'] = csrfToken
      }
      if (input.body !== undefined) {
        headers['Content-Type'] = 'application/json'
        request.body = JSON.stringify(input.body)
      }
      if (Object.keys(headers).length > 0) request.headers = headers

      let response: Response
      try {
        response = await fetchRequest(input.path, request)
      } catch (error) {
        if (
          input.signal?.aborted === true ||
          (typeof error === 'object' &&
            error !== null &&
            'name' in error &&
            error.name === 'AbortError')
        ) {
          throw new ProtectedHttpClientError('aborted')
        }
        throw new ProtectedHttpClientError('network_error')
      }
      if (response.status === 401) options.onSessionInvalid?.()
      return response
    },
  })
}
