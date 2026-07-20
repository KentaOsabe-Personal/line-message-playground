import { describe, expect, test } from 'vitest'

import { createLiffRuntimeConfig, LiffConfigError } from '../src/liffConfig'

describe('LiffRuntimeConfig', () => {
  // テストケース: HTTPS origin、固定path、LIFF IDからruntime設定を生成する。
  // 期待値: LIFF URLとendpoint・redirect URIが単一の入力から一意に導出される。
  test('derives the LIFF and endpoint URLs from canonical inputs', () => {
    expect(createLiffRuntimeConfig({
      liffId: '1234567890-AbCdEf',
      currentOrigin: 'https://example.ngrok-free.app',
      currentPathname: '/liff',
    })).toEqual({
      liffId: '1234567890-AbCdEf',
      liffUrl: 'https://liff.line.me/1234567890-AbCdEf',
      endpointUrl: 'https://example.ngrok-free.app/liff',
      redirectUri: 'https://example.ngrok-free.app/liff',
    })
  })

  // テストケース: LIFF entry URLにLINE復帰用queryとfragmentが付いた状態から設定を導出する。
  // 期待値: 安全性判定はoriginとpathnameだけを使い、query・fragmentを変更せずredirect URIへ混入させない。
  test('ignores query and fragment for safety while preserving the browser URL', () => {
    const browserUrl = new URL('https://example.ngrok-free.app/liff?liff.state=opaque#resume')

    const config = createLiffRuntimeConfig({
      liffId: '1234567890-AbCdEf',
      currentOrigin: browserUrl.origin,
      currentPathname: browserUrl.pathname,
    })

    expect(config.redirectUri).toBe('https://example.ngrok-free.app/liff')
    expect(browserUrl.search).toBe('?liff.state=opaque')
    expect(browserUrl.hash).toBe('#resume')
  })

  // テストケース: 非HTTPS、固定path不一致、空または不正なLIFF IDを渡す。
  // 期待値: SDK初期化に使える設定を返さず、安全な設定エラーで拒否する。
  test.each([
    { liffId: '123-a', currentOrigin: 'http://example.com', currentPathname: '/liff' },
    { liffId: '123-a', currentOrigin: 'https://example.com', currentPathname: '/liff/' },
    { liffId: '', currentOrigin: 'https://example.com', currentPathname: '/liff' },
    { liffId: '123-a?token=secret', currentOrigin: 'https://example.com', currentPathname: '/liff' },
  ])('rejects unsafe configuration %#', (input) => {
    expect(() => createLiffRuntimeConfig(input)).toThrow(LiffConfigError)
  })
})
