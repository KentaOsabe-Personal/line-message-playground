import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'

import liff from '@line/liff'

import { validatePublicHost } from '../vite.config'
import publicHostFixture from './fixtures/public-hosts.json'

describe('task 1 runtime dependencies and public host', () => {
  // テストケース: package manifestにRouterとTailwindの依存versionを定義する。
  // 期待値: 設計で承認されたexact versionだけが固定される。
  it('pins the router and Tailwind build dependencies at the approved versions', () => {
    const packageJson = JSON.parse(readFileSync('package.json', 'utf8')) as {
      dependencies: Record<string, string>
      devDependencies: Record<string, string>
    }

    expect(packageJson.dependencies['react-router']).toBe('8.3.1')
    expect(packageJson.devDependencies['tailwindcss']).toBe('4.3.3')
    expect(packageJson.devDependencies['@tailwindcss/vite']).toBe('4.3.3')
  })

  // テストケース: ViteのFrontend entry設定を読み取る。
  // 期待値: SPA modeとTailwind Vite plugin、Tailwind stylesheet importが有効になる。
  it('configures the Vite entry as an SPA with the Tailwind plugin', () => {
    const configSource = readFileSync('vite.config.ts', 'utf8')
    const stylesheet = readFileSync('src/style.css', 'utf8')

    expect(configSource).toContain("import tailwindcss from '@tailwindcss/vite'")
    expect(configSource).toContain('plugins: [react(), tailwindcss()]')
    expect(configSource).toContain("appType: 'spa'")
    expect(stylesheet).toMatch(/@import\s+(['"])tailwindcss\1\s*;/)
  })

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
  it.each(publicHostFixture.invalid)('rejects a noncanonical public host: %s', (host) => {
    expect(() => validatePublicHost(host)).toThrow('PUBLIC_HOST_INVALID')
  })

  // テストケース: Backend と Vite が共有する公開host fixtureの正常値を検証する。
  // 期待値: すべてのcanonical hostがexact allowed hostとして保持される。
  it.each(publicHostFixture.valid)('accepts a host from the shared fixture: %s', (host) => {
    expect(validatePublicHost(host)).toBe(host)
  })
})
