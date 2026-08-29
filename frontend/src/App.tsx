import { Navigate, Outlet, Route, Routes, useLocation, useNavigate, useOutletContext, useParams } from 'react-router'

import AuthGate from './AuthGate'
import type { AuthGateProps } from './AuthGate'
import AppLayout from './AppLayout'
import HomePage from './HomePage'
import NotFoundPage from './NotFoundPage'
import PageFrame from './PageFrame'
import { meta, parseProtectedPath } from './appRoutes'
import AccountConsole from './AccountConsole'
import AccountPage from './AccountPage'
import ChannelAdminPage from './ChannelAdminPage'
import DeliveryPage from './DeliveryPage'
import type { AccountApiClient } from './accountApi'
import type { AuthGateContext } from './AuthGate'
import type { ChannelAdminApiClient } from './channelAdminApi'
import type { LinkedDeliveryApiClient } from './deliveryApi'

type AppRouterProps = {
  authGateProps?: Omit<AuthGateProps, 'children' | 'currentPathname' | 'replacePath'>
  featureClients?: Readonly<{
    channelApi?: ChannelAdminApiClient
    accountApi?: AccountApiClient
    deliveryApi?: LinkedDeliveryApiClient
  }>
}

function RouteScreen() {
  const location = useLocation()
  const pageMeta = meta(location.pathname)
  return (
    <PageFrame title={pageMeta.title} heading={pageMeta.heading} routeFocusKey={location.pathname}>
      <section aria-label={`${pageMeta.heading}コンテンツ`} />
    </PageFrame>
  )
}

function RichMenuDetailRoute() {
  const { channelId = '' } = useParams()
  if (parseProtectedPath(`/liff/rich-menus/${channelId}`) === null) return <NotFoundPage />
  return <RouteScreen />
}

function ChannelRoute({ api }: { api?: ChannelAdminApiClient }) {
  const context = useOutletContext<AuthGateContext>()
  return <ChannelAdminPage api={api} onSessionInvalid={() => { void context.refreshSession() }} />
}

function AccountRoute({ api }: { api?: AccountApiClient }) {
  const context = useOutletContext<AuthGateContext>()
  return <AccountPage context={context} api={api} />
}

function DeliveryRoute({ api }: { api?: LinkedDeliveryApiClient }) {
  const context = useOutletContext<AuthGateContext>()
  return <DeliveryPage linkedClient={api} onSessionInvalid={() => { void context.refreshSession() }} />
}

function AuthenticatedApplication({ authGateProps }: AppRouterProps) {
  const location = useLocation()
  const navigate = useNavigate()
  if (parseProtectedPath(location.pathname) === null) return <NotFoundPage />
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

export function AppRouter({ authGateProps, featureClients }: AppRouterProps) {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/liff" replace />} />
      <Route path="/liff" element={<AuthenticatedApplication authGateProps={authGateProps} />}>
        <Route index element={<HomePage />} />
        <Route path="channels" element={<ChannelRoute api={featureClients?.channelApi} />} />
        <Route path="account" element={<AccountRoute api={featureClients?.accountApi} />} />
        <Route path="rich-menus" element={<RouteScreen />} />
        <Route path="rich-menus/:channelId" element={<RichMenuDetailRoute />} />
        <Route path="deliveries" element={<DeliveryRoute api={featureClients?.deliveryApi} />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}

export default function App() {
  return <AppRouter />
}
