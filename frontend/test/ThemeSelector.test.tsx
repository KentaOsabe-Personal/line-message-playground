import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import ThemeSelector from '../src/ThemeSelector'
import {
  createThemePreferenceStore,
  themePreferenceKey,
  type ThemePreferenceStore,
} from '../src/themePreference'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

describe('表示テーマの選択と保存', () => {
  let container: HTMLDivElement
  let root: Root
  let themeRoot: HTMLDivElement
  let mediaQuery: MediaQueryList
  let storage: Storage
  let store: ThemePreferenceStore

  beforeEach(() => {
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    themeRoot = document.createElement('div')
    mediaQuery = Object.assign(new EventTarget(), {
      matches: false,
      media: '(prefers-color-scheme: dark)',
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
    }) as MediaQueryList
    const values = new Map<string, string>()
    storage = {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => {
        values.set(key, value)
      },
      removeItem: (key) => {
        values.delete(key)
      },
      clear: () => values.clear(),
      key: (index) => [...values.keys()][index] ?? null,
      get length() {
        return values.size
      },
    }
    store = createThemePreferenceStore({ storage, mediaQuery, root: themeRoot })
  })
  afterEach(async () => {
    await act(async () => root.unmount())
    container.remove()
    vi.restoreAllMocks()
  })
  const render = () => act(async () => root.render(<ThemeSelector store={store} />))
  const change = (value: string) =>
    act(async () => {
      const select = container.querySelector('select')!
      select.value = value
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
  const notifySystem = (dark: boolean) => {
    Object.defineProperty(mediaQuery, 'matches', { value: dark, configurable: true })
    mediaQuery.dispatchEvent(new Event('change'))
  }

  // テストケース: 保存値がない状態でテーマ選択を表示し、ブラウザの配色通知を変更する。
  // 期待値: 既定は自動で、ブラウザの通知に合わせてライトとダークを切り替える。
  test('follows browser appearance only in automatic mode', async () => {
    await render()
    expect(container.querySelector('label')?.textContent).toContain('表示テーマ')
    expect([...container.querySelectorAll('option')].map((item) => item.textContent)).toEqual([
      '自動',
      'ライト',
      'ダーク',
    ])
    expect(container.querySelector('select')?.value).toBe('auto')
    expect(themeRoot.dataset.theme).toBe('light')
    notifySystem(true)
    expect(themeRoot.dataset.theme).toBe('dark')
    notifySystem(false)
    expect(themeRoot.dataset.theme).toBe('light')
  })

  // テストケース: ライトを通知するブラウザでダークを選び、通知を変更してから自動へ戻す。
  // 期待値: 手動選択を通知より優先し、自動へ戻した時点で最新の通知に従う。
  test('overrides a light-only browser and resumes automatic appearance', async () => {
    await render()
    await change('dark')
    expect(themeRoot.dataset.theme).toBe('dark')
    expect(storage.getItem(themePreferenceKey)).toBe('dark')
    notifySystem(false)
    expect(themeRoot.dataset.theme).toBe('dark')
    await change('auto')
    expect(themeRoot.dataset.theme).toBe('light')
    notifySystem(true)
    await change('light')
    expect(themeRoot.dataset.theme).toBe('light')
    notifySystem(true)
    expect(themeRoot.dataset.theme).toBe('light')
    await change('auto')
    expect(themeRoot.dataset.theme).toBe('dark')
  })

  // テストケース: 手動設定を保存した後、画面を開き直すための新しいstoreを作る。
  // 期待値: 保存した選択を復元し、画面を描画する前にも配色を適用できる。
  test('restores the preference before rendering', () => {
    store.setPreference('dark')
    const restored = createThemePreferenceStore({ storage, mediaQuery, root: themeRoot })
    restored.apply()
    expect(restored.getSnapshot()).toBe('dark')
    expect(themeRoot.dataset.theme).toBe('dark')
  })

  // テストケース: 不正な保存値がある状態や配色通知APIを使えない状態で画面を開く。
  // 期待値: 自動設定へ戻り、通知がない場合はライトを使い、手動ダークも選べる。
  test('ignores invalid saved values and supports unavailable media queries', () => {
    storage.setItem(themePreferenceKey, 'invalid')
    const fallback = createThemePreferenceStore({ storage, mediaQuery: null, root: themeRoot })
    fallback.apply()
    expect(fallback.getSnapshot()).toBe('auto')
    expect(themeRoot.dataset.theme).toBe('light')
    fallback.setPreference('dark')
    expect(themeRoot.dataset.theme).toBe('dark')
  })

  // テストケース: 保存値の読み取りと書き込みをブラウザが拒否する。
  // 期待値: 表示や操作を中断せず、開いている間は手動設定を保持する。
  test('keeps theme selection usable when storage is denied', async () => {
    vi.spyOn(storage, 'getItem').mockImplementation(() => {
      throw new Error('denied')
    })
    vi.spyOn(storage, 'setItem').mockImplementation(() => {
      throw new Error('denied')
    })
    store = createThemePreferenceStore({ storage, mediaQuery, root: themeRoot })
    await render()
    await change('dark')
    expect(container.querySelector('select')?.value).toBe('dark')
    expect(themeRoot.dataset.theme).toBe('dark')
  })

  // テストケース: 同じ設定を使う複数の選択欄で配色を変更する。
  // 期待値: 選択欄を同期し、入力欄の内容とフォーカスを維持する。
  test('shares the preference without replacing form state', async () => {
    await act(async () =>
      root.render(
        <>
          <ThemeSelector store={store} />
          <ThemeSelector store={store} />
          <textarea defaultValue="編集中の文章" />
        </>,
      ),
    )
    const select = container.querySelector('select')!
    select.focus()
    await change('dark')
    expect([...container.querySelectorAll('select')].map((item) => item.value)).toEqual([
      'dark',
      'dark',
    ])
    expect(container.querySelector('textarea')?.value).toBe('編集中の文章')
    expect(document.activeElement).toBe(select)
  })

  // テストケース: テーマ選択欄を閉じて、配色通知の購読を解除する。
  // 期待値: 最後の選択欄が閉じた時点でイベントリスナーを解除する。
  test('removes its media listener after the last subscription', () => {
    const remove = vi.spyOn(mediaQuery, 'removeEventListener')
    const unsubscribe = store.subscribe(() => {})
    unsubscribe()
    expect(remove).toHaveBeenCalledWith('change', store.apply)
  })
})
