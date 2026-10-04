# 設計：自由文の文章判定ラボ

2026-10-04改訂。旧通知相談の状態機械とv2契約を撤去し、同じ入口で小さなv3契約へ一括置換する。個人用ラボであり旧画面の互換動作は保持しない。

## API

既存 `POST /api/labs/text-judgment/access` とBearer本人認証を維持する。
`POST /api/labs/text-judgment/judgments` の入力は `{contractVersion: 3, text: string}` のみ。1〜1,000文字に制限し、余計なフィールド・v2入力は400で拒否する。

応答は `{contractVersion: 3, model, elapsedMs, answers: {intent, sentiment, urgency}}`。`intent` はChoiceのchoice/probabilities/confidence、`sentiment` はScoreのscore/probabilities/legend/confidence、`urgency` はNoulのnoulを保持する。`elapsedMs` は既存JevGatewayで測った通信時間で、LINE認証時間は含まない。

質問はBackend `judgment_questions.py` の3つに固定し、Jevのstateは入力文字列のみ。`services.py` は既存制限と認証期限確認を使い、回答の型・確率の範囲と合計・候補・スコア範囲を検証する。閾値や値の補正は設けない。`views.py` はHTTP境界を担当。認証・外部通信・runtimeの既存moduleは再利用する。

## Frontend

`TextJudgmentLabPage` は既存AuthGateを合成。`TextJudgmentLab` は入力と判定の吹き出しを縦に並べる。分類は4本の割合バー、感情は0〜2の目盛り、急ぎは確率。詳細は返却値の折り畳みだけに限定する。

`textJudgmentLabApi` がHTTP、`textJudgmentLabDto` がunknown応答の検証、`useTextJudgmentLab` が入力・送信中・結果・失敗とAbortControllerを管理する。15秒で待機を終了し、送信中の失効・画面離脱・タイムアウト後の応答は破棄する。入力は送信時だけtrimする。過去の判定と入力は画面内だけに保持し、再読込で消える。

## 検証

同一JSON fixtureをBackend HTTPテストとFrontend DTO/APIテストで使用する。任意文・独立送信・生の値・入力境界・不正外部応答・認証失効・失敗復元・後着破棄・二重送信を検証。既存認証・gateway・制限テストは継続する。実Jevとデスクトップ／モバイルの実画面で表示を確認する。

サイズ判定はbriefの5タスクでPASS。新しい永続化・API提供者・設定画面は追加しない。
