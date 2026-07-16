import { describe, expect, it } from 'vitest'

import liff from '@line/liff'

import { validatePublicHost } from '../vite.config'

describe('task 1 runtime dependencies and public host', () => {
  // テストケース: 固定した LIFF SDK を module として import する。
  // 期待値: SDK の init API を参照できる。
  it('imports the pinned LIFF SDK', () => {
    expect(typeof liff.init).toBe('function')
  })

  // テストケース: canonical な単一 ASCII hostname を Vite 設定へ渡す。
  // 期待値: exact allowed host としてそのまま受理される。
  it('accepts a canonical public host', () => {
    expect(validatePublicHost('example.ngrok.app')).toBe('example.ngrok.app')
  })

  // テストケース: scheme、port、path、wildcard、空白等を Vite 設定へ渡す。
  // 期待値: すべて設定エラーとして拒否される。
  it.each([
    '',
    'https://example.ngrok.app',
    'example.ngrok.app:443',
    'example.ngrok.app/liff',
    '*.ngrok.app',
    ' example.ngrok.app',
    'example.ngrok.app ',
    'example..ngrok.app',
    '-example.ngrok.app',
    'example_.ngrok.app',
    '例え.jp',
  ])('rejects a noncanonical public host: %s', (host) => {
    expect(() => validatePublicHost(host)).toThrow('PUBLIC_HOST_INVALID')
  })
})
