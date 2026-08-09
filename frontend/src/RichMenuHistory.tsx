import { useRef, useState } from 'react'
import type { HistoryEntryView, HistoryPageView } from './richMenuAdminDto'
import { operationKindLabels, operationStatusLabels } from './RichMenuStatePanel'
type Props = { initialPage: HistoryPageView; readOnly: boolean; loadNext: (cursor: string) => Promise<HistoryPageView> }
export default function RichMenuHistory({ initialPage, readOnly, loadNext }: Props) {
  const [items, setItems] = useState<HistoryEntryView[]>(initialPage.items.slice(0, 50))
  const [cursor, setCursor] = useState(initialPage.nextCursor)
  const [loading, setLoading] = useState(false)
  const [incomplete, setIncomplete] = useState(false)
  const latch = useRef(false)
  const append = async () => {
    if (latch.current || cursor === null || items.length >= 50) return
    latch.current = true; setLoading(true); setIncomplete(false)
    try {
      const page = await loadNext(cursor)
      setItems(current => [...current, ...page.items].slice(0, 50))
      setCursor(items.length + page.items.length >= 50 ? null : page.nextCursor)
    } catch { setIncomplete(true) }
    finally { latch.current = false; setLoading(false) }
  }
  const defaultLabels = { became_default: 'LINEに反映済み', cleared_default: '適用解除済み', not_default: '現在は未適用', external_default_preserved: 'LINE側のメニューを維持', unknown: '確認できません' } as const
  const cleanupLabels = { not_required: '不要', required: '必要', completed: '完了', unknown: '確認できません' } as const
  return <section className="rich-menu-history" aria-labelledby="rich-menu-history-heading">
    <h3 id="rich-menu-history-heading">操作履歴{readOnly ? '（読取専用）' : ''}</h3>
    {items.length === 0 ? <p className="empty-note">まだ操作履歴はありません。</p> : <ol className="history-list">{items.map((entry, index) => <li key={`${entry.operation.operationId}-${index}`}>
      <div className="history-heading"><div><p className="eyebrow">{entry.channelLabel}</p><h4>{operationKindLabels[entry.operation.kind]}</h4><time>{new Date(entry.operation.acceptedAt).toLocaleString('ja-JP')}</time></div><span className={`status ${entry.operation.status === 'succeeded' ? 'active' : entry.operation.status === 'unknown' || entry.operation.status === 'cleanup_required' ? 'inactive' : ''}`}>{operationStatusLabels[entry.operation.status]}</span></div>
      <dl className="history-summary">
        <div><dt>LINEへの反映</dt><dd>{defaultLabels[entry.defaultRelation]}</dd></div>
        <div><dt>後片付け</dt><dd>{cleanupLabels[entry.cleanupRelation]}</dd></div>
        <div><dt>受付時点</dt><dd>{new Date(entry.operation.acceptedAt).toLocaleString('ja-JP')}</dd></div>
        <div><dt>完了時点</dt><dd>{entry.operation.completedAt === null ? '未完了' : new Date(entry.operation.completedAt).toLocaleString('ja-JP')}</dd></div>
      </dl>
      {entry.configuration !== null && <div><p>{entry.configuration.templateId} v{entry.configuration.templateVersion}</p><ul>{entry.configuration.fields.map((field, fieldIndex) => <li key={`${fieldIndex}-${field.displayName}`}>{field.displayName}: {field.uri}</li>)}</ul></div>}
      <details className="technical-details"><summary>技術情報</summary><p>操作ID: <code>{entry.operation.operationId}</code></p><p>状態遷移: {entry.transitions.join(' → ')}</p><p>{entry.operation.status}（{entry.operation.result}）</p></details>
    </li>)}</ol>}
    {incomplete && <p role="alert">追加履歴を取得できませんでした。表示中の履歴が完全・最新とは確認できません。</p>}
    {cursor !== null && items.length < 50 && <button type="button" disabled={loading} onClick={() => { void append() }}>{loading ? '履歴を取得中…' : '次の履歴を表示'}</button>}
    {items.length >= 50 && <p>この画面で表示できる履歴の上限（50件）に達しました。</p>}
  </section>
}
