import { renderToStaticMarkup } from 'react-dom/server'
import { expect, test } from 'vitest'

import TextJudgmentResult from '../src/TextJudgmentResult'
import type { JudgmentResponse } from '../src/textJudgmentLabTypes'
import fixture from './fixtures/text-judgment-lab-v3.json'

// テストケース: 異なる数値とmetadataを共通結果表示へ渡す。
// 期待値: 表示だけを丸め、順序を維持し、詳細には丸める前の応答全体を残す。
test('renders ordered values and the entire unrounded response without mutation', () => {
  const result: JudgmentResponse = {
    ...fixture,
    contractVersion: 3,
    model: 'different-model',
    elapsedMs: 321.678,
    answers: {
      ...fixture.answers,
      intent: {
        ...fixture.answers.intent,
        type: 'choice',
        choice: 'question',
        probabilities: { question: 0.12345, request: 0.45678, report: 0.23456, other: 0.18521 },
      },
      sentiment: { ...fixture.answers.sentiment, type: 'score', score: 1.23456 },
      urgency: { type: 'noul', noul: 0.87654 },
    },
  }
  const before = structuredClone(result)
  const container = document.createElement('div')
  container.innerHTML = renderToStaticMarkup(<TextJudgmentResult result={result} />)
  expect([...container.querySelectorAll('h2')].map((h) => h.textContent)).toEqual([
    '文章の分類',
    '文章の感情',
    '急ぎの要望',
  ])
  expect(
    [...container.querySelectorAll('.judgment-probability')].map((row) => row.textContent),
  ).toEqual(['質問12.3%', '依頼45.7%', '報告23.5%', 'その他18.5%'])
  expect(container.textContent).toContain('1.23')
  expect(container.textContent).toContain('87.7%')
  expect(container.textContent).toContain('Jev応答 322 ms · different-model')
  expect(container.querySelector('meter[aria-label="感情のスコア"]')?.getAttribute('max')).toBe('2')
  expect(JSON.parse(container.querySelector('pre')!.textContent)).toEqual(before)
  expect(result).toEqual(before)
})
