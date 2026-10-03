const NOTIFICATION_HELP_URL = 'https://help.line.me/line/ios/?contentId=20000276&lang=ja'
const CHAT_NOTIFICATION_GUIDE_URL =
  'https://guide.line.me/ja/account-and-settings/notification-chatroom.html'

const officialHelpLinks = new Set([NOTIFICATION_HELP_URL, CHAT_NOTIFICATION_GUIDE_URL])

export type LabQuestionContentId =
  | 'topic'
  | 'missing_scope'
  | 'settings_scope'
  | 'workaround'
  | 'urgency'
  | 'missing_result'
  | 'settings_result'

export type LabGuideId = 'missing_all' | 'missing_specific' | 'settings_all' | 'settings_specific'

const questions = {
  topic: {
    prompt: 'どちらについて相談しますか？',
    choices: [
      { id: 'missing_notification', label: '通知が届かない' },
      { id: 'notification_settings', label: '通知の設定方法を知りたい' },
    ],
  },
  missing_scope: {
    prompt: '通知が届かない範囲を教えてください。',
    choices: [
      { id: 'all', label: 'すべてのトーク' },
      { id: 'specific', label: '特定のトーク' },
      { id: 'unknown', label: '分からない' },
    ],
  },
  settings_scope: {
    prompt: '通知を設定したい範囲を教えてください。',
    choices: [
      { id: 'all', label: '全体' },
      { id: 'specific', label: '特定のトーク' },
      { id: 'unknown', label: '分からない' },
    ],
  },
  workaround: {
    prompt: 'LINEを開けばメッセージを確認できますか？',
    choices: [
      { id: 'can_read', label: '確認できる' },
      { id: 'cannot_read', label: '確認できない' },
      { id: 'unknown', label: '分からない' },
    ],
  },
  urgency: {
    prompt: 'お急ぎですか？',
    choices: [
      { id: 'yes', label: '急いでいる' },
      { id: 'no', label: '急いでいない' },
    ],
  },
  missing_result: {
    prompt: '案内を試した結果を教えてください。',
    choices: [
      { id: 'done', label: '解決した' },
      { id: 'not_done', label: 'まだ届かない' },
      { id: 'not_tried', label: 'まだ試していない' },
      { id: 'cannot_check', label: '確認できない' },
    ],
  },
  settings_result: {
    prompt: '設定を試した結果を教えてください。',
    choices: [
      { id: 'done', label: '設定できた' },
      { id: 'not_done', label: '設定できない' },
      { id: 'not_tried', label: 'まだ試していない' },
      { id: 'cannot_check', label: '確認できない' },
    ],
  },
} as const

const guides = {
  missing_all: {
    summary: 'iPhoneとLINEの通知設定を確認してください。',
    steps: [
      'iPhoneの「設定」から通知一覧を開き、LINEの通知が許可されているか確認します。',
      'LINEの設定から「通知」を開き、通知が有効か確認します。',
    ],
    helpUrl: NOTIFICATION_HELP_URL,
    checkedOn: '2026-09-20',
  },
  missing_specific: {
    summary: '届かないトークの通知設定を確認してください。',
    steps: ['対象のトークを開き、上部メニューから通知をオンにします。'],
    helpUrl: CHAT_NOTIFICATION_GUIDE_URL,
    checkedOn: '2026-09-20',
  },
  settings_all: {
    summary: 'iPhoneとLINEの通知を希望に合わせて設定します。',
    steps: [
      'iPhoneの「設定」でLINEの通知許可を変更します。',
      'LINEの設定から「通知」を開き、必要な通知項目を変更します。',
    ],
    helpUrl: NOTIFICATION_HELP_URL,
    checkedOn: '2026-09-20',
  },
  settings_specific: {
    summary: '対象トークの通知を切り替えます。',
    steps: ['対象のトークを開き、上部メニューから通知のオン・オフを変更します。'],
    helpUrl: CHAT_NOTIFICATION_GUIDE_URL,
    checkedOn: '2026-09-20',
  },
} as const

export const LAB_CONTENT = {
  safetyNotice:
    '入力は外部サービスJevへ送信します。個人情報を含まない架空の相談を使ってください。会話はこのページだけに保持されます。',
  examples: [
    { id: 'missing_notification', text: 'LINEの通知が届きません' },
    { id: 'notification_settings', text: 'LINEの通知の設定方法を知りたいです' },
  ],
  questions,
  guides,
  messages: {
    cannotRead: '通知設定以外の問題の可能性があります。LINE公式ヘルプを確認してください。',
    missingUnresolved:
      'まだ通知が届かないため、この相談は未解決として終了します。詳しい対処はLINE公式ヘルプを確認してください。',
    settingsUnresolved:
      '通知を設定できないため、この相談は未解決として終了します。詳しい設定方法はLINE公式ヘルプを確認してください。',
    outOfScope:
      'このラボで扱えるのは「通知が届かない」「通知の設定方法を知りたい」の2種類です。入力例から選ぶこともできます。',
    restartRequired:
      '確定した回答は途中で変更できません。現在の相談を終了し、新しい相談を始めてください。',
    resolved: '通知が届くようになりました。相談を終了します。',
    settingsCompleted: '通知設定を完了しました。相談を終了します。',
    interrupted: '相談を中断しました。',
  },
} as const

export function getQuestion(id: LabQuestionContentId) {
  return questions[id]
}

export function getGuide(id: LabGuideId) {
  return guides[id]
}

export function getOfficialHelpLink(value: string): string {
  if (!officialHelpLinks.has(value)) throw new Error('LAB_HELP_LINK_NOT_ALLOWED')
  return value
}

// 表示に使う固定の文言を定義する。表示理由は、判定結果や現在の会話状態から再計算しない。
export const LAB_JUDGMENT_LABELS = {
  topic: '相談の種類',
  relevance: '対象範囲',
  change: '変更希望',
  scope: '通知の範囲',
  workaround: '代替確認',
  result: '結果',
  impact_evidence: '支障の根拠',
  impact: '支障の大きさ',
  urgency: '急ぎの要望',
  start: '相談開始',
} as const
export const LAB_VALUE_LABELS: Readonly<Record<string, string>> = {
  missing_notification: '通知が届かない',
  notification_settings: '通知の設定方法を知りたい',
  both: '両方の相談',
  in_scope: '対象内',
  mixed: '対象内と対象外が混在',
  out_of_scope: '対象外',
  keep: '現在の相談を継続',
  restart: '相談のやり直し',
  all: '全体・すべてのトーク',
  specific: '特定のトーク',
  unknown: '分からない',
  can_read: 'LINEを開けば確認できる',
  cannot_read: 'LINEを開いても確認できない',
  done: '完了・解決',
  not_done: '未解決',
  not_tried: 'まだ試していない',
  cannot_check: '確認できない',
  present: '支障の記述あり',
  absent: '支障の記述なし',
  unmentioned: '未言及',
  unclear: '判定要確認',
  low: '支障小',
  high: '支障大',
  unassessed: '未評価',
  needs_review: '判定要確認',
}
export const labValueLabel = (value: string | boolean) =>
  typeof value === 'boolean'
    ? value
      ? '急いでいる'
      : '急いでいない'
    : (LAB_VALUE_LABELS[value] ?? value)
export const LAB_APPLICATION_REASONS = {
  newly_confirmed: '今回の回答を採用',
  unmentioned: '未言及のため',
  conditions_not_met: '採用条件を満たさないため',
  confirmed_preserved: '確定済み回答を維持',
  priority_rule: '優先規則による不使用',
  not_used_here: '現在の場面では使わない',
  applied_no_action_change: '採用したが動作は変わらない',
} as const
export const LAB_PRIORITY_REASONS = {
  restart: '訂正・やり直し希望',
  different_topic: '別の相談',
  multiple_topics: '複数の相談',
  out_of_scope: '対象外',
  guard_needs_review: '対象範囲・変更希望の要確認',
} as const
export const LAB_SKIP_REASONS = {
  answered_before: '以前に回答済み',
  answered_this_turn: '今回回答済み',
  impact_low: '支障が小さいため',
  settings_consultation: '通知設定の相談のため',
  cannot_read_termination: 'LINEを開いても確認できず未解決終了するため',
} as const
export const LAB_CONFIRMATION_REASONS = {
  unmentioned: '未言及のため',
  conditions_not_met: '採用条件を満たさないため',
  unclear: '判定が曖昧なため',
  impact_unassessed: '支障をまだ評価できないため',
  missing_answer: '回答がまだないため',
  multiple_topics: '複数の相談から選ぶため',
  guard_needs_review: '対象範囲・変更希望を確認するため',
} as const
export const LAB_NORMALIZATION_REASONS = {
  eligible: '数値上は採用可能',
  unmentioned: '未言及',
  unclear: '判定が曖昧',
  confidence_below_threshold: 'confidenceの採用条件',
  probability_below_threshold: '最大候補確率の採用条件',
  maximum_not_unique: '最大候補が一つである条件',
  impact_evidence_not_adopted: '支障の根拠を採用できない',
  impact_evidence_absent: '支障の根拠がない',
  noul_between_thresholds: '急ぎの確率が中間',
  score_high_boundary: '支障大の境界',
  noul_urgent_boundary: '急ぎの境界',
  noul_not_urgent_boundary: '急ぎなしの境界',
} as const
export const LAB_END_LABELS = {
  resolved: '通知相談の解決',
  settings_completed: '通知設定の完了',
  unresolved: '未解決終了・LINE公式ヘルプへ案内',
  interrupted: '相談の中断',
} as const
