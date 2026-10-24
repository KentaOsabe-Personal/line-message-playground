import type { ReactNode } from 'react'
import type { CurrentReadState } from './richMenuAdminState'
import RichMenuDesignIcon from './RichMenuDesignIcon'

type Props = {
  channelLabel: string
  current: CurrentReadState
  collapsed?: boolean
  onRefresh: () => void
  onImageError: () => void
  actions?: ReactNode
}
export default function RichMenuCurrentMenu({
  channelLabel,
  current,
  collapsed = false,
  onRefresh,
  onImageError,
  actions,
}: Props) {
  const content =
    current.state === 'loading' ? (
      <p role="status">現在のメニューを読み込み中です。</p>
    ) : current.state !== 'ready' ? (
      <>
        <p role="alert">現在のメニューを確認できません。更新してください。</p>
        <button onClick={onRefresh}>更新</button>
      </>
    ) : current.value.current.kind === 'managed' ? (
      current.value.current.image.status === 'available' ? (
        <div className="rich-menu-image-frame">
          <img
            src={current.value.current.image.imageUrl}
            width={current.value.current.image.width}
            height={current.value.current.image.height}
            style={{ width: '100%', height: 'auto' }}
            alt={`${channelLabel} の現在のメニュー`}
            onError={onImageError}
          />
        </div>
      ) : (
        <>
          <p role="alert">
            現在のメニュー画像を取得できません。許可された操作は引き続き利用できます。
          </p>
          <button onClick={onRefresh}>画像を再取得</button>
        </>
      )
    ) : (
      <p role={current.value.current.kind === 'attention' ? 'alert' : 'status'}>
        {
          {
            unset: 'チャネル既定のメニューは未設定です。',
            external: 'アプリ外で設定されたメニューです。LINE側で確認してください。',
            attention: '現在の状態は要確認です。以前の画像は表示しません。',
            inactive:
              'チャネルは停止中です。保存済み情報だけを表示し、LINE上の現在状態は確認しません。',
          }[current.value.current.kind]
        }
      </p>
    )
  const managed =
    current.state === 'ready' && current.value.current.kind === 'managed'
      ? current.value.current
      : null
  const caption =
    managed?.configuration.status === 'available'
      ? `${managed.image.status === 'available' ? (managed.image.height > 843 ? 'ラージ・' : 'コンパクト・') : ''}${managed.configuration.fields.length}項目`
      : null
  const applied =
    managed &&
    current.state === 'ready' &&
    current.value.rich.latestObservation?.kind === 'managed_default' &&
    !current.value.rich.activeOperation &&
    !current.value.rich.blockingOperation &&
    current.value.rich.cleanupResources.length === 0
  return collapsed ? (
    <details className="rich-menu-current">
      <summary>
        <span>現在のメニュー{caption && <small>{caption}</small>}</span>
        {applied && (
          <span className="rich-menu-state-badge" aria-hidden="true">
            <RichMenuDesignIcon kind="check" />
            適用中
          </span>
        )}
      </summary>
      {content}
    </details>
  ) : (
    <section className="rich-menu-current-card" aria-label="現在のメニュー">
      <header className="rich-menu-card-heading">
        <h3>現在のメニュー</h3>
        {caption && <p>{caption}</p>}
      </header>
      {content}
      {actions}
    </section>
  )
}
