import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router'

import { meta, richMenuPath } from './appRoutes'
import { ChannelAdminApiError, createChannelAdminApiClient } from './channelAdminApi'
import type { ChannelAdminApiClient } from './channelAdminApi'
import type { ChannelAdminItem } from './channelAdminDto'
import { createProtectedHttpClient } from './httpApi'
import PageFrame from './PageFrame'

export type RichMenuChannelChoice = Readonly<{
  channelId: string
  label: string
  stateLabel: string
  mode: 'editable' | 'readOnly' | 'unavailable' | 'recoveryOnly'
  unavailableReason: string | null
}>

export function projectRichMenuChannelChoice(item: ChannelAdminItem): RichMenuChannelChoice {
  if (item.providerId === null) {
    return {
      channelId: item.channelId,
      label: item.label,
      stateLabel: item.active ? '設定が必要' : '停止中・設定が必要',
      mode: 'unavailable',
      unavailableReason: 'provider IDが設定されていません。チャネル管理で設定してください。',
    }
  }
  const lifecyclePending = item.deactivationSummary !== null && item.deactivationSummary.status !== 'completed'
  if (lifecyclePending) {
    return {
      channelId: item.channelId,
      label: item.label,
      stateLabel: '回復操作のみ',
      mode: 'recoveryOnly',
      unavailableReason: 'チャネルのライフサイクル処理中です。保存状態が許す回復操作だけを利用できます。',
    }
  }
  if (!item.active) {
    return {
      channelId: item.channelId,
      label: item.label,
      stateLabel: '停止中・読み取り専用',
      mode: 'readOnly',
      unavailableReason: null,
    }
  }
  return {
    channelId: item.channelId,
    label: item.label,
    stateLabel: '管理可能',
    mode: 'editable',
    unavailableReason: null,
  }
}

type Props = Readonly<{
  api?: ChannelAdminApiClient
  onSessionInvalid?: () => void
}>

type SelectionState =
  | Readonly<{ kind: 'loading' }>
  | Readonly<{ kind: 'failed' }>
  | Readonly<{ kind: 'ready'; choices: RichMenuChannelChoice[] }>

export default function RichMenuChannelSelectionPage({ api: suppliedApi, onSessionInvalid }: Props) {
  const location = useLocation()
  const pageMeta = meta(location.pathname)
  const generation = useRef(0)
  const [state, setState] = useState<SelectionState>({ kind: 'loading' })
  const api = useMemo(
    () => suppliedApi ?? createChannelAdminApiClient(createProtectedHttpClient({ onSessionInvalid })),
    [onSessionInvalid, suppliedApi],
  )

  const load = useCallback(async () => {
    const current = ++generation.current
    setState({ kind: 'loading' })
    try {
      const items = await api.listChannels()
      if (generation.current !== current) return
      setState({ kind: 'ready', choices: items.map(projectRichMenuChannelChoice) })
    } catch (error) {
      if (generation.current !== current) return
      if (error instanceof ChannelAdminApiError && error.error.code === 'authentication_required') {
        onSessionInvalid?.()
        return
      }
      setState({ kind: 'failed' })
    }
  }, [api, onSessionInvalid])

  useEffect(() => {
    void load()
    return () => { generation.current += 1 }
  }, [load])

  return (
    <PageFrame title={pageMeta.title} heading={pageMeta.heading} routeFocusKey={location.pathname}>
      <section className="rich-menu-channel-selection" aria-label="リッチメニュー管理対象チャネル">
        {state.kind === 'loading' && <p role="status">チャネル一覧を読み込んでいます…</p>}
        {state.kind === 'ready' && <p className="sr-only" role="status">{state.choices.length}件のチャネルを表示しました。</p>}
        {state.kind === 'failed' && (
          <div role="alert">
            <p>チャネル一覧を取得できませんでした。</p>
            <button type="button" onClick={() => { void load() }}>再取得</button>
          </div>
        )}
        {state.kind === 'ready' && state.choices.length === 0 && (
          <div className="empty-state">
            <p>登録済みチャネルはありません。</p>
            <Link to="/liff/channels">チャネル管理へ移動</Link>
          </div>
        )}
        {state.kind === 'ready' && state.choices.length > 0 && (
          <div className="channel-list">
            {state.choices.map((choice) => (
              <article className="channel-card" key={choice.channelId}>
                <div className="channel-card-heading">
                  <h2>{choice.label}</h2>
                  <span className={`status ${choice.mode === 'editable' ? 'active' : 'inactive'}`}>{choice.stateLabel}</span>
                </div>
                {choice.unavailableReason !== null && <p>{choice.unavailableReason}</p>}
                {choice.mode === 'editable' && <Link className="button-link" to={richMenuPath(choice.channelId)}>リッチメニューを管理</Link>}
                {choice.mode === 'readOnly' && <Link className="button-link secondary" to={richMenuPath(choice.channelId)}>保存状態と履歴を確認</Link>}
                {choice.mode === 'recoveryOnly' && <Link className="button-link secondary" to={richMenuPath(choice.channelId)}>保存状態と回復操作を確認</Link>}
                {choice.mode === 'unavailable' && <Link to="/liff/channels">チャネル設定を確認</Link>}
              </article>
            ))}
          </div>
        )}
      </section>
    </PageFrame>
  )
}
