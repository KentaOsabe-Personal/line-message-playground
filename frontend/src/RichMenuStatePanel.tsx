import type { RichMenuAction, RichMenuStateView } from './richMenuAdminDto'

import type { CurrentReadState } from './richMenuAdminState'

const unavailableReasonLabels: Record<string, string> = {
  channel_inactive: 'チャネルが利用できないため変更できません。',
  integration_not_ready: '連携の準備が整っていないため変更できません。',
}

type Props = {
  state?: RichMenuStateView
  readOnly?: boolean
  current?: CurrentReadState
  onRefresh?: () => void
}

export const observationLabels = {
  default_none: '既定なし',
  managed_default: '管理対象が現在の既定',
  other_managed_default: '別の管理対象が現在の既定',
  external_default: 'アプリ外の既定',
  unknown: '結果不明',
} as const
export const operationStatusLabels = {
  accepted: '受付済み',
  processing: '処理中',
  failed: '失敗',
  unknown: '結果を確認できません',
  cleanup_required: '後片付けが必要',
  recovery_active: '確認中',
  succeeded: '完了',
} as const
export const operationKindLabels = {
  apply: 'リッチメニューを反映',
  unlink: '適用を解除',
  release: '管理を終了',
  recheck: '結果を再確認',
  cleanup: '後片付け',
} as const
export const richMenuActionLabels: Record<RichMenuAction, string> = {
  new_preview: '新しいプレビュー',
  apply: '適用',
  unlink: '適用解除',
  release: '管理終了',
  recheck: '結果を再確認',
  cleanup: '後片付け',
  get_state: '最新状態を再取得',
  view_history: '履歴を表示',
  clear_to_disable: '無効化へ進む',
}

export default function RichMenuStatePanel({ state, readOnly = false, current, onRefresh }: Props) {
  const rich = current?.state === 'ready' ? current.value.rich : current ? undefined : state
  const inactive = current?.state === 'ready' && current.value.current.kind === 'inactive'
  const operation = rich?.activeOperation ?? rich?.blockingOperation
  const needsAttention =
    rich?.latestObservation?.kind === 'unknown' ||
    rich?.latestObservation?.kind === 'other_managed_default' ||
    (rich?.latestObservation?.kind === 'default_none' &&
      ['applied', 'cleanup_required'].includes(rich.currentResource?.lifecycle ?? '')) ||
    (current?.state === 'ready' && current.value.current.kind === 'attention')
  const message =
    current?.state === 'loading'
      ? '現在の状態を読み込み中です。'
      : current?.state === 'failed'
        ? '現在の状態を取得できません。更新してください。'
        : current?.state === 'refresh_required'
          ? '状態が変わったため、更新が必要です。'
          : inactive
            ? 'チャネルは停止中です。保存済み情報だけを表示しています。'
            : operation
              ? operationStatusLabels[operation.status]
              : needsAttention
                ? '現在の状態は要確認です。LINE側の変更または未解決の操作を確認してください。'
                : rich?.latestObservation?.kind === 'external_default'
                  ? 'アプリ外で設定されています。LINE側で確認してください。'
                  : rich?.latestObservation?.kind === 'managed_default'
                    ? 'LINE適用済み'
                    : rich?.latestObservation?.kind === 'default_none'
                      ? '確認済み・未設定'
                      : '現在の状態は要確認です。更新してください。'
  const problem =
    current?.state === 'failed' ||
    current?.state === 'refresh_required' ||
    needsAttention ||
    (operation && ['unknown', 'failed', 'cleanup_required'].includes(operation.status))
  const reason =
    (rich?.unavailableReason
      ? (unavailableReasonLabels[rich.unavailableReason] ??
        '現在は変更できません。更新して状態を確認してください。')
      : null) ??
    (readOnly || rich?.mode === 'read_only'
      ? '読取専用のため変更できません。'
      : rich?.mode === 'recovery_only'
        ? '許可された回復操作だけを利用できます。'
        : rich?.cleanupResources.length
          ? '後片付けが完了するまで新しい変更はできません。'
          : null)
  return (
    <section className="rich-menu-state-card" aria-label="リッチメニュー状態">
      <p role={problem ? 'alert' : 'status'}>{message}</p>
      {inactive && rich && (
        <p>
          保存済み管理対象: {rich.currentResource ? 'あり' : 'なし'}
          {operation ? `・未解決の操作: ${operationStatusLabels[operation.status]}` : ''}
        </p>
      )}
      {reason && <p>{reason}</p>}
      {onRefresh && current?.state !== 'loading' && (problem || !rich) && (
        <button onClick={onRefresh}>更新</button>
      )}
      {!inactive && problem && rich && (
        <p>
          次にできる操作:{' '}
          {rich.effectiveActions
            .filter((action) => action !== 'view_history')
            .map((action) => richMenuActionLabels[action])
            .join('、') || '現在実行できる操作はありません。'}
        </p>
      )}
    </section>
  )
}
