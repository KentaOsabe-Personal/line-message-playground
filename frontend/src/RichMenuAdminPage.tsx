import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router'

import { meta } from './appRoutes'
import { ChannelAdminApiError, createChannelAdminApiClient } from './channelAdminApi'
import type { ChannelAdminApiClient } from './channelAdminApi'
import type { ChannelAdminItem } from './channelAdminDto'
import { isChannelAdminUuid } from './channelAdminDto'
import { createProtectedHttpClient } from './httpApi'
import PageFrame from './PageFrame'
import RichMenuAdminConsole from './RichMenuAdminConsole'
import { createRichMenuAdminApiClient } from './richMenuAdminApi'
import type { RichMenuAdminApiClient } from './richMenuAdminApi'

type Props = Readonly<{
  channelApi?: ChannelAdminApiClient
  richApi?: RichMenuAdminApiClient
  onSessionInvalid?: () => void
}>

type DetailState =
  | Readonly<{ kind: 'loading' }>
  | Readonly<{ kind: 'notFound' }>
  | Readonly<{ kind: 'failed' }>
  | Readonly<{ kind: 'providerMissing'; channel: ChannelAdminItem }>
  | Readonly<{ kind: 'ready'; channel: ChannelAdminItem }>

const hiddenLookupCodes = new Set(['channel_not_found', 'owner_operation_blocked', 'provider_mismatch'])

export default function RichMenuAdminPage({ channelApi: suppliedChannelApi, richApi: suppliedRichApi, onSessionInvalid }: Props) {
  const { channelId = '' } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const routeMeta = meta(location.pathname)
  const generation = useRef(0)
  const [readController] = useState(() => new AbortController())
  const [state, setState] = useState<DetailState>({ kind: 'loading' })
  const http = useMemo(() => createProtectedHttpClient({ onSessionInvalid }), [onSessionInvalid])
  const channelApi = useMemo(() => suppliedChannelApi ?? createChannelAdminApiClient(http), [http, suppliedChannelApi])
  const richApi = useMemo(() => suppliedRichApi ?? createRichMenuAdminApiClient(http), [http, suppliedRichApi])

  const loadChannel = useCallback(async () => {
    const current = ++generation.current
    if (!isChannelAdminUuid(channelId)) {
      setState({ kind: 'notFound' })
      return
    }
    setState({ kind: 'loading' })
    try {
      const channel = await channelApi.getChannel(channelId, { signal: readController.signal })
      if (generation.current !== current) return
      if (channel.channelId !== channelId) {
        setState({ kind: 'notFound' })
      } else if (channel.providerId === null) {
        setState({ kind: 'providerMissing', channel })
      } else {
        setState({ kind: 'ready', channel })
      }
    } catch (error) {
      if (generation.current !== current) return
      if (error instanceof ChannelAdminApiError && error.error.code === 'authentication_required') {
        onSessionInvalid?.()
        return
      }
      if (error instanceof ChannelAdminApiError && hiddenLookupCodes.has(error.error.code)) {
        setState({ kind: 'notFound' })
        return
      }
      setState({ kind: 'failed' })
    }
  }, [channelApi, channelId, onSessionInvalid, readController.signal])

  useEffect(() => () => readController.abort(), [readController])

  useEffect(() => {
    void loadChannel()
    return () => { generation.current += 1 }
  }, [loadChannel])

  const title = state.kind === 'ready' || state.kind === 'providerMissing'
    ? `${state.channel.label} | リッチメニュー管理`
    : routeMeta.title

  return (
    <PageFrame title={title} heading="リッチメニュー管理" routeFocusKey={location.pathname}>
      <Link className="back-link" to="/liff/rich-menus">チャネル選択へ戻る</Link>
      {state.kind === 'loading' && <p role="status">対象チャネルを確認しています…</p>}
      {state.kind === 'notFound' && <p role="alert">対象が見つかりません。</p>}
      {state.kind === 'failed' && (
        <div role="alert"><p>対象チャネルを取得できませんでした。</p><button type="button" onClick={() => { void loadChannel() }}>再取得</button></div>
      )}
      {state.kind === 'providerMissing' && (
        <section aria-label="リッチメニュー管理を利用できません">
          <h2>{state.channel.label}</h2>
          <p>provider IDが設定されていません。リッチメニュー管理は利用できません。</p>
          <Link to="/liff/channels">チャネル設定を確認</Link>
        </section>
      )}
      {state.kind === 'ready' && (
        <RichMenuAdminConsole
          channelId={state.channel.channelId}
          channelApi={channelApi}
          richApi={richApi}
          readSignal={readController.signal}
          onSessionInvalid={onSessionInvalid}
          onBack={() => setState({ kind: 'notFound' })}
          onDeleted={() => navigate('/liff/rich-menus')}
        />
      )}
    </PageFrame>
  )
}
