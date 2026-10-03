import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import TextJudgmentDetails from '../src/TextJudgmentDetails'
import { applyJudgment, conversationQuestion, createConversationCore } from '../src/textJudgmentLabState'
import { v2Response } from './textJudgmentLabV2Fixture'

function renderDetails(confidence = 0.6999) {
  const judgment = v2Response()
  const before = createConversationCore('c')
  judgment.inspection = { ...judgment.inspection, normalization: { ...judgment.inspection.normalization,
    topic: { status: confidence >= 0.7 ? 'eligible' : 'needs_review', reasons: [confidence >= 0.7 ? 'eligible' : 'confidence_below_threshold'],
      checks: [{ rule: 'confidence_below_threshold', actual: confidence, operator: 'gte', expected: 0.7, passed: confidence >= 0.7 }] } } }
  judgment.details.choices = { ...judgment.details.choices, topic: { ...judgment.details.choices.topic, confidence } }
  return renderToStaticMarkup(<TextJudgmentDetails judgment={judgment} uiElapsedMs={456}
    before={before} previousQuestion={conversationQuestion(before)!} application={applyJudgment(before, judgment).application} />)
}
describe('task 11.2 判定詳細', () => {
  // テストケース: 成功した発言の詳細を描画する。
  // 期待値: 詳細は初期状態で閉じる。同じ発言の判定出力、採用判断、実際の動作、全9質問、文脈の全項目、各版を表示する。
  it('出力・採用・実動作と発言時の文脈を分ける', () => {
    const html = renderDetails()
    expect(html).not.toMatch(/<details[^>]* open/)
    for (const label of ['Jevの出力', 'アプリの採用判断', '実際の動作', '送信した文脈', '判定質問の設計',
      'text-judgment-questions/2', 'text-judgment-adoption/1', 'text-judgment-conversation/1',
      'currentText', 'recentUserTexts', 'clarification', 'topicPicker', 'Jevへ送信していない', 'criteriaは未定義']) expect(html).toContain(label)
    for (const id of ['topic','relevance','change','scope','workaround','result','impact_evidence','impact','urgency']) expect(html).toContain(`data-question-id="${id}"`)
    expect(html).toContain('456 ms')
  })
  // テストケース: confidenceが採用閾値の直前と一致する場合を表示する。どちらも百分率の小数1桁表示では70.0%になる。
  // 期待値: 丸め前の値0.6999と0.7、比較式、採用できるかどうかの違いを表示する。
  it('同じ丸め表示でも元の比較と採用判断を区別する', () => {
    const below = renderDetails(0.6999)
    const boundary = renderDetails(0.7)
    expect(below).toContain('70.0%')
    expect(boundary).toContain('70.0%')
    expect(below).toContain('0.6999 ≥ 0.7: 条件未達')
    expect(boundary).toContain('0.7 ≥ 0.7: 条件成立')
  })
  // テストケース: Score、Noul、confidenceの意味を閲覧する。
  // 期待値: Scoreの段階を確率で重み付けした平均値、Noulのyesの確率、確率分布から計算するconfidenceを説明し、正答を保証しないことを示す。
  it('数値の意味とアプリ方針を正確に説明する', () => {
    const html = renderDetails()
    for (const label of ['0〜2の段階番号を確率で重み付けした平均値', 'yesの確率', '確率分布から計算',
      '候補の確率とは別', '正答率の保証ではありません', 'アプリの初期方針', '案内の種類や実際の対応速度は変えません']) expect(html).toContain(label)
  })
})
