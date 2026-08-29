import { describe, expect, test } from 'vitest'

import { classifyRoute, meta, parseProtectedPath, richMenuPath } from '../src/appRoutes'

const channelId = '123e4567-e89b-42d3-a456-426614174000'

describe('RouteRegistry', () => {
  // テストケース: 定義済みstatic pathをroute registryへ渡す。
  // 期待値: pathごとに一意なnavigation key、title、h1 metadataを返す。
  test.each([
    ['/liff', 'home', 'LINE Message Playground', 'トップ'],
    ['/liff/channels', 'channels', 'チャネル管理 | LINE Message Playground', 'チャネル管理'],
    ['/liff/account', 'account', 'アカウント管理 | LINE Message Playground', 'アカウント管理'],
    ['/liff/rich-menus', 'richMenus', 'リッチメニュー管理 | LINE Message Playground', 'リッチメニュー管理'],
    ['/liff/deliveries', 'deliveries', 'LINEテスト配信 | LINE Message Playground', 'LINEテスト配信'],
  ] as const)('accepts the exact static route %s with unique metadata', (path, navigationKey, title, heading) => {
    expect(parseProtectedPath(path)).toBe(path)
    expect(meta(path)).toEqual({ navigationKey, title, heading })
  })

  // テストケース: canonical UUIDと非canonical UUIDからrich-menu pathを生成する。
  // 期待値: canonical UUIDだけを安全な動的pathとして生成・解析する。
  test('builds and parses a rich-menu path only for a canonical UUID', () => {
    const path = `/liff/rich-menus/${channelId}`

    expect(richMenuPath(channelId)).toBe(path)
    expect(parseProtectedPath(path)).toBe(path)
    expect(meta(path)).toEqual({
      navigationKey: 'richMenus',
      title: 'リッチメニュー管理 | LINE Message Playground',
      heading: 'リッチメニュー管理',
    })
    expect(() => richMenuPath(channelId.toUpperCase())).toThrow('INVALID_CHANNEL_ID')
  })

  // テストケース: query、hash、外部URL、encoded traversal、未知pathを復帰先として解析する。
  // 期待値: すべて許可済みpathではない値として拒否する。
  test.each([
    '/liff/channels/',
    '/liff/channels/extra',
    '/liff?next=/liff/account',
    '/liff#account',
    'https://example.com/liff',
    '//example.com/liff',
    '/liff/%2e%2e/account',
    `/liff/rich-menus/${channelId}/history`,
    '/liff/rich-menus/not-a-uuid',
  ])('rejects unsafe or unknown return path %s', (path) => {
    expect(parseProtectedPath(path)).toBeNull()
  })

  // テストケース: root pathと未知pathをroute registryで分類する。
  // 期待値: rootだけをredirectとし、未知pathは自動遷移しない404に分類する。
  test('classifies root redirect and unknown paths without redirecting unknown paths', () => {
    expect(classifyRoute('/')).toMatchObject({ kind: 'rootRedirect', path: '/liff' })
    expect(classifyRoute('/unknown')).toEqual({
      kind: 'notFound',
      path: null,
      meta: {
        navigationKey: null,
        title: 'ページが見つかりません | LINE Message Playground',
        heading: 'ページが見つかりません',
      },
    })
  })
})
