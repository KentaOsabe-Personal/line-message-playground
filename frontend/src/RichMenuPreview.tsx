import { useEffect, useState } from 'react'

import type { PreviewView } from './richMenuAdminDto'

type Props = { preview: PreviewView; imageUrl: string; templateName: string; now?: string }

const observationLabels: Record<PreviewView['observation']['kind'], string> = {
  default_none: '現在のリッチメニューなし', managed_default: 'このアプリで管理中', other_managed_default: '別の管理メニューを適用中',
  external_default: 'LINE側で作成したメニューを適用中', unknown: '確認できません',
}

export default function RichMenuPreview({ preview, imageUrl, templateName, now }: Props) {
  const [clock, setClock] = useState(() => Date.now())
  const expiresAt = new Date(preview.expiresAt).getTime()
  const currentTime = now === undefined ? clock : new Date(now).getTime()
  const remainingMs = expiresAt - currentTime
  const expired = remainingMs <= 0
  const remainingMinutes = Math.max(1, Math.ceil(remainingMs / 60_000))

  useEffect(() => {
    if (now !== undefined || remainingMs <= 0) return
    const interval = window.setInterval(() => {
      const current = Date.now()
      setClock(current)
      if (current >= expiresAt) window.clearInterval(interval)
    }, Math.min(60_000, remainingMs))
    return () => window.clearInterval(interval)
  }, [expiresAt, now])

  const openLink = (uri: string) => {
    const opened = window.open(uri, '_blank', 'noopener,noreferrer')
    if (opened !== null) opened.opener = null
  }

  return (
    <section className="rich-menu-preview" aria-labelledby="rich-menu-preview-heading">
      <div className="card-heading"><div><span className="step-number">2</span><div><p className="eyebrow">表示を確認</p><h3 id="rich-menu-preview-heading">プレビュー</h3></div></div><span className={expired ? 'status inactive' : 'status active'}>{expired ? '期限切れ' : `残り ${remainingMinutes}分`}</span></div>
      <div className="rich-menu-image-frame"><img src={imageUrl} width={preview.image.width} height={preview.image.height} alt={`${preview.channelLabel} のリッチメニュープレビュー`} /></div>
      {preview.warnings.includes('external_default_replaced') && <p role="alert">外部の既定リッチメニューを置き換える可能性があります。</p>}
      <ul className="preview-link-list">
        {preview.fields.map((field, index) => (
          <li key={`${field.displayName}:${index}`}>
            <div><strong>{field.displayName}</strong><span>{field.uri}</span></div>
            <button type="button" className="secondary compact" onClick={() => openLink(field.uri)}>リンクを開く</button>
          </li>
        ))}
      </ul>
      {preview.observation.kind === 'unknown'
        ? <p role="alert">実状態を安全に確認できないため、新しいプレビューを生成してください。</p>
        : expired
        ? <p role="alert">期限切れです。新しいプレビューを生成してください。</p>
        : <p className="preview-ready" role="status">適用可能です。内容を確認したら「LINEに反映」へ進んでください。</p>}
      <details className="technical-details"><summary>確認情報</summary><dl>
        <div><dt>対象</dt><dd>{preview.channelLabel}</dd></div>
        <div><dt>レイアウト</dt><dd>{templateName}（{preview.templateId} / 版 {preview.templateVersion}）</dd></div>
        <div><dt>現在のLINE状態</dt><dd>{observationLabels[preview.observation.kind]}</dd></div>
        <div><dt>有効期限</dt><dd>{new Date(preview.expiresAt).toLocaleString('ja-JP')}</dd></div>
      </dl><p>リンク先の到達や表示結果は保証・保存しません。</p></details>
    </section>
  )
}
