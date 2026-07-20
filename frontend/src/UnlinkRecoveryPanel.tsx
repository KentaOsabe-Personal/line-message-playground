import { useState } from 'react'

import { AccountApiError } from './accountApi'
import type { AccountApiClient } from './accountApi'
import type { SessionStatus } from './authDto'

type PendingSession = Extract<SessionStatus, { state: 'unlinking' }>

type Props = {
  session: PendingSession
  api: AccountApiClient
  getAccessToken: () => string | null
  reauthenticateForUnlink: () => void
  reauthenticationReady: boolean
  onSessionReceived: (session: SessionStatus) => void
  refreshSession: () => Promise<void>
}

const toSession = (result: Awaited<ReturnType<AccountApiClient['executeUnlink']>>): SessionStatus =>
  result.state === 'completed'
    ? { state: 'anonymous' }
    : { state: 'unlinking', stage: result.stage, retryAction: result.retryAction }

export default function UnlinkRecoveryPanel({
  session,
  api,
  getAccessToken,
  reauthenticateForUnlink,
  reauthenticationReady,
  onSessionReceived,
  refreshSession,
}: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const resume = async () => {
    if (busy) return
    setError(null)
    if (session.stage === 'deauthorization_pending' && !reauthenticationReady) {
      reauthenticateForUnlink()
      return
    }
    const input = session.stage === 'deauthorization_pending'
      ? (() => {
          const token = getAccessToken()
          if (token === null) return null
          return { userAccessToken: token }
        })()
      : {}
    if (input === null) {
      reauthenticateForUnlink()
      return
    }
    setBusy(true)
    try {
      onSessionReceived(toSession(await api.executeUnlink(input)))
    } catch (caught) {
      if (caught instanceof AccountApiError && caught.error.code === 'invalid_line_proof') {
        reauthenticateForUnlink()
      } else if (caught instanceof AccountApiError && caught.httpStatus === 409) {
        await refreshSession()
      } else {
        setError(caught instanceof AccountApiError ? caught.error.summary : '解除処理を再開できませんでした。')
      }
    } finally {
      setBusy(false)
    }
  }

  const needsLine = session.stage === 'deauthorization_pending'
  return (
    <section className="account-console unlink-recovery" aria-labelledby="unlink-recovery-title">
      <h2 id="unlink-recovery-title">全連携解除を処理中です</h2>
      <p>{needsLine
        ? '完了を確認できていません。LINEで再認証して処理を再開してください。'
        : 'LINE側の認可取消は確認済みです。LINEへ再送せず、ローカルデータの削除だけを再開します。'}</p>
      {error && <p className="notice error" role="alert">{error}</p>}
      <button type="button" disabled={busy} onClick={() => void resume()}>
        {busy ? '再開中…' : needsLine ? 'LINEで再認証して解除を再開' : 'ローカル削除を再開'}
      </button>
    </section>
  )
}
