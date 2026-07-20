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
