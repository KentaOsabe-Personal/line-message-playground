import { useEffect, useState } from 'react'

import type { PreviewView } from './richMenuAdminDto'

type Props = { preview: PreviewView; imageUrl: string; templateName: string; now?: string }

const observationLabels: Record<PreviewView['observation']['kind'], string> = {
  default_none: 'default_none', managed_default: 'managed_default', other_managed_default: 'other_managed_default',
  external_default: 'external_default', unknown: 'unknown',
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
    <section aria-labelledby="rich-menu-preview-heading">
      <h3 id="rich-menu-preview-heading">期限付きプレビュー</h3>
      <dl>
        <div><dt>対象チャネル</dt><dd>{preview.channelLabel}</dd></div>
        <div><dt>テンプレート</dt><dd>{templateName}（{preview.templateId}） / 版 {preview.templateVersion}</dd></div>
        <div><dt>LINE実状態</dt><dd>{observationLabels[preview.observation.kind]}</dd></div>
        <div><dt>有効期限</dt><dd>{new Date(preview.expiresAt).toLocaleString('ja-JP')}</dd></div>
      </dl>
      <img src={imageUrl} width={preview.image.width} height={preview.image.height} alt={`${preview.channelLabel} のリッチメニュープレビュー`} />
      {preview.warnings.includes('external_default_replaced') && <p role="alert">外部の既定リッチメニューを置き換える可能性があります。</p>}
      <ul>
        {preview.fields.map((field, index) => (
          <li key={`${field.displayName}:${index}`}>
            <strong>{field.displayName}</strong> <span>{field.uri}</span>{' '}
            <button type="button" onClick={() => openLink(field.uri)}>このリンクを確認</button>
          </li>
        ))}
      </ul>
      {preview.observation.kind === 'unknown'
        ? <p role="alert">実状態を安全に確認できないため、新しいプレビューを生成してください。</p>
        : expired
        ? <p role="alert">期限切れです。新しいプレビューを生成してください。</p>
        : <p role="status">適用可能 / 残り {remainingMinutes}分</p>}
      <p>リンク先の到達や表示結果は保証・保存しません。</p>
    </section>
  )
}
