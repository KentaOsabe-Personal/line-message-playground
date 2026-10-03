export type LabThemeId = 'information' | 'paraphrase' | 'negation' | 'urgency' | 'impact_urgency' | 'unknown_ambiguity' | 'out_of_scope'
export type LabTheme = Readonly<{
  id: LabThemeId; label: string; consultation: string; question: string
  examples: readonly string[]; observation: string; expectation: string
}>

// 期待する結果は、実際の結果と比べるために示す。テーマを選んでも相談状態や判定方針は変更しない。
export const LAB_THEMES: readonly LabTheme[] = [
  {
    id: 'information', label: '情報量', consultation: '同じ通知不達相談の開始時', question: '相談内容を最初に入力する場面',
    examples: ['通知が届きません', 'すべてのトークで通知が届きません。LINEを開けば読めます'],
    observation: '複数の回答を読み取れたか、どの確認が不要になったかを観察します。',
    expectation: '情報が多い文章では、相談の種類に加えて範囲・回避策が確定し、追加の質問が減ることを期待します。',
  },
  {
    id: 'paraphrase', label: '言い換え', consultation: '同じ通知不達相談', question: '同じ開始条件または通知の範囲を確認する場面',
    examples: ['すべてのトークで通知が届きません', '誰から連絡が来ても通知が出ません'],
    observation: '相談の種類と範囲が同じかを観察します。',
    expectation: '異なる表現でも「通知が届かない」「すべてのトーク」という同じ回答を期待します。',
  },
  {
    id: 'negation', label: '否定', consultation: '同じ通知不達相談', question: 'LINEを開けばメッセージを確認できるかを確認する場面',
    examples: ['LINEを開けば読めます', 'LINEを開いても読めません'],
    observation: '回避策を区別できたか、後者で未解決終了・公式ヘルプ案内へ進んだかを観察します。',
    expectation: '前者は回避策あり、後者は確認できないという回答になり、後者は公式ヘルプへ案内して終わることを期待します。',
  },
  {
    id: 'urgency', label: '急ぎ', consultation: '同じ通知設定相談。範囲をそろえ、急ぎは未確定', question: '急ぎの要望を確認する場面',
    examples: ['急いでいません', '今すぐ教えてください'],
    observation: '案内の種類を維持し、Noulと手順の開閉だけが変わったかを観察します。',
    expectation: '急ぎなしでは手順を開き、急ぎありでは要点を先に示して手順を閉じることを期待します。',
  },
  {
    id: 'impact_urgency', label: '支障と急ぎ', consultation: '同じ通知不達相談。急ぎなしを確定し、支障・回避策は未確定', question: '同じ相談状態で支障を含む文章を入力する場面',
    examples: ['家族に聞いて確認できるので支障は小さい', '必要な連絡を確認できず予定を決められない'],
    observation: '支障と急ぎを分けられたか、回避策のChoiceも変化していないかを観察します。差をScoreだけの効果とは断定しません。',
    expectation: '急ぎなしは維持し、支障の判定と追加確認に違いが出ることを期待します。回避策の回答も読み取られる可能性があります。',
  },
  {
    id: 'unknown_ambiguity', label: '不明と曖昧さ', consultation: '同じ相談で範囲は未確定', question: '同じ通知の範囲を確認する場面',
    examples: ['範囲は分かりません', 'うーん、どうだろう'],
    observation: '本人の「分からない」と判定要確認を区別できたかを観察します。',
    expectation: '前者は「分からない」という回答が確定し、後者は回答を確定せず確認することを期待します。',
  },
  {
    id: 'out_of_scope', label: '対象外', consultation: '同じ相談開始条件', question: '相談内容を最初に入力する場面',
    examples: ['LINEの通知設定を知りたい', 'LINEのアカウントを削除したい'],
    observation: '対象内の案内と対応範囲の説明を区別できたかを観察します。',
    expectation: '前者は通知設定の相談へ進み、後者はこのラボの対応範囲を説明することを期待します。',
  },
]
