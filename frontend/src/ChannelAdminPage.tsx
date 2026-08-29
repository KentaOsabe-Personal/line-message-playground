import { useLocation, useNavigate } from 'react-router'

import ChannelAdminConsole from './ChannelAdminConsole'
import type { ChannelAdminApiClient } from './channelAdminApi'
import PageFrame from './PageFrame'
import { meta } from './appRoutes'
import { richMenuPath } from './appRoutes'

export type ChannelAdminPageProps = Readonly<{
  api?: ChannelAdminApiClient
  onSessionInvalid?: () => void
}>

export default function ChannelAdminPage({ api, onSessionInvalid }: ChannelAdminPageProps) {
  const location = useLocation()
  const navigate = useNavigate()
  const pageMeta = meta(location.pathname)

  return (
    <PageFrame title={pageMeta.title} heading={pageMeta.heading} routeFocusKey={location.pathname}>
      <ChannelAdminConsole
        api={api}
        onSessionInvalid={onSessionInvalid}
        onNavigateRichMenu={(channelId) => navigate(richMenuPath(channelId))}
      />
    </PageFrame>
  )
}
