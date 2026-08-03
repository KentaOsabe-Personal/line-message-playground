import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react'

import { createChannelAdminApiClient } from './channelAdminApi'
import type { ChannelAdminApiClient } from './channelAdminApi'
import { createProtectedHttpClient } from './httpApi'
import { createRichMenuAdminApiClient } from './richMenuAdminApi'
import type { RichMenuAdminApiClient } from './richMenuAdminApi'
import { initialRichMenuAdminState, transitionRichMenuAdmin } from './richMenuAdminState'
import type { RichMenuAction } from './richMenuAdminDto'

type Props = {
  channelId: string
  channelApi?: ChannelAdminApiClient
  richApi?: RichMenuAdminApiClient
  invalidated?: boolean
  onSessionInvalid?: () => void
}

const actionLabels: Record<RichMenuAction, string> = {
  new_preview: '新しいプレビュー', apply: '適用', unlink: '適用解除', release: '管理終了',
  recheck: '結果を再確認', cleanup: '後片付け', get_state: '最新状態を再取得',
  view_history: '履歴を表示', clear_to_disable: '無効化へ進む',
}

export default function RichMenuAdminConsole({ channelId, channelApi: suppliedChannelApi, richApi: suppliedRichApi, invalidated = false, onSessionInvalid }: Props) {
  const [state, dispatch] = useReducer(transitionRichMenuAdmin, initialRichMenuAdminState)
  const generation = useRef(0)
  const http = useMemo(() => createProtectedHttpClient({ onSessionInvalid }), [onSessionInvalid])
  const channelApi = useMemo(() => suppliedChannelApi ?? createChannelAdminApiClient(http), [suppliedChannelApi, http])
  const richApi = useMemo(() => suppliedRichApi ?? createRichMenuAdminApiClient(http), [suppliedRichApi, http])

  const load = useCallback(async () => {
    const current = ++generation.current
    dispatch({ type: 'loadStarted', generation: current })
    try {
      const [channel, templates, rich, history, deactivation] = await Promise.all([
        channelApi.getChannel(channelId), richApi.listTemplates(), richApi.getState(channelId),
        richApi.getHistory(channelId), richApi.getDeactivation(channelId),
      ])
      const scoped = channel.channelId === channelId && rich.channelId === channelId &&
        history.items.every(item => item.channelId === channelId) &&
        (deactivation === null || deactivation.channelId === channelId)
      if (!scoped) {
        dispatch({ type: 'loadFailed', generation: current, error: { code: 'protocol_error', summary: '応答の対象を確認できません。' } })
        return
      }
      dispatch({ type: 'loadSucceeded', generation: current, value: { channel, templates, rich, history, deactivation } })
    } catch {
      dispatch({ type: 'loadFailed', generation: current, error: { code: 'load_failed', summary: '管理状態を取得できませんでした。' } })
    }
  }, [channelApi, channelId, richApi])

  useEffect(() => {
    if (invalidated) { generation.current += 1; dispatch({ type: 'sessionInvalidated' }); return }
    void load()
    return () => { generation.current += 1 }
  }, [invalidated, load])

  if (invalidated) return null
  if (state.state === 'idle' || state.state === 'loading') return <section aria-label="リッチメニュー管理"><p role="status">管理状態を読み込んでいます…</p></section>
  if (state.state === 'load_failed') return (
    <section aria-label="リッチメニュー管理"><div role="alert"><p>{state.error.summary}</p><button type="button" onClick={() => { void load() }}>最新状態を再取得</button></div></section>
  )
  if (state.state === 'refresh_required') return (
    <section aria-label="リッチメニュー管理"><div role="alert"><p>操作結果を確定できません。最新状態を再取得してください。</p><button type="button" onClick={() => { void load() }}>最新状態を再取得</button></div></section>
  )
  const readOnly = state.state === 'read_only'
  return (
    <section className="rich-menu-admin" aria-labelledby="rich-menu-admin-heading">
      <header><p className="eyebrow">Rich menu console</p><h2 id="rich-menu-admin-heading">{state.channel.label}</h2></header>
      <dl>
        <div><dt>チャネル状態</dt><dd>{state.channel.active ? '有効' : '無効'}</dd></div>
        <div><dt>提供モード</dt><dd>{readOnly ? '読取専用' : state.rich.mode}</dd></div>
        <div><dt>更新時点</dt><dd>{new Date(state.channel.updatedAt).toLocaleString('ja-JP')}</dd></div>
        <div><dt>履歴件数</dt><dd>{state.rich.historySummary.totalCount}</dd></div>
      </dl>
      {state.deactivation !== null && <p>無効化状態: {state.deactivation.status}</p>}
      {!readOnly && (
        <div aria-label="許可された操作">
          <h3>許可された操作</h3>
          {state.rich.effectiveActions.length === 0
            ? <p>現在実行できる操作はありません。</p>
            : <ul>{state.rich.effectiveActions.map(action => <li key={action}>{actionLabels[action]}</li>)}</ul>}
        </div>
      )}
      {readOnly && <p role="status">このチャネルは保存済み状態だけを表示する読取専用です。</p>}
      <button type="button" onClick={() => { void load() }}>最新状態を再取得</button>
    </section>
  )
}
