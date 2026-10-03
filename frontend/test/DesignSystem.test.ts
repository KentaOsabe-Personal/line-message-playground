import { readFileSync } from 'node:fs'

import { describe, expect, test } from 'vitest'

const stylesheet = readFileSync(`${process.cwd()}/src/style.css`, 'utf8')
const authStylesheet = readFileSync(`${process.cwd()}/src/auth-login.css`, 'utf8')
const themeBlock = stylesheet.match(/@theme\s*\{([\s\S]*?)\}/)?.[1] ?? ''
const implementationCss = stylesheet.replace(/@theme\s*\{[\s\S]*?\}/, '')

const themeColor = (name: string) => {
  const value = themeBlock.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`))?.[1]
  if (value === undefined) throw new Error(`theme color not found: ${name}`)
  return value
}

const relativeLuminance = (hex: string) => {
  const channels = [1, 3, 5].map(
    (offset) => Number.parseInt(hex.slice(offset, offset + 2), 16) / 255,
  )
  const linear = channels.map((value) =>
    value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
  )
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
}

const contrast = (first: string, second: string) => {
  const values = [relativeLuminance(first), relativeLuminance(second)].sort(
    (left, right) => right - left,
  )
  return (values[0] + 0.05) / (values[1] + 0.05)
}

describe('全画面design system', () => {
  // テストケース: 全画面で使うTailwind theme tokenを読み込む。
  // 期待値: 色、余白、角丸、影、focusの共通tokenが一つのthemeで定義される。
  test('7.1 defines shared Tailwind theme and interaction tokens', () => {
    expect(stylesheet).toMatch(/@import\s+(['"])tailwindcss\1\s*;/)
    expect(stylesheet).toContain('@theme {')
    for (const token of [
      '--color-page',
      '--color-surface',
      '--color-text',
      '--color-muted',
      '--color-border',
      '--color-line',
      '--color-line-strong',
      '--color-danger',
      '--radius-card',
      '--shadow-card',
      '--focus-ring',
    ])
      expect(stylesheet).toContain(token)
    expect(implementationCss).not.toMatch(/--rm-/)
    expect(implementationCss).not.toMatch(/#[0-9a-fA-F]{3,8}|rgb\(/)
    const radii = [...implementationCss.matchAll(/border-radius:\s*([^;]+);/g)].map(
      (match) => match[1],
    )
    const shadows = [...implementationCss.matchAll(/box-shadow:\s*([^;]+);/g)].map(
      (match) => match[1],
    )
    expect(radii.every((value) => value.startsWith('var('))).toBe(true)
    expect(shadows.every((value) => value.startsWith('var('))).toBe(true)
  })

  // テストケース: 共通header、navigation、機能workspace、404をwide／narrowで表示する。
  // 期待値: wideは横navigationと広いworkspace、narrowはdisclosureと1列layoutになり横overflowしない。
  test('7.2 defines responsive shell and feature workspace contracts', () => {
    expect(stylesheet).toMatch(
      /\.application-navigation,[\s\S]*\.owner-actions\s*\{[^}]*display:\s*flex/s,
    )
    expect(stylesheet).toMatch(/\.page-heading\s*\{[^}]*max-width:/s)
    expect(stylesheet).toMatch(
      /\.delivery-target-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2/s,
    )
    expect(stylesheet).toMatch(
      /@media\s*\(max-width:\s*820px\)[\s\S]*\.delivery-target-grid,[\s\S]*grid-template-columns:\s*1fr/s,
    )
    expect(stylesheet).toMatch(/\.not-found-link,[\s\S]*\.button-link\s*\{/)
  })

  // テストケース: 未認証owner向けのログイン画面をwide／narrowで表示する。
  // 期待値: wideは紹介と認証操作を分離し、narrowは1列へ収め、LINEログインを主要buttonとして示す。
  test('7.2 styles a responsive, explicit LINE login experience', () => {
    expect(authStylesheet).toMatch(
      /\.auth-page \.auth-card\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1\.08fr\)/s,
    )
    expect(authStylesheet).toMatch(
      /\.auth-page \.line-login-button\s*\{[^}]*width:\s*100%[^}]*background:\s*var\(--color-line\)/s,
    )
    expect(authStylesheet).toMatch(
      /@media\s*\(max-width:\s*820px\)[\s\S]*\.auth-page \.auth-card\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/s,
    )
    expect(authStylesheet).not.toMatch(/#[0-9a-fA-F]{3,8}|rgb\(/)
  })

  // テストケース: PageFrameがheading、content、loading、success、failureを共通表示する。
  // 期待値: 共通card幅、色非依存status、隠れないroute focusを持つ。
  test('7.3 styles page frame and semantic statuses consistently', () => {
    expect(stylesheet).toMatch(/\.page-frame\s*\{[^}]*max-width:/s)
    expect(stylesheet).toMatch(
      /\.page-status-loading[\s\S]*\.page-status-success[\s\S]*\.page-status-error/,
    )
    expect(stylesheet).toMatch(/\.page-frame:focus\s*\{[^}]*outline:\s*none/s)
  })

  // テストケース: チャネル管理を共通card、form、danger actionで表示する。
  // 期待値: 長い識別情報が折り返され、narrowで操作群が画面幅に収まる。
  test('7.4 styles channel administration without horizontal overflow', () => {
    expect(stylesheet).toMatch(/\.channel-details[^}]*overflow-wrap:\s*anywhere/s)
    expect(stylesheet).toMatch(
      /@media\s*\(max-width:\s*720px\)[\s\S]*\.channel-card-heading[\s\S]*flex-direction:\s*column/s,
    )
    expect(stylesheet).toMatch(
      /\.channel-admin button\.danger,[\s\S]*\.rich-menu-admin button\.danger\s*\{[^}]*var\(--color-danger\)/s,
    )
  })

  // テストケース: アカウント管理と全連携解除回復を共通状態表現で表示する。
  // 期待値: recipient card、unlink panel、回復panelが共通surfaceと危険色を使う。
  test('7.5 styles account management and unlink recovery', () => {
    expect(stylesheet).toMatch(
      /\.account-console[\s\S]*\.unlink-panel[\s\S]*var\(--color-surface\)/,
    )
    expect(stylesheet).toMatch(/\.unlink-panel[\s\S]*var\(--color-danger\)/)
  })

  // テストケース: リッチメニューのselector、editor、preview、履歴、回復を統一表示する。
  // 期待値: modeを文字付きstatusで区別し、画像と長いIDが対応幅を超えない。
  test('7.6 styles every rich-menu mode and bounded content', () => {
    expect(stylesheet).toMatch(
      /\.status\.editable[\s\S]*\.status\.read-only[\s\S]*\.status\.unavailable[\s\S]*\.status\.recovery-only/,
    )
    expect(stylesheet).toMatch(/\.rich-menu-admin[\s\S]*overflow-wrap:\s*anywhere/)
    expect(stylesheet).toMatch(/\.rich-menu-image-frame img\s*\{[^}]*max-width:\s*100%/s)
  })

  // テストケース: 配信入力、preview、確認、結果を共通form、card、statusで表示する。
  // 期待値: processing、success、failure、unknownがtextと境界色で区別され、narrowで収まる。
  test('7.7 styles delivery workflow and distinct operation states', () => {
    expect(stylesheet).toMatch(
      /\.delivery[\s\S]*\.panel\.progress[\s\S]*\.panel\.success[\s\S]*\.panel\.error[\s\S]*\.panel\.uncertain/,
    )
    expect(stylesheet).toMatch(/\.delivery\s+(input|textarea|select)[\s\S]*max-width:\s*100%/)
  })

  // テストケース: 全routeのkeyboard、target、contrast、overflow契約を共通化する。
  // 期待値: border-box、24px target、focus-visible、長文折返し、reduced motionを全画面へ適用する。
  test('7.8 integrates accessibility and overflow safeguards', () => {
    expect(stylesheet).toMatch(/\*,[\s\S]*box-sizing:\s*border-box/)
    expect(stylesheet).toMatch(/button,[\s\S]*min-height:\s*24px/)
    expect(stylesheet).toMatch(/:focus-visible[\s\S]*var\(--focus-ring\)/)
    expect(stylesheet).toMatch(/overflow-wrap:\s*anywhere/)
    expect(stylesheet).toContain('@media (prefers-reduced-motion: reduce)')
    expect(stylesheet).toMatch(
      /\.page-content a\s*\{[^}]*display:\s*inline-flex[^}]*min-height:\s*24px/s,
    )
    expect(stylesheet).toMatch(
      /:where\(a, button, input, select, textarea, summary, \[tabindex\]\):focus-visible\s*\{[^}]*scroll-margin-block-start:/s,
    )
    expect(stylesheet).toMatch(
      /@media\s*\(max-width:\s*720px\)[\s\S]*\.application-header\s*\{[^}]*position:\s*static/s,
    )
    const surface = themeColor('--color-surface')
    expect(contrast(themeColor('--color-text'), surface)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(themeColor('--color-muted'), surface)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(themeColor('--color-border'), surface)).toBeGreaterThanOrEqual(3)
    expect(contrast(themeColor('--color-line'), surface)).toBeGreaterThanOrEqual(3)
    expect(contrast(themeColor('--color-focus'), surface)).toBeGreaterThanOrEqual(3)
  })
})
