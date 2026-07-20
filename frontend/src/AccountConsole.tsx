import { useEffect, useMemo, useState } from 'react'

import { AccountApiError, createAccountApiClient } from './accountApi'
import type { AccountApiClient } from './accountApi'
import type { ChannelLink, UnlinkPreview } from './accountDto'
import type { SessionStatus } from './authDto'
import { createProtectedHttpClient } from './httpApi'
import UnlinkRecoveryPanel from './UnlinkRecoveryPanel'

type AccountSession = Extract<SessionStatus, { state: 'authenticated' | 'unlinking' }>

type Props = {
  session: AccountSession
  api?: AccountApiClient
  getAccessToken: () => string | null
  reauthenticate: () => void
  reauthenticateForUnlink: () => void
  unlinkReauthenticationReady: boolean
  onSessionReceived: (session: SessionStatus) => void
  refreshSession: () => Promise<void>
}

const linkStateLabel = {
  unlinked: '未連携',
  linked_enabled: '有効',
  linked_disabled: '無効',
} as const

const friendshipLabel = {
  friend: '友だち',
  not_friend: '友だちではありません',
  unknown: '不明',
} as const

const resultToSession = (result: Awaited<ReturnType<AccountApiClient['executeUnlink']>>): SessionStatus =>
  result.state === 'completed'
    ? { state: 'anonymous' }
    : { state: 'unlinking', stage: result.stage, retryAction: result.retryAction }

export default function AccountConsole({
  session,
  api,
  getAccessToken,
  reauthenticate,
  reauthenticateForUnlink,
  unlinkReauthenticationReady,
  onSessionReceived,
  refreshSession,
}: Props) {
  const client = useMemo(() => api ?? createAccountApiClient(createProtectedHttpClient({
    onSessionInvalid: () => { void refreshSession() },
  })), [api, refreshSession])
  const [channels, setChannels] = useState<ChannelLink[]>([])
  const [loading, setLoading] = useState(session.state === 'authenticated')
  const [operation, setOperation] = useState<string | null>(null)
  const [targetErrors, setTargetErrors] = useState<Record<string, string>>({})
  const [globalError, setGlobalError] = useState<string | null>(null)
  const [preview, setPreview] = useState<UnlinkPreview | null>(null)

  useEffect(() => {
    if (session.state !== 'authenticated') return
    let current = true
    setLoading(true)
    setGlobalError(null)
    void client.listChannels().then((items) => {
      if (current) setChannels(items)
    }).catch((caught) => {
      if (current) setGlobalError(caught instanceof AccountApiError ? caught.error.summary : '配信先を取得できませんでした。')
    }).finally(() => {
      if (current) setLoading(false)
    })
    return () => { current = false }
  }, [client, session.state])

  if (session.state === 'unlinking') {
    return <UnlinkRecoveryPanel
      session={session}
      api={client}
      getAccessToken={getAccessToken}
      reauthenticateForUnlink={reauthenticateForUnlink}
      reauthenticationReady={unlinkReauthenticationReady}
      onSessionReceived={onSessionReceived}
      refreshSession={refreshSession}
    />
  }

  const replaceChannel = (next: ChannelLink) => {
    setChannels((items) => items.map((item) => item.channelId === next.channelId ? next : item))
  }

  const runTargetOperation = async (key: string, operationCall: () => Promise<ChannelLink | void>) => {
    if (operation !== null) return
    setOperation(key)
    setTargetErrors((errors) => ({ ...errors, [key]: '' }))
    try {
      const next = await operationCall()
      if (next) replaceChannel(next)
    } catch (caught) {
      setTargetErrors((errors) => ({
        ...errors,
        [key]: caught instanceof AccountApiError ? caught.error.summary : '操作を完了できませんでした。',
      }))
    } finally {
      setOperation(null)
    }
  }

  const unlinkRecipient = async (channel: ChannelLink) => {
    if (channel.recipientId === null) return
    const recipientId = channel.recipientId
    await runTargetOperation(channel.channelId, async () => {
      await client.unlinkRecipient(recipientId)
      replaceChannel({
        ...channel,
        linkState: 'unlinked',
        friendshipState: 'unknown',
        deliveryAvailable: false,
        recipientId: null,
      })
    })
  }

  const showPreview = async () => {
    if (operation !== null) return
    setOperation('unlink-preview')
    setGlobalError(null)
    try {
      setPreview(await client.previewUnlink())
    } catch (caught) {
      setGlobalError(caught instanceof AccountApiError ? caught.error.summary : '解除内容を取得できませんでした。')
    } finally {
      setOperation(null)
    }
  }

  const executeInitialUnlink = async () => {
    if (preview === null || operation !== null) return
    const userAccessToken = getAccessToken()
    if (userAccessToken === null) {
      reauthenticate()
      return
    }
    setOperation('unlink-execute')
    setGlobalError(null)
    try {
      const result = await client.executeUnlink({
        confirmationToken: preview.confirmationToken,
        userAccessToken,
      })
      onSessionReceived(resultToSession(result))
    } catch (caught) {
      if (caught instanceof AccountApiError && caught.error.code === 'invalid_line_proof') {
        reauthenticate()
      } else if (caught instanceof AccountApiError && caught.error.code === 'stale_confirmation') {
        setPreview(null)
        setGlobalError(caught.error.summary)
      } else if (
        caught instanceof AccountApiError &&
        (caught.error.code === 'unlink_in_progress' || caught.error.code === 'unlink_attempt_stale')
      ) {
        await refreshSession()
      } else {
        setGlobalError(caught instanceof AccountApiError ? caught.error.summary : '全連携解除を開始できませんでした。')
      }
    } finally {
      setOperation(null)
    }
  }

  return (
    <section className="account-console" aria-labelledby="account-console-title">
      <h2 id="account-console-title">アカウント管理</h2>
      <section aria-labelledby="recipient-title">
        <h3 id="recipient-title">配信先管理</h3>
        {loading && <p aria-live="polite">配信先を読み込んでいます…</p>}
        {!loading && channels.length === 0 && !globalError && <p>登録可能なチャネルはありません。</p>}
        <ul className="channel-list">
          {channels.map((channel) => {
            const busy = operation === channel.channelId
            const linked = channel.linkState !== 'unlinked'
            return (
              <li key={channel.channelId} className="channel-card">
                <h4>{channel.channelLabel}</h4>
                <dl>
                  <div><dt>チャネル: </dt><dd>{channel.channelState === 'active' ? '利用可能' : '停止中'}</dd></div>
                  <div><dt>連携状態: </dt><dd>{linkStateLabel[channel.linkState]}</dd></div>
                  <div><dt>友だち状態: </dt><dd>{friendshipLabel[channel.friendshipState]}</dd></div>
                  <div><dt>配信: </dt><dd>{channel.deliveryAvailable ? '配信可能' : '配信不可'}</dd></div>
                </dl>
                <div className="actions">
                  {!linked && <button
                    type="button"
                    disabled={busy || channel.channelState !== 'active'}
                    onClick={() => void runTargetOperation(channel.channelId, () => client.registerRecipient(
                      channel.channelId,
                      getAccessToken() ?? undefined,
                    ))}
                  >{busy ? '登録中…' : '登録'}</button>}
                  {linked && channel.linkState === 'linked_enabled' && <button
                    type="button" className="secondary" disabled={busy}
                    onClick={() => channel.recipientId && void runTargetOperation(
                      channel.channelId,
                      () => client.setRecipientEnabled(channel.recipientId as string, false),
                    )}
                  >{busy ? '変更中…' : '無効化'}</button>}
                  {linked && channel.linkState === 'linked_disabled' && <button
                    type="button" disabled={busy || channel.channelState !== 'active'}
                    onClick={() => channel.recipientId && void runTargetOperation(
                      channel.channelId,
                      () => client.setRecipientEnabled(channel.recipientId as string, true),
                    )}
                  >{busy ? '変更中…' : '再有効化'}</button>}
                  {linked && <button type="button" className="danger" disabled={busy} onClick={() => void unlinkRecipient(channel)}>
                    {busy ? '解除中…' : 'このチャネルとの連携を解除'}
                  </button>}
                </div>
                {targetErrors[channel.channelId] && <p className="field-error" role="alert">{targetErrors[channel.channelId]}</p>}
              </li>
            )
          })}
        </ul>
      </section>

      <section className="unlink-panel" aria-labelledby="unlink-title">
        <h3 id="unlink-title">全連携解除</h3>
        {preview === null ? (
          <>
            <p>保存されたLINE identityとすべての配信先関係を削除します。</p>
            <button type="button" className="danger" disabled={operation !== null} onClick={() => void showPreview()}>
              {operation === 'unlink-preview' ? '確認内容を取得中…' : '全連携解除の内容を確認'}
            </button>
          </>
        ) : (
          <div className="panel uncertain">
            <h4>削除内容の最終確認</h4>
            <p>表示名: {preview.displayName}</p>
            <p>配信先: {preview.recipientCount}件</p>
            {preview.channelLabels.length > 0 && <ul>{preview.channelLabels.map((label) => <li key={label}>{label}</li>)}</ul>}
            <p>配信監査記録は保持されます。</p>
            <p>確認の有効期限: <time dateTime={preview.expiresAt}>{new Date(preview.expiresAt).toLocaleString('ja-JP')}</time></p>
            <div className="actions">
              <button type="button" className="danger" disabled={operation !== null} onClick={() => void executeInitialUnlink()}>
                {operation === 'unlink-execute' ? '解除処理中…' : '確認して全連携解除'}
              </button>
              <button type="button" className="secondary" disabled={operation !== null} onClick={() => setPreview(null)}>キャンセル</button>
            </div>
          </div>
        )}
        {globalError && <p className="notice error" role="alert">{globalError}</p>}
      </section>
    </section>
  )
}
