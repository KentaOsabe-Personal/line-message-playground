import { describe, expect, test } from 'vitest'

import { createTextJudgmentLabConfig, TextJudgmentLabConfigError } from '../src/textJudgmentLabConfig'

describe('text judgment lab public configuration', () => {
  // テストケース: 公開LIFF IDとcanonical HTTPS originを渡す。
  // 期待値: ラボ入口だけを指すimmutableな公開設定を返す。
  test('builds the public lab LIFF configuration', () => {
    expect(createTextJudgmentLabConfig({
      liffId: '1234567890-AbCdEf', currentOrigin: 'https://lab.example.test',
    })).toEqual({
      liffId: '1234567890-AbCdEf',
      liffUrl: 'https://liff.line.me/1234567890-AbCdEf',
      entryUrl: 'https://lab.example.test/labs/text-judgment',
    })
  })

  // テストケース: 空ID、秘密風query、HTTP origin、path付きoriginを渡す。
  // 期待値: SDK初期化前に固定設定エラーで拒否する。
  test.each([
    { liffId: '', currentOrigin: 'https://lab.example.test' },
    { liffId: '123-a?token=secret', currentOrigin: 'https://lab.example.test' },
    { liffId: '123-a', currentOrigin: 'http://lab.example.test' },
    { liffId: '123-a', currentOrigin: 'https://lab.example.test/path' },
  ])('rejects unsafe public configuration %#', (input) => {
    expect(() => createTextJudgmentLabConfig(input)).toThrow(TextJudgmentLabConfigError)
  })
})
