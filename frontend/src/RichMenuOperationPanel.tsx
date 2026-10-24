import { useEffect, useRef, useState } from 'react'
import type { OperationView, PreviewView } from './richMenuAdminDto'
import type { OperationState, PreviewDisplay } from './richMenuAdminState'
import { operationStatusLabels } from './RichMenuStatePanel'

type Props = {
  embedded?: boolean
  onBack?: () => void
  onCancel?: () => void
  channelLabel: string
  preview?: PreviewView | PreviewDisplay | null
  currentDefault?: string
  busy: boolean
  result: OperationView | null
  operation?: OperationState
  onApply: () => void
  valid?: boolean
  now?: string
  refreshFailed?: boolean
  onRefresh?: () => void
  onTrack?: () => void
}
export default function RichMenuOperationPanel({
  embedded = false,
  onBack,
  onCancel,
  channelLabel,
  preview,
  busy,
  result,
  operation,
  onApply,
  valid = true,
  now,
  refreshFailed = false,
  onRefresh,
  onTrack,
}: Props) {
  const submitted = useRef(false)
  const [submittedTokens, setSubmittedTokens] = useState<ReadonlySet<string>>(() => new Set())
  const [clock, setClock] = useState(() => Date.now())
  const deadline = preview ? Date.parse(preview.expiresAt) : NaN
  useEffect(() => {
    if (now !== undefined || !Number.isFinite(deadline)) return
    const timer = window.setInterval(() => setClock(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [deadline, now])
  useEffect(() => {
    if (!busy) submitted.current = false
  }, [busy, result, preview?.confirmationToken])
  const processing = busy || operation?.state === 'submitting' || operation?.state === 'tracking'
  const unknown = operation?.state === 'transport_unknown' || result?.status === 'unknown'
  const canApply = Boolean(
    preview &&
    valid &&
    Number.isFinite(deadline) &&
    (now ? Date.parse(now) : clock) < deadline &&
    preview.observation.kind !== 'unknown' &&
    !processing &&
    !unknown &&
    !submittedTokens.has(preview.confirmationToken) &&
    (result === null || result.status === 'succeeded' || result.status === 'failed'),
  )
  const submit = () => {
    if (submitted.current || !canApply || (Date.now() >= deadline && now === undefined)) return
    submitted.current = true
    setSubmittedTokens((tokens) => new Set(tokens).add(preview!.confirmationToken))
    onApply()
  }
  return (
    <section
      className={`rich-menu-apply-card${embedded ? ' rich-menu-inline-apply' : ''}`}
      aria-label="LINEへの反映と直前結果"
    >
      {preview && (
        <>
          {!embedded && (
            <>
              <h3>LINEに反映</h3>
              <p>
                <strong>{channelLabel}</strong> のリッチメニューとして反映します。
              </p>
            </>
          )}
          {preview.observation.kind === 'external_default' && (
            <p>既定を置き換えますが、アプリ外資源自体は削除しません。</p>
          )}
          <div className="rich-menu-confirm-actions">
            <button type="button" disabled={!canApply} onClick={submit}>
              LINEに反映
            </button>
            {onBack && (
              <button type="button" className="secondary" disabled={busy} onClick={onBack}>
                編集へ戻る
              </button>
            )}
            {onCancel && (
              <button type="button" className="secondary" disabled={busy} onClick={onCancel}>
                キャンセル
              </button>
            )}
          </div>
        </>
      )}
      {processing && <p role="status">操作の結果を確認しています…</p>}
      {result && (
        <p
          role={
            ['failed', 'unknown', 'cleanup_required'].includes(result.status) ? 'alert' : 'status'
          }
        >
          直前の操作: {operationStatusLabels[result.status]}
        </p>
      )}
      {unknown && (
        <div role="alert">
          <p>結果を確認できません。同じ操作の結果を確認してください。自動再送は行いません。</p>
          {onTrack && <button onClick={onTrack}>同じ操作の結果を確認</button>}
        </div>
      )}
      {refreshFailed && (
        <div role="alert">
          <p>現在表示の再取得に失敗しました。直前の操作結果は保持しています。</p>
          {onRefresh && <button onClick={onRefresh}>現在表示を更新</button>}
        </div>
      )}
    </section>
  )
}
