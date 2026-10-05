import { useSyncExternalStore } from 'react'

import {
  getThemePreferenceStore,
  isThemePreference,
  type ThemePreference,
  type ThemePreferenceStore,
} from './themePreference'

export default function ThemeSelector({
  store = getThemePreferenceStore(),
}: Readonly<{ store?: ThemePreferenceStore }>) {
  const preference = useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    (): ThemePreference => 'auto',
  )
  return (
    <label className="theme-selector">
      <span>表示テーマ</span>
      <select
        value={preference}
        onChange={(event) => {
          const value = event.currentTarget.value
          if (isThemePreference(value)) store.setPreference(value)
        }}
      >
        <option value="auto">自動</option>
        <option value="light">ライト</option>
        <option value="dark">ダーク</option>
      </select>
    </label>
  )
}
