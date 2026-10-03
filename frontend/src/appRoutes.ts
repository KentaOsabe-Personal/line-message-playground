import { isChannelAdminUuid } from './channelAdminDto'

export type StaticProtectedPath =
  '/liff' | '/liff/channels' | '/liff/account' | '/liff/rich-menus' | '/liff/deliveries'

export type ProtectedAppPath = StaticProtectedPath | `/liff/rich-menus/${string}`

export type NavigationKey = 'home' | 'channels' | 'account' | 'richMenus' | 'deliveries' | null

export type RouteMeta = Readonly<{
  navigationKey: NavigationKey
  title: string
  heading: string
}>

export type RouteMatch =
  | Readonly<{ kind: 'rootRedirect'; path: '/liff'; meta: RouteMeta }>
  | Readonly<{ kind: 'protected'; path: ProtectedAppPath; meta: RouteMeta }>
  | Readonly<{ kind: 'notFound'; path: null; meta: RouteMeta }>

const staticRoutes: Readonly<Record<StaticProtectedPath, RouteMeta>> = Object.freeze({
  '/liff': Object.freeze({
    navigationKey: 'home',
    title: 'LINE Message Playground',
    heading: 'トップ',
  }),
  '/liff/channels': Object.freeze({
    navigationKey: 'channels',
    title: 'チャネル管理 | LINE Message Playground',
    heading: 'チャネル管理',
  }),
  '/liff/account': Object.freeze({
    navigationKey: 'account',
    title: 'アカウント管理 | LINE Message Playground',
    heading: 'アカウント管理',
  }),
  '/liff/rich-menus': Object.freeze({
    navigationKey: 'richMenus',
    title: 'リッチメニュー管理 | LINE Message Playground',
    heading: 'リッチメニュー管理',
  }),
  '/liff/deliveries': Object.freeze({
    navigationKey: 'deliveries',
    title: 'LINEテスト配信 | LINE Message Playground',
    heading: 'LINEテスト配信',
  }),
})

const richMenuMeta: RouteMeta = Object.freeze({
  navigationKey: 'richMenus',
  title: 'リッチメニュー管理 | LINE Message Playground',
  heading: 'リッチメニュー管理',
})

const notFoundMeta: RouteMeta = Object.freeze({
  navigationKey: null,
  title: 'ページが見つかりません | LINE Message Playground',
  heading: 'ページが見つかりません',
})

const richMenuPrefix = '/liff/rich-menus/'

function isStaticProtectedPath(pathname: string): pathname is StaticProtectedPath {
  return Object.hasOwn(staticRoutes, pathname)
}

export function parseProtectedPath(pathname: string): ProtectedAppPath | null {
  if (isStaticProtectedPath(pathname)) return pathname
  if (!pathname.startsWith(richMenuPrefix)) return null
  const channelId = pathname.slice(richMenuPrefix.length)
  return isChannelAdminUuid(channelId) ? `/liff/rich-menus/${channelId}` : null
}

export function richMenuPath(channelId: string): `/liff/rich-menus/${string}` {
  if (!isChannelAdminUuid(channelId)) throw new Error('INVALID_CHANNEL_ID')
  return `/liff/rich-menus/${channelId}`
}

export function classifyRoute(pathname: string): RouteMatch {
  if (pathname === '/') {
    return { kind: 'rootRedirect', path: '/liff', meta: staticRoutes['/liff'] }
  }
  const path = parseProtectedPath(pathname)
  if (path === null) return { kind: 'notFound', path: null, meta: notFoundMeta }
  return {
    kind: 'protected',
    path,
    meta: isStaticProtectedPath(path) ? staticRoutes[path] : richMenuMeta,
  }
}

export function meta(pathname: string): RouteMeta {
  return classifyRoute(pathname).meta
}
