import { describe, expect, test } from 'vitest'

import {
  LAB_CONTENT,
  getGuide,
  getOfficialHelpLink,
  getQuestion,
} from '../src/textJudgmentLabContent'

describe('text judgment lab fixed content', () => {
  // テストケース: 新規相談の説明と二つの例文を取得する。
  // 期待値: 外部送信・架空相談・ページ内保持を明示し、例文IDと文面を固定する。
  test('defines the safety notice and both starter examples', () => {
    expect(LAB_CONTENT.safetyNotice).toContain('外部サービスJevへ送信')
    expect(LAB_CONTENT.safetyNotice).toContain('個人情報を含まない架空の相談')
    expect(LAB_CONTENT.safetyNotice).toContain('このページだけに保持')
    expect(LAB_CONTENT.examples).toEqual([
      { id: 'missing_notification', text: 'LINEの通知が届きません' },
      { id: 'notification_settings', text: 'LINEの通知の設定方法を知りたいです' },
    ])
  })

  // テストケース: 各会話位置の固定質問と選択肢をIDから取得する。
  // 期待値: 自由生成なしで一問分だけを取得し、本人の「分からない」と判定要確認を混同しない。
  test('provides one fixed question and closed choices per conversation position', () => {
    expect(getQuestion('topic').choices.map((choice) => choice.id)).toEqual([
      'missing_notification', 'notification_settings',
    ])
    expect(getQuestion('missing_scope').choices.map((choice) => choice.id)).toEqual(['all', 'specific', 'unknown'])
    expect(getQuestion('workaround').choices.map((choice) => choice.id)).toEqual(['can_read', 'cannot_read', 'unknown'])
    expect(getQuestion('urgency').choices.map((choice) => choice.id)).toEqual(['yes', 'no'])
    expect(getQuestion('missing_result').choices.map((choice) => choice.id)).toEqual([
      'done', 'not_done', 'not_tried', 'cannot_check',
    ])
  })

  // テストケース: 四つのiPhone案内を取得する。
  // 期待値: 固定要点・詳細・HTTPS公式リンク・確認日を全案内が持つ。
  test('defines all four iPhone guides with reviewed official links', () => {
    expect(Object.keys(LAB_CONTENT.guides).sort()).toEqual([
      'missing_all', 'missing_specific', 'settings_all', 'settings_specific',
    ])
    for (const guideId of Object.keys(LAB_CONTENT.guides) as (keyof typeof LAB_CONTENT.guides)[]) {
      const guide = getGuide(guideId)
      expect(guide.summary.length).toBeGreaterThan(0)
      expect(guide.steps.length).toBeGreaterThan(0)
      expect(guide.checkedOn).toBe('2026-09-20')
      expect(new URL(guide.helpUrl).protocol).toBe('https:')
      expect(getOfficialHelpLink(guide.helpUrl)).toBe(guide.helpUrl)
    }
  })

  // テストケース: 任意URLを公式リンクとして解決しようとする。
  // 期待値: allowlist外やHTTPリンクを拒否し、表示先を入力から作らない。
  test('rejects links outside the fixed HTTPS allowlist', () => {
    expect(() => getOfficialHelpLink('https://example.com/help')).toThrow('LAB_HELP_LINK_NOT_ALLOWED')
    expect(() => getOfficialHelpLink('http://help.line.me/line/ios/')).toThrow('LAB_HELP_LINK_NOT_ALLOWED')
  })

  // テストケース: 終了と範囲外の固定文面を取得する。
  // 期待値: 通知設定以外の可能性、公式ヘルプ、新規開始を固定文で案内する。
  test('defines fixed completion and restart messages', () => {
    expect(LAB_CONTENT.messages.cannotRead).toContain('通知設定以外の問題の可能性')
    expect(LAB_CONTENT.messages.cannotRead).toContain('LINE公式ヘルプ')
    expect(LAB_CONTENT.messages.missingUnresolved).toContain('まだ通知が届かない')
    expect(LAB_CONTENT.messages.missingUnresolved).toContain('LINE公式ヘルプ')
    expect(LAB_CONTENT.messages.settingsUnresolved).toContain('通知を設定できない')
    expect(LAB_CONTENT.messages.settingsUnresolved).toContain('LINE公式ヘルプ')
    expect(LAB_CONTENT.messages.restartRequired).toContain('新しい相談')
    expect(LAB_CONTENT.messages.outOfScope).toContain('2種類')
  })
})
