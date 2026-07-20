# Brief: linked-recipient-delivery

## Problem

個人開発者がLIFFで連携した配信先と複数のLINE公式アカウントを使ってテスト配信したくても、現在の機能は環境変数に固定した単一ユーザー・単一チャネルにしか送信できない。単純に宛先入力を追加すると、確認後の対象差し替え、異なるチャネルへの誤送信、冪等キーの衝突、監査情報不足が発生する。

## Current State

既存`line-message-delivery`は、件名と本文の検証、整形後プレビュー、確認トークン、固定宛先への同期push、LINE retry key、二重送信防止、`processing`／`succeeded`／`failed`／`unknown`、LINE request ID記録を実装済みである。一方、`DeliveryAttempt.target_mode`はDB制約を含め`fixed_user`だけで、チャネルと宛先を監査記録、確認トークン、fingerprintへ含めていない。

## Desired Outcome

認証済み利用者が、登録済みで有効なチャネルとそのチャネルで配信可能なrecipientを選び、実際の配信元・配信先・整形済み内容を確認してから送信できる。確認後に対象または内容が変われば再確認を要求し、同じ操作は同じ対象・内容の結果へ収束する。配信記録から使用チャネル、recipient、送信時点の対象状態、LINE結果、利用者による明示的受取確認を追跡できる。

## Approach

既存の配信サービスと状態機械を維持しながら、送信commandへ内部`channelId`と`recipientId`を追加する。Backendで所有権、有効状態、チャネル別友だち状態を検証し、確認トークンとcontent fingerprintへ対象contextを含める。Gatewayは環境変数を直接読まず、選択チャネルの資格情報をrepositoryから受け取る。必要に応じて署名付きpostbackトークンを含む「受け取りました」操作を送り、`line-webhook-command-dispatch`のaction契約経由で当該配信だけを確認済みにする。

## Scope

- **In**: 配信元チャネル・配信先選択、対象のBackend検証、対象込みプレビュー、確認トークン、冪等性、監査migration、DB資格情報によるpush、対象変更時の再確認、友だち状態による抑止、明示的受取確認、Frontend状態遷移・API契約・テスト
- **Out**: multicast、broadcast、narrowcast、予約配信、自動再送、配信到達保証、既読取得、任意ユーザーID入力、複数メッセージキャンペーン

## Boundary Candidates

- 認証済み利用者が選択可能なチャネル・recipient一覧
- 対象と内容に結び付く確認トークン
- 対象contextを含む冪等性と配信監査
- 資格情報repositoryを使うチャネル別LINE gateway
- Webhook postbackによる明示的受取確認

## Out of Boundary

- LINEユーザーID、アクセストークン、チャネルシークレットのFrontend送信・表示
- push APIの2xxを端末到達や既読として表示すること
- ブロック中または状態不明のrecipientへの黙示的な送信
- 失敗・結果不明時の別operation IDによる自動再送

## Upstream / Downstream

- **Upstream**: 実装済み`line-message-delivery`、`line-channel-foundation`、`line-account-linking`、`line-friendship-sync`、`line-webhook-command-dispatch`
- **Downstream**: `line-channel-admin-ui`、配信履歴画面、月間利用量確認、将来の複数宛先配信

## Existing Spec Touchpoints

- **Extends**: なし。既存`line-message-delivery`を初期版の承認済み仕様として残し、本specが後続migrationと新しい公開契約を所有する
- **Adjacent**: 既存のformatting、confirmation、delivery service、gateway、Frontend reducerを再利用・拡張する。Webhook受付は`line-webhook-ingress`、汎用postback振り分けは`line-webhook-command-dispatch`が所有し、本specは受取確認tokenの検証と配信記録更新を所有する

## Constraints

- チャネル・recipientの変更を確認済み内容の変更として扱う
- 操作ID、LINE retry key、監査記録を引き続き一貫させる
- 外部通信をDB transaction内で実行しない
- terminal状態を後続Webhookや再試行で上書きしない。明示的受取確認は別属性として記録する
- 既存の失敗分類、unknown状態、安全な概要、秘密情報非露出を維持する
