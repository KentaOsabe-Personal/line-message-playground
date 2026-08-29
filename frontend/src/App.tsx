import { Link, Navigate, Outlet, Route, Routes, useLocation, useNavigate, useParams } from 'react-router'

import AuthGate from './AuthGate'
import type { AuthGateProps } from './AuthGate'
import { parseProtectedPath } from './appRoutes'
import AccountConsole from './AccountConsole'

type AppRouterProps = {
  authGateProps?: Omit<AuthGateProps, 'children' | 'currentPathname' | 'replacePath'>
}

function RouteScreen({ name }: { name: string }) {
  return <section aria-label={name}><h1>{name}</h1></section>
}

function RichMenuDetailRoute() {
  const { channelId = '' } = useParams()
  if (parseProtectedPath(`/liff/rich-menus/${channelId}`) === null) return <NotFoundPage />
  return <RouteScreen name="リッチメニュー管理画面" />
}

function NotFoundPage() {
  return (
    <main>
      <h1>ページが見つかりません</h1>
      <Link to="/liff">トップへ戻る</Link>
    </main>
  )
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
        : <Outlet />}
    </AuthGate>
  )
}

export function AppRouter({ authGateProps }: AppRouterProps) {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/liff" replace />} />
      <Route path="/liff" element={<AuthenticatedApplication authGateProps={authGateProps} />}>
        <Route index element={<RouteScreen name="トップ" />} />
        <Route path="channels" element={<RouteScreen name="チャネル管理画面" />} />
        <Route path="account" element={<RouteScreen name="アカウント管理画面" />} />
        <Route path="rich-menus" element={<RouteScreen name="リッチメニュー選択画面" />} />
        <Route path="rich-menus/:channelId" element={<RichMenuDetailRoute />} />
        <Route path="deliveries" element={<RouteScreen name="LINEテスト配信画面" />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}

export default function App() {
  return <AppRouter />
}
