import { useId } from 'react'
import type { LabTheme, LabThemeId } from './textJudgmentLabThemes'

export type ThemeInputState = Readonly<{
  authorized: boolean; pending: boolean; inputMode: 'free_and_choices' | 'choices_only'; ended: boolean
}>
export default function TextJudgmentThemePanel({ themes, selectedThemeId, inputState, onThemeChange, onSubmitExample }: Readonly<{
  themes: readonly LabTheme[]; selectedThemeId: LabThemeId; inputState: ThemeInputState
  onThemeChange(id: LabThemeId): void; onSubmitExample(text: string): void
}>) {
  const selectId = useId()
  const theme = themes.find(item => item.id === selectedThemeId)
  const canSubmit = inputState.authorized && !inputState.pending && !inputState.ended && inputState.inputMode === 'free_and_choices'
  const validExample = (text: string) => text.trim().length > 0 && Array.from(text).length <= 1000
  return <section className="lab-theme-panel min-w-0 break-words [overflow-wrap:anywhere]" aria-label="検証テーマ">
    <h3>検証テーマ</h3>
    <label htmlFor={selectId}>確かめたいこと</label>
    <select id={selectId} value={selectedThemeId} onChange={event => {
      const selected = themes.find(item => item.id === event.target.value)
      if (selected) onThemeChange(selected.id)
    }}>
      {themes.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
    </select>
    <p>期待は実測結果ではありません。同じ前提でも実APIで同じ結果は保証しません。</p>
    <p>前提は通常の相談操作でそろえてください。テーマ選択は確定済み回答・判定質問・採用規則を変更しません。</p>
    {theme && <>
      <h4>{theme.label}</h4>
      <dl>
        <dt>前提の相談</dt><dd>{theme.consultation}</dd>
        <dt>前提の質問</dt><dd>{theme.question}</dd>
        <dt>観察ポイント</dt><dd>{theme.observation}</dd>
        <dt>期待する違い</dt><dd>{theme.expectation}</dd>
      </dl>
      <ul>{theme.examples.map(text => <li key={text}>
        <p>{text}</p>
        <button type="button" className="lab-example" disabled={!canSubmit || !validExample(text)} onClick={() => {
          if (canSubmit && validExample(text)) onSubmitExample(text)
        }}>この例文を送信</button>
      </li>)}</ul>
      {!canSubmit && <p>例文を送るには、認証済みで自由入力できる相談中の状態が必要です。</p>}
    </>}
  </section>
}
