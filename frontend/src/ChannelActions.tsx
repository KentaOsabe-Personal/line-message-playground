import { useEffect, useState, type FormEvent } from 'react'

import { ChannelAdminApiError } from './channelAdminApi'
import type { ChannelAdminItem, ConnectionCheck } from './channelAdminDto'

type CredentialRepair = { accessToken: string; channelSecret: string }
type Props = {
  item: ChannelAdminItem
  pending?: boolean
  onSetState: (active: boolean, credentials?: CredentialRepair) => Promise<void>
  onDelete: () => Promise<void>
  onCheck: () => Promise<ConnectionCheck>
}
type Confirmation = 'enable' | 'disable' | 'delete' | null

const connectionLabels: Record<ConnectionCheck['status'], string> = {
  connected: '接続できました',
  credential_unavailable: '資格情報を安全に利用できません',
  authentication_failed: 'アクセストークンが拒否されました',
  identity_mismatch: 'bot identity が一致しません',
  rate_limited: 'LINE の利用制限中です',
  line_unavailable: 'LINE の確認結果を確定できません',
}

const safeFailure = (error: unknown) => error instanceof ChannelAdminApiError
  ? error.error.code === 'channel_referenced'
    ? 'このチャネルには保持すべき履歴があります。削除せず無効化してください。'
    : error.error.summary
  : '操作を完了できませんでした。最新状態を再取得してください。'

export default function ChannelActions({ item, pending = false, onSetState, onDelete, onCheck }: Props) {
  const [confirmation, setConfirmation] = useState<Confirmation>(null)
  const [notification, setNotification] = useState<string | null>(null)
  const [connection, setConnection] = useState<ConnectionCheck | null>(null)

  useEffect(() => {
    setConnection(null)
  }, [item.updatedAt])

  const copyWebhook = async () => {
    try {
      await navigator.clipboard.writeText(item.webhookUrl)
      setNotification('Webhook URLをコピーしました。')
    } catch {
      setNotification('Webhook URLをコピーできませんでした。')
    }
  }

  const checkConnection = async () => {
    if (pending) return
    setNotification(null)
    setConnection(null)
    try {
      setConnection(await onCheck())
    } catch (error) {
      setConnection(null)
      setNotification(safeFailure(error))
    }
  }

  const confirmState = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault()
    if (pending || confirmation === null || confirmation === 'delete') return
    const form = event?.currentTarget
    try {
      let credentials: CredentialRepair | undefined
      if (confirmation === 'enable' && item.credentialsState === 'repair_required' && form !== undefined) {
        const data = new FormData(form)
        const accessToken = String(data.get('accessToken') ?? '')
        const channelSecret = String(data.get('channelSecret') ?? '')
        if (accessToken.trim().length === 0 || channelSecret.trim().length === 0) {
          setNotification('有効化には完全な資格情報ペアを入力してください。')
          return
        }
        credentials = { accessToken, channelSecret }
      }
      await onSetState(confirmation === 'enable', credentials)
      setNotification(confirmation === 'enable' ? 'チャネルを有効にしました。' : 'チャネルを無効にしました。')
      setConfirmation(null)
    } catch (error) {
      setNotification(safeFailure(error))
    } finally {
      form?.reset()
    }
  }

  const confirmDelete = async () => {
    if (pending) return
    try {
      await onDelete()
      setConfirmation(null)
    } catch (error) {
      setNotification(safeFailure(error))
    }
  }

  return (
    <div className="channel-actions">
      <div className="actions">
        <button type="button" className="secondary" onClick={() => { void copyWebhook() }}>Webhook URLをコピー</button>
        <button type="button" className="secondary" disabled={pending} onClick={() => { void checkConnection() }}>接続を確認</button>
        <button type="button" disabled={pending} onClick={() => setConfirmation(item.active ? 'disable' : 'enable')}>
          {item.active ? '無効化' : '有効化'}
        </button>
        <button type="button" className="danger" disabled={pending} onClick={() => setConfirmation('delete')}>削除</button>
      </div>
      {pending && <p role="status">操作を処理しています…</p>}
      {notification !== null && <p className="notice" role="status">{notification}</p>}
      {connection !== null && (
        <section className="connection-result" aria-live="polite">
          <strong>{connectionLabels[connection.status]}</strong>
          <span>確認日時: {new Date(connection.checkedAt).toLocaleString('ja-JP')}</span>
          {connection.status === 'connected' && <small>access token と bot identity の確認のみです。チャネルシークレット、Webhook、配信、端末到達は確認していません。</small>}
        </section>
      )}
      {confirmation !== null && (
        <section className="confirmation" role="dialog" aria-modal="true" aria-label="チャネル操作の確認">
          <h4>{confirmation === 'delete' ? 'チャネル削除の確認' : '状態変更の確認'}</h4>
          <dl>
            <div><dt>名称</dt><dd>{item.label}</dd></div>
            <div><dt>公開ID</dt><dd>{item.channelId}</dd></div>
            <div><dt>現在状態</dt><dd>{item.active ? '有効' : '無効'}</dd></div>
          </dl>
          {confirmation === 'delete' ? (
            <>
              <p className="notice error">この削除は取り消せません。参照中のチャネルは削除せず無効化してください。</p>
              <div className="actions">
                <button type="button" className="danger" disabled={pending} onClick={() => { void confirmDelete() }}>削除を確定</button>
                <button type="button" className="secondary" disabled={pending} onClick={() => setConfirmation(null)}>キャンセル</button>
              </div>
            </>
          ) : (
            <form onSubmit={(event) => { void confirmState(event) }}>
              {confirmation === 'enable' && item.credentialsState === 'repair_required' && (
                <fieldset>
                  <legend>有効化と同時に資格情報を修復</legend>
                  <label>チャネルアクセストークン<input name="accessToken" type="password" autoComplete="off" maxLength={16 * 1024} /></label>
                  <label>チャネルシークレット<input name="channelSecret" type="password" autoComplete="off" maxLength={16 * 1024} /></label>
                </fieldset>
              )}
              <div className="actions">
                <button type="submit" disabled={pending}>{confirmation === 'enable' ? '有効化を確定' : '無効化を確定'}</button>
                <button type="button" className="secondary" disabled={pending} onClick={() => setConfirmation(null)}>キャンセル</button>
              </div>
            </form>
          )}
        </section>
      )}
    </div>
  )
}
