import { useEffect, useRef } from 'react'
import type { OperationView, PreviewView } from './richMenuAdminDto'
import { operationStatusLabels, richMenuActionLabels } from './RichMenuStatePanel'

type Props = {
  channelLabel: string; preview: PreviewView; currentDefault: string
  busy: boolean; result: OperationView | null; onApply: () => void
}

export default function RichMenuOperationPanel({ channelLabel, preview, currentDefault, busy, result, onApply }: Props) {
  const submitted = useRef(false)
  useEffect(() => { if (!busy) submitted.current = false }, [busy, result])
  const submit = () => { if (busy || submitted.current) return; submitted.current = true; onApply() }
  return <section className="rich-menu-apply-card" aria-labelledby="rich-menu-apply-heading">
    <div className="card-heading"><div><span className="step-number">3</span><div><p className="eyebrow">公開する</p><h3 id="rich-menu-apply-heading">LINEに反映</h3></div></div></div>
    <p><strong>{channelLabel}</strong> のリッチメニューとして反映します。</p>
    {preview.observation.kind === 'external_default' && <p className="notice warning">適用すると既定を置き換えますが、アプリ外資源自体は削除しません。現在LINE側で設定されているリッチメニューから切り替わります。</p>}
    <button type="button" className="primary-action" disabled={busy || result !== null} onClick={submit}>{busy ? '反映しています…' : result !== null ? '操作結果を確認してください' : 'この内容をLINEに反映'}</button>
    {result !== null && <div className={`operation-result ${result.status === 'succeeded' ? 'success' : result.status === 'unknown' ? 'uncertain' : ''}`} role="status"><strong>{operationStatusLabels[result.status]}</strong><p>次にできること: {result.nextAllowedActions.length === 0 ? 'ありません' : result.nextAllowedActions.map(action => richMenuActionLabels[action]).join('、')}</p><details className="technical-details"><summary>操作IDを確認</summary><code>{result.operationId}</code></details></div>}
    <details className="technical-details"><summary>反映内容の詳細</summary><p>{preview.templateId} v{preview.templateVersion} / 現在の状態: {currentDefault}</p><ul>{preview.fields.map((field, index) => <li key={`${index}-${field.displayName}`}>{field.displayName}: {field.uri}</li>)}</ul></details>
  </section>
}
