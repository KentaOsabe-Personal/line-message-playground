import { Navigate, Outlet, Route, Routes, useLocation, useNavigate, useParams } from 'react-router'

import AuthGate from './AuthGate'
import type { AuthGateProps } from './AuthGate'
import AppLayout from './AppLayout'
import HomePage from './HomePage'
import NotFoundPage from './NotFoundPage'
import PageFrame from './PageFrame'
import { meta, parseProtectedPath } from './appRoutes'
import AccountConsole from './AccountConsole'

type AppRouterProps = {
  authGateProps?: Omit<AuthGateProps, 'children' | 'currentPathname' | 'replacePath'>
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
              <Outlet />
            </AppLayout>
          )}
    </AuthGate>
  )
}

export function AppRouter({ authGateProps }: AppRouterProps) {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/liff" replace />} />
      <Route path="/liff" element={<AuthenticatedApplication authGateProps={authGateProps} />}>
        <Route index element={<HomePage />} />
        <Route path="channels" element={<RouteScreen />} />
        <Route path="account" element={<RouteScreen />} />
        <Route path="rich-menus" element={<RouteScreen />} />
        <Route path="rich-menus/:channelId" element={<RichMenuDetailRoute />} />
        <Route path="deliveries" element={<RouteScreen />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}

export default function App() {
  return <AppRouter />
}
