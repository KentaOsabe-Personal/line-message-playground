import { useEffect, useRef, useState } from 'react'
import type {
  ManagedResourceView,
  ObservationView,
  OperationView,
  RichMenuAction,
  RecoveryTarget,
  RichMenuStateView,
} from './richMenuAdminDto'
import type { RecoveryRequest } from './useRichMenuAdmin'
import { operationStatusLabels } from './RichMenuStatePanel'

type Props = {
  channelLabel: string
  actions: RichMenuAction[]
  currentResource: ManagedResourceView | null
  cleanupResources: ManagedResourceView[]
  subjectOperation: OperationView | null
  observation: ObservationView | null
  busy: boolean
  result: OperationView | null
  recoveryTargets?: RecoveryTarget[]
  onRecovery?: (request: RecoveryRequest) => void
  active?: boolean
  mode?: RichMenuStateView['mode']
  deactivationPending?: boolean
  onStart?: (kind: RecoveryRequest['kind'], targetResourceId?: string) => void
}
export default function RichMenuRecoveryPanel({
  channelLabel,
  actions,
  currentResource,
  cleanupResources,
  subjectOperation,
  observation,
  busy,
  result,
  recoveryTargets = [],
  onRecovery,
  active = true,
  mode = 'enabled',
  deactivationPending = false,
  onStart,
}: Props) {
  const submitted = useRef(false)
  const [confirmation, setConfirmation] = useState<{
    kind: 'unlink' | 'release'
    resourceId: string
    channelLabel: string
  } | null>(null)
  useEffect(() => {
    if (!busy) submitted.current = false
  }, [busy, result])
  const allowed = actions.filter(
    (action) =>
      active &&
      mode !== 'read_only' &&
      mode !== 'unavailable' &&
      (!(deactivationPending || mode === 'recovery_only') ||
        ['recheck', 'cleanup'].includes(action)),
  )
  const targets = recoveryTargets.filter(
    (target) =>
      cleanupResources.some((resource) => resource.resourceId === target.targetResourceId) &&
      target.targetResourceId !== currentResource?.resourceId,
  )
  const start = (request: RecoveryRequest) => {
    if (busy || submitted.current || !allowed.includes(request.kind)) return
    if (
      (request.kind === 'unlink' || request.kind === 'release') &&
      (request.targetResourceId !== currentResource?.resourceId ||
        !confirmation ||
        confirmation.resourceId !== request.targetResourceId ||
        confirmation.channelLabel !== channelLabel)
    )
      return
    if (request.kind === 'recheck' && request.subjectOperationId !== subjectOperation?.operationId)
      return
    if (
      request.kind === 'cleanup' &&
      !targets.some(
        (target) =>
          target.subjectOperationId === request.subjectOperationId &&
          target.targetResourceId === request.targetResourceId,
      )
    )
      return
    submitted.current = true
    if (onRecovery) onRecovery(request)
    else
      onStart?.(request.kind, 'targetResourceId' in request ? request.targetResourceId : undefined)
    setConfirmation(null)
  }
  if (!allowed.some((action) => ['unlink', 'release', 'recheck', 'cleanup'].includes(action)))
    return null
  const management = (kind: 'unlink' | 'release') => {
    if (!allowed.includes(kind) || !currentResource) return null
    const confirming =
      confirmation?.kind === kind &&
      confirmation.resourceId === currentResource.resourceId &&
      confirmation.channelLabel === channelLabel
    return (
      <div>
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() =>
            setConfirmation({ kind, resourceId: currentResource.resourceId, channelLabel })
          }
        >
          {kind === 'unlink' ? '適用解除' : '管理終了'}
        </button>
        {confirming && (
          <div role="group" aria-label={`${kind === 'unlink' ? '適用解除' : '管理終了'}の確認`}>
            <p>対象チャネル: {channelLabel}</p>
            <p>
              管理対象:{' '}
              <span style={{ overflowWrap: 'anywhere' }}>{currentResource.resourceId}</span>
            </p>
            {kind === 'unlink' ? (
              <p>
                LINE既定を外します。管理対象資源を削除します。アプリ外メニューの表示が再開する可能性があります。削除に失敗した場合は後片付けが必要です。
              </p>
            ) : (
              <p>
                LINE上の現在既定と資源は変更・削除しません。このアプリによる追跡を終了します。以後このアプリから管理・削除できず、自動的に管理対象へ戻りません。履歴は保持します。
              </p>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() => start({ kind, targetResourceId: currentResource.resourceId })}
            >
              {kind === 'unlink' ? '適用解除を確定' : '管理終了を確定'}
            </button>
            <button type="button" disabled={busy} onClick={() => setConfirmation(null)}>
              取消
            </button>
          </div>
        )}
      </div>
    )
  }
  return (
    <section className="recovery-card" aria-label="リッチメニューの管理と回復">
      {allowed.includes('recheck') && (
        <div>
          <p role="alert">結果が不明です。元の操作だけを再確認し、自動再送は行いません。</p>
          {subjectOperation ? (
            <button
              disabled={busy}
              onClick={() =>
                start({ kind: 'recheck', subjectOperationId: subjectOperation.operationId })
              }
            >
              前回の結果を確認
            </button>
          ) : (
            <p>確認対象との関係を特定できません。最新状態を取得してください。</p>
          )}
        </div>
      )}
      {allowed.includes('cleanup') && (
        <div>
          <p>
            現在の既定を変更せず、許可された一件の管理対象資源だけを削除します。この削除は元に戻せません。
          </p>
          {targets.length === 0 ? (
            <p role="alert">後片付け対象との関係を確認できません。最新状態を取得してください。</p>
          ) : (
            targets.map((target, index) => (
              <div key={`${target.subjectOperationId}:${target.targetResourceId}`}>
                <p style={{ overflowWrap: 'anywhere' }}>対象: {target.targetResourceId}</p>
                <button
                  disabled={busy}
                  onClick={() =>
                    start({
                      kind: 'cleanup',
                      subjectOperationId: target.subjectOperationId,
                      targetResourceId: target.targetResourceId,
                    })
                  }
                >
                  残ったデータ {index + 1} を削除
                </button>
              </div>
            ))
          )}
        </div>
      )}
      {management('unlink')}
      {allowed.includes('release') &&
        (observation?.kind === 'managed_default' ? (
          <details>
            <summary>管理終了の補助操作</summary>
            {management('release')}
          </details>
        ) : (
          <div>
            <p role="alert">
              外部変更の可能性があります。現在の状態を確認してから管理終了を選んでください。
            </p>
            {management('release')}
          </div>
        ))}
      {busy && <p role="status">処理しています…</p>}
      {result && (
        <p
          role={
            ['unknown', 'failed', 'cleanup_required'].includes(result.status) ? 'alert' : 'status'
          }
        >
          直前の操作: {operationStatusLabels[result.status]}
        </p>
      )}
    </section>
  )
}
