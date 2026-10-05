export type ThemePreference = 'auto' | 'light' | 'dark'

export const themePreferenceKey = 'line-playground:theme'

export const isThemePreference = (value: unknown): value is ThemePreference =>
  value === 'auto' || value === 'light' || value === 'dark'

export function createThemePreferenceStore({
  storage,
  mediaQuery,
  root,
}: {
  storage: Storage | null
  mediaQuery: MediaQueryList | null
  root: HTMLElement | null
}) {
  let preference: ThemePreference = 'auto'
  try {
    const saved = storage?.getItem(themePreferenceKey)
    if (isThemePreference(saved)) preference = saved
  } catch {
    // 保存値を読み取れない場合は、自動設定で表示する。
  }
  const subscribers = new Set<() => void>()
  const apply = () => {
    if (root)
      root.dataset.theme =
        preference === 'auto' ? (mediaQuery?.matches ? 'dark' : 'light') : preference
  }
  return Object.freeze({
    getSnapshot: () => preference,
    apply,
    subscribe: (notify: () => void) => {
      if (subscribers.size === 0) mediaQuery?.addEventListener('change', apply)
      subscribers.add(notify)
      apply()
      return () => {
        subscribers.delete(notify)
        if (subscribers.size === 0) mediaQuery?.removeEventListener('change', apply)
      }
    },
    setPreference: (value: ThemePreference) => {
      preference = value
      apply()
      try {
        storage?.setItem(themePreferenceKey, value)
      } catch {
        // 保存できなくても、開いている画面には選択を反映する。
      }
      subscribers.forEach((notify) => notify())
    },
  })
}

export type ThemePreferenceStore = ReturnType<typeof createThemePreferenceStore>
let browserStore: ThemePreferenceStore | undefined

export function getThemePreferenceStore(): ThemePreferenceStore {
  if (!browserStore) {
    let storage: Storage | null = null
    try {
      storage = typeof window === 'undefined' ? null : window.localStorage
    } catch {
      // ブラウザが保存を許可しない場合は、画面を開いている間だけ設定を保持する。
    }
    browserStore = createThemePreferenceStore({
      storage,
      mediaQuery:
        typeof window !== 'undefined' && typeof window.matchMedia === 'function'
          ? window.matchMedia('(prefers-color-scheme: dark)')
          : null,
      root: typeof document === 'undefined' ? null : document.documentElement,
    })
  }
  return browserStore
}
