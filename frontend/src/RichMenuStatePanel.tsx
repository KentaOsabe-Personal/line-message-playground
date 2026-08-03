import type { RichMenuAction, RichMenuStateView } from './richMenuAdminDto'

type Props = { state: RichMenuStateView; readOnly: boolean }

const observationLabels = {
  default_none: '既定なし', managed_default: '管理対象が現在の既定',
  other_managed_default: '別の管理対象が現在の既定', external_default: 'アプリ外の既定', unknown: '結果不明',
} as const
const modeLabels = { read_only: '読取専用', recovery_only: '回復操作のみ', enabled: '通常提供', unavailable: '利用不可' } as const
export const richMenuActionLabels: Record<RichMenuAction, string> = {
  new_preview: '新しいプレビュー', apply: '適用', unlink: '適用解除', release: '管理終了',
  recheck: '結果を再確認', cleanup: '後片付け', get_state: '最新状態を再取得',
  view_history: '履歴を表示', clear_to_disable: '無効化へ進む',
}

export default function RichMenuStatePanel({ state, readOnly }: Props) {
  const operation = state.activeOperation ?? state.blockingOperation
  const observation = (() => {
    if (state.latestObservation === null) return '未観測'
    if (state.latestObservation.kind !== 'default_none') return observationLabels[state.latestObservation.kind]
    if (state.currentResource?.lifecycle === 'applied' || state.currentResource?.lifecycle === 'cleanup_required') return 'LINE既定なし（外部解除または未解決結果の可能性）'
    return '既定なし（保存状態と整合）'
  })()
  const blockedReason = state.unavailableReason ?? (state.mode === 'recovery_only'
    ? '未解決操作または後片付けがあるため、明示された回復操作以外は禁止されています。'
    : state.cleanupResources.length > 0 ? '管理資源の後片付けが完了するまで競合操作は禁止されています。'
      : state.latestObservation?.kind === 'other_managed_default' ? '別の管理対象が既定のため、最新状態に対して許可された操作だけ実行できます。' : null)
  return (
    <section className="panel" aria-labelledby="rich-menu-state-heading">
      <h3 id="rich-menu-state-heading">リッチメニュー状態</h3>
      <dl>
        <div><dt>保存状態</dt><dd>{state.currentResource === null ? '管理対象なし' : `管理対象（${state.currentResource.lifecycle}）`}</dd></div>
        <div><dt>LINE実状態</dt><dd>{observation}</dd></div>
        <div><dt>提供モード</dt><dd>{readOnly ? '読取専用' : modeLabels[state.mode]}</dd></div>
        <div><dt>操作状態</dt><dd>{operation === null ? '処理中の操作なし' : `${richMenuActionLabels[operation.kind]}: ${operation.status}`}</dd></div>
        <div><dt>後片付け</dt><dd>{state.cleanupResources.length === 0 ? '不要' : `${state.cleanupResources.length}件が必要`}</dd></div>
      </dl>
      {state.latestObservation?.kind === 'external_default' && <p>アプリ外資源の内容や所有権は推測しません。LINE側で状態を確認してください。</p>}
      {state.latestObservation?.kind === 'unknown' && <p>結果を推測せず、明示的な再確認が必要です。</p>}
      {blockedReason !== null && <p role="alert">禁止理由: {blockedReason}</p>}
      {!readOnly && <div aria-label="実行可能な操作"><h4>実行可能な操作</h4>{state.effectiveActions.length === 0
        ? <p>現在実行できる操作はありません。</p>
        : <ul>{state.effectiveActions.map(action => <li key={action}>{richMenuActionLabels[action]}</li>)}</ul>}</div>}
    </section>
  )
}
