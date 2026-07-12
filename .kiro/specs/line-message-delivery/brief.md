# Brief: line-message-delivery

## Problem
LINE Messaging API を学習する個人開発者が、件名と本文を指定して自分の LINE アカウントへ安全にテスト配信する手段を必要としている。現状は Frontend と Backend の疎通確認までしか実装されておらず、実際のメッセージ送信、誤送信防止、二重送信防止、結果追跡を一連の操作として試せない。

## Current State
React Frontend、Django REST API、MySQL のローカル開発基盤と、`LINE_CHANNEL_ACCESS_TOKEN`、`LINE_CHANNEL_SECRET`、`LINE_USER_ID` を Backend へ渡す環境変数配線は存在する。一方、LINE 配信用の Django app、送信 API、公式 SDK、配信記録、件名・本文入力 UI は未実装である。

LINE のテキストメッセージには独立した件名フィールドがないため、入力された件名と本文を送信用テキストへ変換する必要がある。

## Desired Outcome
利用者が件名と本文を入力し、整形後の配信内容を確認してから最終送信を実行できる。Backend は固定の `LINE_USER_ID` に対して LINE 公式 Python SDK の保守対象である `linebot.v3` API から同期 push 送信し、二重送信を防止するとともに、成功・失敗を追跡可能な形で記録する。Frontend は送信結果を明確に表示する。

送信テキストは次の形式とする。

```text
【件名】

本文
```

## Approach
LINE 公式 Python SDK を Backend のサービス境界内で使用し、同期的に push message API を呼び出す。SDK の `linebot.v3` API と retry key を利用し、アプリケーション側の一意な冪等性キーおよび配信記録と組み合わせて、連打や再試行による二重送信を防止する。

MySQL には入力内容、整形後テキスト、送信対象方式、処理状態、冪等性キー、送信日時、LINE リクエスト ID、失敗情報を保存する。Frontend は入力、内容確認、送信中の操作抑止、成功・失敗表示を担当し、LINE の認証情報やユーザー ID は参照しない。

公式 SDK は調査時点の `line-bot-sdk` 3.25.0 が Python 3.14 を明示的にサポートし、Apache-2.0 で提供され、Django 6 との既知の依存衝突や同期 push を妨げる既知の重大問題がないことを確認済みである。実装時も保守対象の `linebot.v3` のみを使用する。

## Scope
- **In**: 件名・本文の入力と検証、`【件名】\n\n本文` 形式への整形、送信前の内容確認、固定 `LINE_USER_ID` への同期 push 送信、LINE 公式 Python SDK の導入、冪等性による二重送信防止、配信結果と LINE リクエスト ID の記録、Frontend での送信中・成功・失敗表示、Backend の HTTP 契約と LINE 呼び出しをモックしたテスト
- **Out**: broadcast・multicast・narrowcast、宛先選択とユーザー管理、Webhook、応答メッセージ、予約配信、非同期ジョブ、失敗時の自動再送、月間利用上限の監視、本番向けの認証・認可、大規模配信

## Boundary Candidates
- Frontend の配信フォーム、確認操作、送信状態および結果表示
- Backend の入力検証と配信 HTTP API 契約
- LINE SDK 呼び出し、テキスト整形、外部エラー変換を担う配信サービス
- 冪等性、配信状態、監査情報を保持する永続化モデル

## Out of Boundary
- LINE ユーザー ID の収集、登録、変更、削除を行う管理機能
- Webhook の公開 HTTPS、署名検証、イベント処理
- 複数宛先や不特定多数を対象とするキャンペーン管理
- ワーカー、メッセージキュー、スケジューラーによるバックグラウンド処理
- 本番公開を前提とした利用者認証、権限管理、レート制限
- 料金プランや月間メッセージ利用量の取得・監視

## Upstream / Downstream
- **Upstream**: 既存の Docker Compose 開発環境、React から `/api/...` へ接続する Vite proxy、Django REST Framework、MySQL、Backend に注入済みの LINE 環境変数、同一 LINE チャネルから取得した有効な `LINE_USER_ID`
- **Downstream**: 配信履歴の一覧・詳細表示、月間利用量確認、broadcast 等の送信方式追加、宛先管理、Webhook、予約配信や非同期再送

## Existing Spec Touchpoints
- **Extends**: なし。既存 spec は存在しない
- **Adjacent**: `health` Django app と既存の Frontend 疎通確認画面。配信機能は独立した Django app に置き、health check の責務へ混在させない

## Constraints
- ローカルの個人学習環境と自分宛て配信を対象とし、外部公開や本番運用を前提としない
- LINE のチャネルアクセストークン、チャネルシークレット、ユーザー ID は Backend の環境変数だけから参照し、Frontend、API レスポンス、Git、ログへ露出させない
- LINE 公式 Python SDK は `linebot.v3` API を使用し、旧 2.x API は使用しない
- 固定宛先には、送信チャネルと同一チャネルで取得した有効な `LINE_USER_ID` を使用する
- LINE API の 400、401、403、409 相当、429、タイムアウト、5xx を利用者向け結果と監査記録へ安全に変換し、秘密情報や外部レスポンス全文を露出させない
- 送信は取り消せない外部作用として扱い、確認操作と冪等性検査を通過するまで LINE API を呼び出さない
- LINE API のテキスト長制限を超えないよう、件名・本文の入力長と整形後テキスト長を Backend で検証する
- Frontend は相対パス `/api/...` だけを使用し、LINE API を直接呼び出さない
