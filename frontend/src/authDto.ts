export type OwnerProfile = { displayName: string; linked: true }

export type SessionStatus =
  | { state: 'anonymous' }
  | { state: 'authenticated'; profile: OwnerProfile }
  | {
      state: 'unlinking'
      stage: 'deauthorization_pending' | 'local_deletion_pending'
      retryAction: 'reauthenticate' | 'retry_local_delete'
    }

export type SafeApiError = {
  code: string
  summary: string
  fields?: Record<string, string[]>
}

export type Parsed<T> = { ok: true; value: T } | { ok: false; error: SafeApiError }

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

export function parseSessionStatus(value: unknown): Parsed<SessionStatus> {
  if (!isRecord(value) || typeof value.state !== 'string') return protocolError()
  if (value.state === 'anonymous' && hasExactKeys(value, ['state'])) {
    return { ok: true, value: { state: 'anonymous' } }
  }
  if (value.state === 'authenticated' && hasExactKeys(value, ['state', 'profile']) && isRecord(value.profile)) {
    const profile = value.profile
    if (
      hasExactKeys(profile, ['displayName', 'linked']) &&
      typeof profile.displayName === 'string' &&
      profile.displayName.length > 0 &&
      profile.linked === true
    ) {
      return { ok: true, value: { state: 'authenticated', profile: { displayName: profile.displayName, linked: true } } }
    }
    return protocolError()
  }
  if (value.state === 'unlinking' && hasExactKeys(value, ['state', 'stage', 'retryAction'])) {
    if (value.stage === 'deauthorization_pending' && value.retryAction === 'reauthenticate') {
      return { ok: true, value: { state: 'unlinking', stage: value.stage, retryAction: value.retryAction } }
    }
    if (value.stage === 'local_deletion_pending' && value.retryAction === 'retry_local_delete') {
      return { ok: true, value: { state: 'unlinking', stage: value.stage, retryAction: value.retryAction } }
    }
  }
  return protocolError()
}

export function parseSafeApiError(value: unknown): Parsed<SafeApiError> {
  if (!isRecord(value) || !hasExactKeys(value, ['error']) || !isRecord(value.error)) return protocolError()
  const error = value.error
  const allowedKeys = error.fields === undefined ? ['code', 'summary'] : ['code', 'summary', 'fields']
  if (
    !hasExactKeys(error, allowedKeys) ||
    typeof error.code !== 'string' || error.code.length === 0 ||
    typeof error.summary !== 'string' || error.summary.length === 0
  ) return protocolError()

  if (error.fields === undefined) return { ok: true, value: { code: error.code, summary: error.summary } }
  if (!isRecord(error.fields)) return protocolError()
  const fields: Record<string, string[]> = {}
  for (const [key, messages] of Object.entries(error.fields)) {
    if (!Array.isArray(messages) || messages.length === 0 || messages.some((message) => typeof message !== 'string')) {
      return protocolError()
    }
    fields[key] = messages as string[]
  }
  return { ok: true, value: { code: error.code, summary: error.summary, fields } }
}
