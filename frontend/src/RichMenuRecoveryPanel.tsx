import { useEffect, useRef } from 'react'
import type { ManagedResourceView, ObservationView, OperationKind, OperationView, RichMenuAction } from './richMenuAdminDto'
import { richMenuActionLabels } from './RichMenuStatePanel'

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
  return <section className="panel" aria-labelledby="rich-menu-recovery-heading">
    <h3 id="rich-menu-recovery-heading">回復・管理操作</h3>
    <p>対象チャネル: {channelLabel}</p><p>現在既定との関係: {observation?.kind ?? '未観測'}</p>
    {actions.includes('unlink') && <div><h4>適用解除</h4><p>対象: 現在の管理対象（{currentResource?.lifecycle ?? '確認不能'}）</p><p>LINE既定を外します。利用者の導線が失われる不可逆な外部効果があります。</p><button type="button" disabled={busy || result !== null || currentResource === null} onClick={() => start('unlink')}>適用解除を確定</button></div>}
    {actions.includes('release') && <div><h4>管理終了</h4><p>対象: 現在の管理対象（{currentResource?.lifecycle ?? '確認不能'}）</p><p>LINE既定を維持します。アプリでの管理だけを終了し、自動では元に戻しません。</p><button type="button" disabled={busy || result !== null || currentResource === null} onClick={() => start('release')}>管理終了を確定</button></div>}
    {actions.includes('recheck') && <div><h4>結果の再確認</h4><p>対象: 元操作 {subjectOperation?.operationId ?? '確認不能'} / 段階 {subjectOperation?.stage ?? '未確定'}</p><p>結果不明となった元の操作だけを再確認します。新しい変更操作や自動再試行は開始しません。</p><p>確認中は競合する操作を開始できません。</p><button type="button" disabled={busy || result !== null || subjectOperation === null} onClick={() => start('recheck')}>元の操作を再確認</button></div>}
    {actions.includes('cleanup') && <div><h4>管理資源の後片付け</h4><p>発生元操作: {subjectOperation?.operationId ?? '確認不能'} / 段階 {subjectOperation?.stage ?? '未確定'}</p><p>保存済み操作に結び付いた一件の管理対象資源だけを削除します。この削除は元に戻せません。</p><p>所有権と現在既定が一致しない場合は削除せず、保存結果を表示します。</p>{cleanupResources.map((resource, index) => <button key={resource.resourceId} type="button" disabled={busy || result !== null || subjectOperation === null} onClick={() => start('cleanup', resource.resourceId)}>後片付け対象 {index + 1}（{resource.lifecycle}）を確定</button>)}</div>}
    {result !== null && <div role="status"><p>保存済み{result.kind}操作: {result.operationId}</p><p>段階: {result.stage ?? '完了'} / 状態: {result.status}（{result.result}）</p><p>次の明示操作: {result.nextAllowedActions.length === 0 ? 'なし' : result.nextAllowedActions.map(action => richMenuActionLabels[action]).join('、')}</p></div>}
  </section>
}
