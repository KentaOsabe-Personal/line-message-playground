import { useEffect, useRef } from 'react'
import type { OperationView, PreviewView } from './richMenuAdminDto'
import { richMenuActionLabels } from './RichMenuStatePanel'

type Props = {
  channelLabel: string; preview: PreviewView; currentDefault: string
  busy: boolean; result: OperationView | null; onApply: () => void
}

export default function RichMenuOperationPanel({ channelLabel, preview, currentDefault, busy, result, onApply }: Props) {
  const submitted = useRef(false)
  useEffect(() => { if (!busy) submitted.current = false }, [busy, result])
  const submit = () => { if (busy || submitted.current) return; submitted.current = true; onApply() }
  return <section className="panel" aria-labelledby="rich-menu-apply-heading">
    <h3 id="rich-menu-apply-heading">適用の最終確認</h3>
    <dl>
      <div><dt>対象チャネル</dt><dd>{channelLabel}</dd></div>
      <div><dt>テンプレート</dt><dd>{preview.templateId} v{preview.templateVersion}</dd></div>
      <div><dt>現在既定</dt><dd>{currentDefault}</dd></div>
    </dl>
    <ul>{preview.fields.map((field, index) => <li key={`${index}-${field.displayName}`}>{field.displayName}: {field.uri}</li>)}</ul>
    {preview.observation.kind === 'external_default' && <p>適用すると既定を置き換えますが、アプリ外資源自体は削除しません。</p>}
    <button type="button" disabled={busy || result !== null} onClick={submit}>{busy ? '開始中…' : result !== null ? '保存済み操作を表示中' : '適用を確定'}</button>
    {result !== null && <div role="status"><p>保存済み適用操作: {result.operationId}</p><p>段階: {result.stage ?? '完了'} / 状態: {result.status}（{result.result}）</p><p>次の明示操作: {result.nextAllowedActions.length === 0 ? 'なし' : result.nextAllowedActions.map(action => richMenuActionLabels[action]).join('、')}</p></div>}
  </section>
}
