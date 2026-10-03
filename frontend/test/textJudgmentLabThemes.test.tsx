import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import TextJudgmentThemePanel, { type ThemeInputState } from '../src/TextJudgmentThemePanel'
import { LAB_THEMES, type LabThemeId } from '../src/textJudgmentLabThemes'

const ready: ThemeInputState = {
  authorized: true,
  pending: false,
  inputMode: 'free_and_choices',
  ended: false,
}
const show = (selectedThemeId: LabThemeId) =>
  renderToStaticMarkup(
    <TextJudgmentThemePanel
      themes={LAB_THEMES}
      selectedThemeId={selectedThemeId}
      inputState={ready}
      onThemeChange={() => undefined}
      onSubmitExample={() => undefined}
    />,
  )
describe('task 11.3 検証テーマ', () => {
  // テストケース: 全7テーマの固定データと例文を確認する。
  // 期待値: 要件に定めた7テーマそれぞれに、2つの例文、相談と質問の前提、観察ポイント、期待する違いを表示する。各例文は1000コードポイント以内とする。
  it('全7テーマの前提・例文・観察・期待を表示する', () => {
    expect(LAB_THEMES.map((theme) => theme.label)).toEqual([
      '情報量',
      '言い換え',
      '否定',
      '急ぎ',
      '支障と急ぎ',
      '不明と曖昧さ',
      '対象外',
    ])
    expect(new Set(LAB_THEMES.map((theme) => theme.id)).size).toBe(7)
    for (const theme of LAB_THEMES) {
      expect(theme.examples).toHaveLength(2)
      for (const example of theme.examples)
        expect(Array.from(example).length).toBeLessThanOrEqual(1000)
      const html = show(theme.id)
      for (const value of [
        theme.consultation,
        theme.question,
        theme.observation,
        theme.expectation,
        ...theme.examples,
      ]) {
        expect(value.length).toBeGreaterThan(0)
        expect(html).toContain(value)
      }
      expect(html).toContain('実測結果ではありません')
      expect(html).toContain('同じ結果は保証しません')
    }
    expect(show('impact_urgency')).toContain('回避策のChoiceも変化')
    expect(show('impact_urgency')).toContain('Scoreだけの効果とは断定')
  })
  // テストケース: すべてのテーマを選択し、自由入力できる状態で例文の送信ボタンを押す。
  // 期待値: テーマ選択時は選んだテーマIDだけを通知する。送信ボタンを押すと、通常の送信関数に例文を1回だけ渡す。
  it('テーマ選択だけでは送信せず、例文を一回渡す', async () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    const onSubmitExample = vi.fn()
    const onThemeChange = vi.fn()
    try {
      await act(async () =>
        root.render(
          <TextJudgmentThemePanel
            themes={LAB_THEMES}
            selectedThemeId="information"
            inputState={ready}
            onThemeChange={onThemeChange}
            onSubmitExample={onSubmitExample}
          />,
        ),
      )
      const select = container.querySelector('select')!
      for (const theme of LAB_THEMES) {
        await act(async () => {
          select.value = theme.id
          select.dispatchEvent(new Event('change', { bubbles: true }))
        })
        expect(onThemeChange).toHaveBeenLastCalledWith(theme.id)
      }
      expect(onSubmitExample).not.toHaveBeenCalled()
      await act(async () => container.querySelector('button')!.click())
      expect(onSubmitExample).toHaveBeenCalledExactlyOnceWith(LAB_THEMES[0].examples[0])
    } finally {
      await act(async () => root.unmount())
    }
  })
  // テストケース: 未認証、判定中、選択肢のみで回答できる状態、相談終了後の各状態でテーマを表示する。
  // 期待値: テーマを閲覧して選択できる。例文の送信ボタンは無効にする。
  it.each([
    { authorized: false },
    { pending: true },
    { inputMode: 'choices_only' as const },
    { ended: true },
  ])('禁止状態%sでも閲覧できる', async (blocked) => {
    const container = document.createElement('div')
    const root = createRoot(container)
    const submit = vi.fn()
    try {
      await act(async () =>
        root.render(
          <TextJudgmentThemePanel
            themes={LAB_THEMES}
            selectedThemeId="information"
            inputState={{ ...ready, ...blocked }}
            onThemeChange={() => undefined}
            onSubmitExample={submit}
          />,
        ),
      )
      expect(container.querySelector('select')?.disabled).toBe(false)
      const buttons = [...container.querySelectorAll('button')]
      expect(buttons).toHaveLength(2)
      for (const button of buttons) {
        expect(button.disabled).toBe(true)
        await act(async () => button.click())
      }
      expect(submit).not.toHaveBeenCalled()
    } finally {
      await act(async () => root.unmount())
    }
  })
  // テストケース: 1001コードポイントの例文と、1000コードポイントの絵文字の例文を渡す。
  // 期待値: 1001コードポイントの例文は送信できない。1000コードポイントの例文は、UTF-16での長さにかかわらず1回送信できる。
  it('code point上限で例文送信を制限する', async () => {
    const container = document.createElement('div')
    const root = createRoot(container)
    const submit = vi.fn()
    const themes = [
      {
        ...LAB_THEMES[0],
        id: 'information' as const,
        examples: ['あ'.repeat(1001), '😀'.repeat(1000)],
      },
    ]
    try {
      await act(async () =>
        root.render(
          <TextJudgmentThemePanel
            themes={themes}
            selectedThemeId="information"
            inputState={ready}
            onThemeChange={() => undefined}
            onSubmitExample={submit}
          />,
        ),
      )
      const buttons = [...container.querySelectorAll('button')]
      expect(buttons[0].disabled).toBe(true)
      expect(buttons[1].disabled).toBe(false)
      await act(async () => buttons[1].click())
      expect(submit).toHaveBeenCalledExactlyOnceWith(themes[0].examples[1])
    } finally {
      await act(async () => root.unmount())
    }
  })
})
