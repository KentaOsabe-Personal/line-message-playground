import type { OwnerProfile, SessionStatus } from './authDto'

export type SafeAuthErrorCode =
  | 'configuration_invalid'
  | 'initialization_failed'
  | 'token_unavailable'
  | 'verification_failed'
  | 'logout_failed'

export type AuthState =
  | { kind: 'initializing' }
  | { kind: 'login_required' }
  | { kind: 'verifying' }
  | { kind: 'anonymous' }
  | { kind: 'authenticated'; profile: OwnerProfile }
  | { kind: 'unlinking'; stage: 'deauthorization_pending' | 'local_deletion_pending' }
  | { kind: 'error'; code: SafeAuthErrorCode; retryable: boolean }

export type AuthEvent =
  | { type: 'restart' }
  | { type: 'login_required' }
  | { type: 'verification_started' }
  | { type: 'session_received'; session: SessionStatus }
  | { type: 'login_cancelled' }
  | { type: 'session_invalidated' }
  | { type: 'failed'; code: SafeAuthErrorCode; retryable: boolean }

export const initialAuthState: AuthState = { kind: 'initializing' }

export function transitionAuth(_state: AuthState, event: AuthEvent): AuthState {
  switch (event.type) {
    case 'restart': return initialAuthState
    case 'login_required': return { kind: 'login_required' }
    case 'verification_started': return { kind: 'verifying' }
    case 'login_cancelled': return { kind: 'anonymous' }
    case 'session_invalidated': return { kind: 'login_required' }
    case 'failed': return { kind: 'error', code: event.code, retryable: event.retryable }
    case 'session_received':
      if (event.session.state === 'anonymous') return { kind: 'anonymous' }
      if (event.session.state === 'authenticated') return { kind: 'authenticated', profile: event.session.profile }
      return { kind: 'unlinking', stage: event.session.stage }
  }
}
