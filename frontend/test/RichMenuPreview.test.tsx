import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import RichMenuPreview from '../src/RichMenuPreview'
import { readFileSync } from 'node:fs'
import type { PreviewView } from '../src/richMenuAdminDto'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

const preview: PreviewView = {
  channelId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  channelLabel: '通知チャネル',
  templateId: 'jp-link-two',
  templateVersion: 3,
  fields: [
    { displayName: '案内', uri: 'https://example.com/guide' },
    { displayName: '予約', uri: 'https://example.com/book' },
  ],
  image: {
    contentType: 'image/png',
    width: 2500,
    height: 843,
    digest: 'a'.repeat(64),
    base64: 'aGVsbG8=',
  },
  observation: {
    kind: 'external_default',
    observedAt: '2026-08-03T10:00:00+09:00',
    fingerprint: 'b'.repeat(64),
    managedResourceId: null,
  },
  warnings: ['external_default_replaced'],
  confirmationToken: 'opaque',
  expiresAt: '2026-08-03T10:05:00+09:00',
}

let container: HTMLDivElement
let root: Root

describe('RichMenuPreview', () => {
  beforeEach(() => {
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  // テストケース: 有効なpreview確認を操作せず描画する。
  // 期待値: URLへ接続せず、対象・画像・全項目・実状態・警告・期限を表示する。
  test('renders the complete expiring confirmation without opening links', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    await act(async () =>
      root.render(
        <RichMenuPreview
          preview={preview}
          imageUrl="blob:preview"
          templateName="2リンク"
          now="2026-08-03T10:02:00+09:00"
        />,
      ),
    )
    expect(open).not.toHaveBeenCalled()
    for (const text of [
      '通知チャネル',
      '2リンク',
      'jp-link-two',
      '版 3',
      '案内',
      'https://example.com/guide',
      '予約',
      '外部の既定リッチメニューを置き換える可能性があります。',
      'LINE側で作成したメニューを適用中',
      '残り 3分',
    ]) {
      expect(container.textContent).toContain(text)
    }
    expect((container.querySelector('img') as HTMLImageElement).src).toContain('blob:preview')
    expect(container.textContent).toContain('適用可能')
  })

  // テストケース: ownerがpreview内の一件のリンクを明示選択する。
  // 期待値: 選択したURLだけをnoopener・noreferrerで開き、結果を保存しない。
  test('opens only the selected link with opener and referrer isolation', async () => {
    const opened = { opener: {} as unknown }
    const open = vi.spyOn(window, 'open').mockReturnValue(opened as Window)
    await act(async () =>
      root.render(
        <RichMenuPreview
          preview={preview}
          imageUrl="blob:preview"
          templateName="2リンク"
          now="2026-08-03T10:02:00+09:00"
        />,
      ),
    )
    const buttons = [...container.querySelectorAll('button')]
    await act(async () => buttons[1].click())
    expect(open).toHaveBeenCalledTimes(1)
    expect(open).toHaveBeenCalledWith('https://example.com/book', '_blank', 'noopener,noreferrer')
    expect(opened.opener).toBeNull()
    expect(container.textContent).toContain('リンク先の到達や表示結果は保証・保存しません。')
  })

  // テストケース: previewの有効期限を経過させる。
  // 期待値: 適用を閉じ、ownerへ再生成が必要なことを表示する。
  test('marks an expired preview as requiring regeneration', async () => {
    await act(async () =>
      root.render(
        <RichMenuPreview
          preview={preview}
          imageUrl="blob:preview"
          templateName="2リンク"
          now="2026-08-03T10:05:01+09:00"
        />,
      ),
    )
    expect(container.textContent).toContain('期限切れ')
    expect(container.textContent).toContain('新しいプレビューを生成してください。')
  })

  // テストケース: 期限内previewのLINE実状態をunknownとして描画する。
  // 期待値: 適用可能と推測せず、安全な再確認案内だけを表示する。
  test('fails closed when the actual LINE state is unknown', async () => {
    const unknown = {
      ...preview,
      observation: { ...preview.observation, kind: 'unknown' as const },
    }
    await act(async () =>
      root.render(
        <RichMenuPreview
          preview={unknown}
          imageUrl="blob:preview"
          templateName="2リンク"
          now="2026-08-03T10:02:00+09:00"
        />,
      ),
    )
    expect(container.textContent).not.toContain('適用可能')
    expect(container.textContent).toContain(
      '実状態を安全に確認できないため、新しいプレビューを生成してください。',
    )
  })

  // テストケース: preview表示中に時計を進めて残り時間を更新する。
  // 期待値: 表示が経過へ追従し、期限到達時に適用不可へ切り替わる。
  test('updates the remaining time until the preview expires', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-03T10:00:00+09:00'))
    await act(async () =>
      root.render(
        <RichMenuPreview preview={preview} imageUrl="blob:preview" templateName="2リンク" />,
      ),
    )
    expect(container.textContent).toContain('残り 5分')
    await act(async () => {
      vi.advanceTimersByTime(60_000)
    })
    expect(container.textContent).toContain('残り 4分')
    await act(async () => {
      vi.advanceTimersByTime(4 * 60_000)
    })
    expect(container.textContent).toContain('期限切れ')
    expect(container.textContent).not.toContain('適用可能')
  })
  // テストケース: 確認画面を表示し、編集へ戻る・期限切れ再生成を選択する。
  // 期待値: 影響と全確認内容を折りたたまず示し、選択した操作だけを呼ぶ。
  test('keeps confirmation and impact visible with back and regeneration actions', async () => {
    const back = vi.fn()
    const regenerate = vi.fn()
    await act(async () =>
      root.render(
        <RichMenuPreview
          preview={preview}
          imageUrl="blob:preview"
          templateName="2リンク"
          now="2026-08-03T10:06:00+09:00"
          onBack={back}
          onRegenerate={regenerate}
        />,
      ),
    )
    expect(container.querySelector('details')).toBeNull()
    for (const text of ['未適用', '領域 1', '利用者別', '端末', '編集・削除しません'])
      expect(container.textContent).toContain(text)
    await act(async () =>
      [...container.querySelectorAll('button')]
        .find((b) => b.textContent === '編集へ戻る')
        ?.click(),
    )
    await act(async () =>
      [...container.querySelectorAll('button')]
        .find((b) => b.textContent === 'プレビューを再生成')
        ?.click(),
    )
    expect(back).toHaveBeenCalledOnce()
    expect(regenerate).toHaveBeenCalledOnce()
  })
  // テストケース: 既存の省略用CSSがある状態で長い完全URLを確認表示する。
  // 期待値: URLの改行禁止と省略を解除し、全値を参照できる。
  test('wraps the complete URL even when the shared stylesheet requests ellipsis', async () => {
    const style = document.createElement('style')
    const appStyles = readFileSync(`${process.cwd()}/src/style.css`, 'utf8')
    style.textContent = appStyles.match(/\.preview-link-list span\s*\{[^}]+\}/)?.[0] ?? ''
    expect(style.textContent).toContain('white-space: nowrap')
    document.head.append(style)
    const uri = `https://example.com/${'long-path/'.repeat(30)}?query=one&second=two#fragment`
    try {
      await act(async () =>
        root.render(
          <RichMenuPreview
            preview={{ ...preview, fields: [{ displayName: '長いリンク', uri }] }}
            imageUrl="blob:preview"
            templateName="2リンク"
            now="2026-08-03T10:02:00+09:00"
          />,
        ),
      )
      const url = container.querySelector('.preview-link-list span')!
      const computed = window.getComputedStyle(url)
      expect(url.textContent).toBe(uri)
      expect(computed.whiteSpace).toBe('normal')
      expect(computed.overflow).toBe('visible')
      expect(computed.textOverflow).toBe('clip')
      expect(computed.overflowWrap).toBe('anywhere')
    } finally {
      style.remove()
    }
  })
})
