import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { Link } from 'react-router'

import ChannelActions from './ChannelActions'
import ChannelEditor from './ChannelEditor'
import { ChannelAdminApiError, createChannelAdminApiClient } from './channelAdminApi'
import type { ChannelAdminApiClient, CreateChannelInput, UpdateChannelInput } from './channelAdminApi'
import type { ChannelAdminItem } from './channelAdminDto'
import { initialChannelAdminState, transitionChannelAdmin } from './channelAdminState'
import { createProtectedHttpClient } from './httpApi'
import { richMenuPath } from './appRoutes'

type Props = {
  api?: ChannelAdminApiClient
  onSessionInvalid?: () => void
  onNavigateRichMenu?: (channelId: string) => void
}

const safeError = (error: unknown) => error instanceof ChannelAdminApiError
  ? error.error
  : { code: 'network_error', summary: 'Backendに接続できません。' }

const formatDate = (value: string | null) => value === null ? '未記録' : new Date(value).toLocaleString('ja-JP')

export default function ChannelAdminConsole({ api: providedApi, onSessionInvalid, onNavigateRichMenu }: Props) {
  const [state, dispatch] = useReducer(transitionChannelAdmin, initialChannelAdminState)
  const [showCreate, setShowCreate] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const generation = useRef(0)
  const readController = useRef<AbortController | null>(null)
  const operationLocks = useRef(new Set<string>())
  const api = useMemo(() => providedApi ?? createChannelAdminApiClient(createProtectedHttpClient({ onSessionInvalid })), [providedApi, onSessionInvalid])

  const load = useCallback(async () => {
    readController.current?.abort()
    const controller = new AbortController()
    readController.current = controller
    const currentGeneration = ++generation.current
    setNotice(null)
    dispatch({ type: 'loadStarted', generation: currentGeneration })
    try {
      const items = await api.listChannels({ signal: controller.signal })
      if (controller.signal.aborted) return
      dispatch({ type: 'loadSucceeded', generation: currentGeneration, items })
    } catch {
      if (controller.signal.aborted) return
      dispatch({
        type: 'loadFailed', generation: currentGeneration,
        error: { code: 'load_failed', summary: 'チャネル一覧を取得できませんでした。' },
      })
    }
  }, [api])

  useEffect(() => {
    void load()
    return () => { generation.current += 1; readController.current?.abort() }
  }, [load])

  const operations = state.state === 'ready' || state.state === 'empty' ? state.operations : {}
  const mutate = async (key: string, request: () => Promise<ChannelAdminItem>, success: string) => {
    if (operationLocks.current.has(key)) return false
    operationLocks.current.add(key)
    dispatch({ type: 'operationStarted', key })
    setNotice(null)
    const operationGeneration = generation.current
    try {
      const item = await request()
      if (generation.current !== operationGeneration) return false
      dispatch({ type: 'mutationSucceeded', key, item })
      setNotice(success)
      setShowCreate(false)
      setEditingId(null)
      return true
    } catch (error) {
      if (generation.current !== operationGeneration) return false
      dispatch({ type: 'operationFailed', key, error: safeError(error) })
      throw error
    } finally {
      operationLocks.current.delete(key)
    }
  }

  const register = (input: CreateChannelInput | UpdateChannelInput) => mutate(
    'create', () => api.register(input as CreateChannelInput), 'チャネルを登録しました。',
  ).then(() => undefined)
  const update = (item: ChannelAdminItem, input: CreateChannelInput | UpdateChannelInput) => mutate(
    `${item.channelId}:update`, () => api.update(item.channelId, input as UpdateChannelInput), 'チャネル情報を更新しました。',
  ).then(() => undefined)
  const setState = async (item: ChannelAdminItem, active: boolean, credentials?: { accessToken: string; channelSecret: string }) => {
    const completed = await mutate(
      `${item.channelId}:state`,
      () => api.setState(item.channelId, { expectedUpdatedAt: item.updatedAt, active, ...credentials }),
      active ? 'チャネルを有効にしました。' : 'チャネルを無効にしました。',
    )
    if (completed && active && item.providerId !== null) onNavigateRichMenu?.(item.channelId)
  }
  const deleteChannel = async (item: ChannelAdminItem) => {
    const key = `${item.channelId}:delete`
    if (operationLocks.current.has(key)) return
    operationLocks.current.add(key)
    dispatch({ type: 'operationStarted', key })
    setNotice(null)
    const operationGeneration = generation.current
    try {
      const deleted = await api.delete(item.channelId, item.updatedAt)
      if (generation.current !== operationGeneration) return
      dispatch({ type: 'deleteSucceeded', key, channelId: deleted.channelId })
      setNotice(`${deleted.label} を削除しました。`)
    } catch (error) {
      if (generation.current !== operationGeneration) return
      dispatch({ type: 'operationFailed', key, error: safeError(error) })
      throw error
    } finally {
      operationLocks.current.delete(key)
    }
  }
  const check = async (item: ChannelAdminItem) => {
    const key = `${item.channelId}:check`
    if (operationLocks.current.has(key)) throw new Error('duplicate operation')
    operationLocks.current.add(key)
    dispatch({ type: 'operationStarted', key })
    const operationGeneration = generation.current
    try {
      const result = await api.checkConnection(item.channelId)
      if (generation.current !== operationGeneration) throw new Error('stale operation result')
      dispatch({ type: 'operationCompleted', key })
      return result
    } catch (error) {
      if (generation.current !== operationGeneration) throw error
      dispatch({ type: 'operationFailed', key, error: safeError(error) })
      throw error
    } finally {
      operationLocks.current.delete(key)
    }
  }

  return (
    <section className="channel-admin" aria-labelledby="channel-admin-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">CHANNELS</p>
          <h2 id="channel-admin-heading">登録チャネル</h2>
          <p>配信やリッチメニューに使用するチャネルを管理します。</p>
        </div>
        {(state.state === 'empty' || state.state === 'ready') && <button type="button" onClick={() => setShowCreate(true)}>新しいチャネルを登録</button>}
      </div>
      {notice !== null && <p className="panel success" role="status">{notice}</p>}
      {(state.state === 'idle' || state.state === 'loading') && <p className="panel progress" role="status">チャネル一覧を読み込んでいます…</p>}
      {state.state === 'load_failed' && (
        <div className="panel error" role="alert"><p>{state.error.summary}</p><button type="button" onClick={() => { void load() }}>再取得</button></div>
      )}
      {state.state === 'refresh_required' && (
        <div className="panel uncertain" role="alert">
          <p>操作結果を確定できません。最新状態を再取得してから、必要な操作をやり直してください。</p>
          <button type="button" onClick={() => { void load() }}>最新状態を再取得</button>
        </div>
      )}
      {state.state === 'empty' && !showCreate && <div className="empty-state"><p>登録済みチャネルはありません。</p><button type="button" onClick={() => setShowCreate(true)}>新しいチャネルを登録</button></div>}
      {(state.state === 'empty' || state.state === 'ready') && showCreate && (
        <ChannelEditor mode="create" pending={operations.create !== undefined} onSubmit={register} onCancel={() => setShowCreate(false)} />
      )}
      {state.state === 'ready' && (
        <div className="channel-list">
          {state.items.map((item) => {
            const operationPending = Object.keys(state.operations).some((key) => key.startsWith(`${item.channelId}:`))
            const richMenuEligible = item.providerId !== null
            const lifecyclePending = item.deactivationSummary !== null && item.deactivationSummary.status !== 'completed'
            return (
              <article className="channel-card" key={item.channelId}>
                <div className="channel-card-heading">
                  <div>
                    <p className="card-kicker">Messaging API channel</p>
                    <h3>{item.label}</h3>
                  </div>
                  <span className={item.active ? 'status active' : 'status inactive'}>{item.active ? '有効' : '無効'}</span>
                </div>
                {!item.active && <p className="notice error">無効中です。新しい配信、配信先登録、Webhook受付には利用できません。</p>}
                {lifecyclePending && <p className="notice" role="status">無効化 {item.deactivationSummary?.status}。競合するチャネル変更はできません。専用画面で保存状態を確認してください。</p>}
                <dl className="channel-summary">
                  <div><dt>資格情報</dt><dd>{item.credentialsState === 'configured' ? '設定済み' : '資格情報の修復が必要'}</dd></div>
                  <div><dt>リッチメニュー</dt><dd>{richMenuEligible ? '管理可能' : '利用不可'}</dd></div>
                  <div><dt>最終更新</dt><dd>{formatDate(item.updatedAt)}</dd></div>
                </dl>
                <details className="resource-details">
                  <summary>技術情報とWebhookを表示</summary>
                  <dl className="channel-details">
                    <div><dt>公開ID</dt><dd>{item.channelId}</dd></div>
                    <div><dt>Messaging API channel ID</dt><dd>{item.messagingApiChannelId}</dd></div>
                    <div><dt>bot user ID</dt><dd>{item.botUserId}</dd></div>
                    <div><dt>provider ID</dt><dd>{item.providerId ?? 'legacy（未設定）'}</dd></div>
                    <div><dt>資格情報更新日時</dt><dd>{formatDate(item.credentialsUpdatedAt)}</dd></div>
                    <div><dt>作成日時</dt><dd>{formatDate(item.createdAt)}</dd></div>
                    <div><dt>Webhook URL</dt><dd><code>{item.webhookUrl}</code></dd></div>
                  </dl>
                </details>
                <div className="channel-primary-actions">
                  <button type="button" className="secondary" disabled={operationPending || lifecyclePending} onClick={() => setEditingId(item.channelId)}>設定を編集</button>
                  {richMenuEligible && onNavigateRichMenu !== undefined && (
                    <Link className="button-link" to={richMenuPath(item.channelId)}>リッチメニューを管理</Link>
                  )}
                </div>
                {editingId === item.channelId && !lifecyclePending && <ChannelEditor mode="edit" item={item} pending={operations[`${item.channelId}:update`] !== undefined} onSubmit={(input) => update(item, input)} onCancel={() => setEditingId(null)} />}
                <ChannelActions
                  item={item}
                  pending={operationPending}
                  onSetState={(active, credentials) => setState(item, active, credentials)}
                  onDelete={() => deleteChannel(item)}
                  onCheck={() => check(item)}
                  onManageLifecycle={richMenuEligible && onNavigateRichMenu !== undefined
                    ? () => onNavigateRichMenu(item.channelId)
                    : undefined}
                />
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}
