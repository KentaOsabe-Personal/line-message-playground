import { useLocation } from 'react-router'

import AccountConsole from './AccountConsole'
import type { AccountApiClient } from './accountApi'
import type { AuthGateContext } from './AuthGate'
import PageFrame from './PageFrame'
import { meta } from './appRoutes'

export type AccountPageProps = Readonly<{
  context: AuthGateContext
  api?: AccountApiClient
}>

export default function AccountPage({ context, api }: AccountPageProps) {
  const location = useLocation()
  const pageMeta = meta(location.pathname)

  return (
    <PageFrame
      title={pageMeta.title}
      heading={pageMeta.heading}
      description="LINEアカウントとの連携状態と、チャネルごとの配信先を管理します。"
      routeFocusKey={location.pathname}
    >
      <AccountConsole {...context} api={api} />
    </PageFrame>
  )
}
