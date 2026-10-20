import type { CurrentReadState } from './richMenuAdminState'

type Props = {
  channelLabel: string
  current: CurrentReadState
  collapsed?: boolean
  onRefresh: () => void
  onImageError: () => void
}
export default function RichMenuCurrentMenu({
  channelLabel,
  current,
  collapsed = false,
  onRefresh,
  onImageError,
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
  return collapsed ? (
    <details className="rich-menu-current">
      <summary>現在のメニュー</summary>
      {content}
    </details>
  ) : (
    <section aria-label="現在のメニュー">
      <h3>現在のメニュー</h3>
      {content}
    </section>
  )
}
