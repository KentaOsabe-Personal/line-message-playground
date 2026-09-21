const NOTIFICATION_HELP_URL = 'https://help.line.me/line/ios/?contentId=20000276&lang=ja'
const CHAT_NOTIFICATION_GUIDE_URL = 'https://guide.line.me/ja/account-and-settings/notification-chatroom.html'

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
  safetyNotice: '入力は外部サービスJevへ送信します。個人情報を含まない架空の相談を使ってください。会話はこのページだけに保持されます。',
  examples: [
    { id: 'missing_notification', text: 'LINEの通知が届きません' },
    { id: 'notification_settings', text: 'LINEの通知の設定方法を知りたいです' },
  ],
  questions,
  guides,
  messages: {
    cannotRead: '通知設定以外の問題の可能性があります。LINE公式ヘルプを確認してください。',
    missingUnresolved: 'まだ通知が届かないため、この相談は未解決として終了します。詳しい対処はLINE公式ヘルプを確認してください。',
    settingsUnresolved: '通知を設定できないため、この相談は未解決として終了します。詳しい設定方法はLINE公式ヘルプを確認してください。',
    outOfScope: 'このラボで扱えるのは「通知が届かない」「通知の設定方法を知りたい」の2種類です。入力例から選ぶこともできます。',
    restartRequired: '確定した回答は途中で変更できません。現在の相談を終了し、新しい相談を始めてください。',
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
