import { Navigate, Outlet, Route, Routes, useLocation, useNavigate, useOutletContext } from 'react-router'

import AuthGate from './AuthGate'
import type { AuthGateProps } from './AuthGate'
import AppLayout from './AppLayout'
import NotFoundPage from './NotFoundPage'
import { parseProtectedPath } from './appRoutes'
import AccountConsole from './AccountConsole'
import AccountPage from './AccountPage'
import ChannelAdminPage from './ChannelAdminPage'
import DeliveryPage from './DeliveryPage'
import RichMenuAdminPage from './RichMenuAdminPage'
import RichMenuChannelSelectionPage from './RichMenuChannelSelectionPage'
import TextJudgmentLabPage from './TextJudgmentLabPage'
import type { AccountApiClient } from './accountApi'
import type { AuthGateContext } from './AuthGate'
import type { ChannelAdminApiClient } from './channelAdminApi'
import type { LinkedDeliveryApiClient } from './deliveryApi'
import type { RichMenuAdminApiClient } from './richMenuAdminApi'
import type { LabHttpClient } from './textJudgmentLabApi'
import type { TextJudgmentLabAuthGateProps } from './TextJudgmentLabAuthGate'

type AppRouterProps = {
  authGateProps?: Omit<AuthGateProps, 'children' | 'currentPathname' | 'replacePath'>
  featureClients?: Readonly<{
    channelApi?: ChannelAdminApiClient
    accountApi?: AccountApiClient
    deliveryApi?: LinkedDeliveryApiClient
    richMenuApi?: RichMenuAdminApiClient
    textJudgmentLabApi?: LabHttpClient
  }>
  textJudgmentLabAuthGateProps?: Omit<TextJudgmentLabAuthGateProps, 'children' | 'api'>
}

function RichMenuSelectionRoute({ api }: { api?: ChannelAdminApiClient }) {
  const context = useOutletContext<AuthGateContext>()
  return <RichMenuChannelSelectionPage api={api} onSessionInvalid={context.refreshSession} />
}

function RichMenuDetailRoute({ channelApi, richApi }: { channelApi?: ChannelAdminApiClient; richApi?: RichMenuAdminApiClient }) {
  const context = useOutletContext<AuthGateContext>()
  return <RichMenuAdminPage channelApi={channelApi} richApi={richApi} onSessionInvalid={context.refreshSession} />
}

function ChannelRoute({ api }: { api?: ChannelAdminApiClient }) {
  const context = useOutletContext<AuthGateContext>()
  return <ChannelAdminPage api={api} onSessionInvalid={context.refreshSession} />
}

function AccountRoute({ api }: { api?: AccountApiClient }) {
  const context = useOutletContext<AuthGateContext>()
  return <AccountPage context={context} api={api} />
}

function DeliveryRoute({ api }: { api?: LinkedDeliveryApiClient }) {
  const context = useOutletContext<AuthGateContext>()
  return <DeliveryPage linkedClient={api} onSessionInvalid={context.refreshSession} />
}

function AuthenticatedApplication({ authGateProps }: AppRouterProps) {
  const location = useLocation()
  const navigate = useNavigate()
  const isRichMenuCandidate = /^\/liff\/rich-menus\/[^/]+$/.test(location.pathname)
  if (parseProtectedPath(location.pathname) === null && !isRichMenuCandidate) return <NotFoundPage />
  return (
    <AuthGate
      {...authGateProps}
      currentPathname={location.pathname}
      replacePath={(path) => navigate(path, { replace: true })}
    >
      {(context) => context.session.state === 'unlinking'
        ? <AccountConsole {...context} />
        : (
            <AppLayout displayName={context.session.profile.displayName} onLogout={context.logout}>
              <Outlet context={context} />
            </AppLayout>
          )}
    </AuthGate>
  )
}

export function AppRouter({ authGateProps, featureClients, textJudgmentLabAuthGateProps }: AppRouterProps) {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/liff" replace />} />
      <Route path="/labs/text-judgment" element={<Navigate to="/liff/labs/text-judgment" replace />} />
      <Route path="/liff/labs/text-judgment" element={<TextJudgmentLabPage api={featureClients?.textJudgmentLabApi} authGateProps={textJudgmentLabAuthGateProps} />} />
      <Route path="/liff" element={<AuthenticatedApplication authGateProps={authGateProps} />}>
        <Route index element={<Navigate to="channels" replace />} />
        <Route path="channels" element={<ChannelRoute api={featureClients?.channelApi} />} />
        <Route path="account" element={<AccountRoute api={featureClients?.accountApi} />} />
        <Route path="rich-menus" element={<RichMenuSelectionRoute api={featureClients?.channelApi} />} />
        <Route path="rich-menus/:channelId" element={<RichMenuDetailRoute channelApi={featureClients?.channelApi} richApi={featureClients?.richMenuApi} />} />
        <Route path="deliveries" element={<DeliveryRoute api={featureClients?.deliveryApi} />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}

export default function App() {
  return <AppRouter />
}
