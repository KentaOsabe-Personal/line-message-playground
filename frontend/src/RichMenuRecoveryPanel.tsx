import { useEffect, useRef } from 'react'
import type { ManagedResourceView, ObservationView, OperationKind, OperationView, RichMenuAction } from './richMenuAdminDto'
import { operationKindLabels, operationStatusLabels, richMenuActionLabels } from './RichMenuStatePanel'

type RecoveryKind = Extract<OperationKind, 'unlink' | 'release' | 'recheck' | 'cleanup'>
type Props = {
  channelLabel: string; actions: RichMenuAction[]; currentResource: ManagedResourceView | null
  cleanupResources: ManagedResourceView[]; subjectOperation: OperationView | null; observation: ObservationView | null
  busy: boolean; result: OperationView | null; onStart: (kind: RecoveryKind, targetResourceId?: string) => void
}
export default function RichMenuRecoveryPanel({ channelLabel, actions, currentResource, cleanupResources, subjectOperation, observation, busy, result, onStart }: Props) {
  const submitted = useRef(false)
  useEffect(() => { if (!busy) submitted.current = false }, [busy, result])
  const start = (kind: RecoveryKind, targetResourceId?: string) => { if (busy || result !== null || submitted.current || !actions.includes(kind)) return; submitted.current = true; onStart(kind, targetResourceId) }
  if (!actions.some(action => ['unlink', 'release', 'recheck', 'cleanup'].includes(action))) return null
  return <section className={actions.includes('recheck') || actions.includes('cleanup') ? 'recovery-card urgent' : 'recovery-card'} aria-labelledby="rich-menu-recovery-heading">
    <div className="recovery-heading"><div><span className="recovery-icon" aria-hidden="true">!</span><div><p className="eyebrow">対応が必要です</p><h3 id="rich-menu-recovery-heading">{actions.includes('recheck') ? '前回の操作結果を確認してください' : actions.includes('cleanup') ? '未完了のデータを後片付けしてください' : '現在のリッチメニューを管理'}</h3></div></div></div>
    {actions.includes('recheck') && <div className="recovery-action"><p>前回の操作がLINEに届いたか確定できませんでした。元の操作だけを再確認し、新しい反映や自動再試行は行いません。</p><button type="button" disabled={busy || result !== null || subjectOperation === null} onClick={() => start('recheck')}>{busy ? '確認しています…' : '前回の結果を確認'}</button><details className="technical-details"><summary>対象操作</summary><p>{subjectOperation === null ? '確認できません' : `${operationKindLabels[subjectOperation.kind]} / ${operationStatusLabels[subjectOperation.status]} / ${subjectOperation.stage ?? '完了'}`}</p><code>{subjectOperation?.operationId}</code></details></div>}
    {actions.includes('cleanup') && <div className="recovery-action"><p>反映されていない管理データが残っています。保存済み操作に結び付いた一件の管理対象資源だけを削除します。この削除は元に戻せません。</p>{cleanupResources.map((resource, index) => <button className="danger" key={resource.resourceId} type="button" disabled={busy || result !== null || subjectOperation === null} onClick={() => start('cleanup', resource.resourceId)}>残ったデータ {index + 1} を削除</button>)}</div>}
    {(actions.includes('unlink') || actions.includes('release')) && <div className="management-actions">
      {actions.includes('unlink') && <div><h4>LINEでの表示をやめる</h4><p>LINE既定を外します。利用者のメニュー導線が表示されなくなります。</p><button type="button" className="danger" disabled={busy || result !== null || currentResource === null} onClick={() => start('unlink')}>適用解除を確定</button></div>}
      {actions.includes('release') && <div><h4>このアプリでの管理だけをやめる</h4><p>LINE既定を維持します。このアプリの管理対象からだけ外します。</p><button type="button" className="secondary" disabled={busy || result !== null || currentResource === null} onClick={() => start('release')}>管理終了を確定</button></div>}
    </div>}
    {result !== null && <div className="operation-result" role="status"><strong>{operationStatusLabels[result.status]}</strong><p>次にできること: {result.nextAllowedActions.length === 0 ? 'ありません' : result.nextAllowedActions.map(action => richMenuActionLabels[action]).join('、')}</p><details className="technical-details"><summary>操作ID</summary><code>{result.operationId}</code></details></div>}
    <details className="technical-details"><summary>現在の対象情報</summary><p>チャネル: {channelLabel}</p><p>LINE状態: {observation?.kind ?? '未観測'}</p></details>
  </section>
}
