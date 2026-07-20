# Brief: line-channel-admin-ui

## Problem

複数のLINE公式アカウントを継続的に試す際、対話式管理コマンドだけではチャネルの追加、資格情報更新、有効状態、Webhook設定値、接続状態を把握しにくい。一方、秘密値を通常の編集フォームへ読み戻す管理画面は、ブラウザ、APIレスポンス、DOM、ログからの漏えいを招く。

## Current State

`line-channel-foundation`でチャネル、暗号化資格情報、bootstrapコマンド、repositoryが提供される予定だが、専用Frontendはない。既存の配信画面はメッセージ入力だけを扱い、Django Adminは主要な利用者導線として設計されていない。利用者認証は`line-account-linking`で確立される。

## Desired Outcome

認証済みの自分だけが、専用画面からLINE公式アカウント／Messaging APIチャネルを一覧、登録、更新、有効化、無効化できる。アクセストークンとチャネルシークレットはwrite-onlyとして入力時だけ置換され、既存値、平文、暗号文を画面やAPIへ返さない。設定済み状態、更新日時、Webhook URL、接続確認結果は秘密値なしで確認できる。

## Approach

`line-channel-foundation`のサービス境界上に、owner認可されたDRF APIとReact管理画面を追加する。秘密入力は常に空で表示し、空欄を変更なし、値ありを置換として扱う。更新APIはsensitive parameterとしてマスキングし、接続確認はBackendが資格情報を一時復号して安全な成否分類だけを返す。参照中チャネルは履歴保全のため原則無効化し、物理削除を制限する。

## Scope

- **In**: チャネル一覧・詳細・新規登録・メタデータ更新、有効化・無効化、write-only資格情報置換、設定済み表示、Webhook URL表示・コピー、接続確認、安全な削除制約、Frontend状態・API契約・テスト
- **Out**: 暗号化キー管理画面、秘密値の表示・コピー・export、複数管理者RBAC、LINE Developersコンソール設定の自動変更、利用量課金管理、公式アカウント自体の作成

## Boundary Candidates

- owner限定チャネル管理API
- write-only資格情報フォームと置換契約
- 秘密値を返さない接続確認
- 参照整合性を守る無効化・削除
- Frontendの一覧・編集・エラー状態

## Out of Boundary

- 保存済みアクセストークンやチャネルシークレットの復号表示
- 暗号化マスターキーのブラウザ入力・DB保存
- 未認証状態や一時的な共有URLからの管理操作
- LINE DevelopersコンソールやOfficial Account Managerの代替

## Upstream / Downstream

- **Upstream**: `line-channel-foundation`、`line-account-linking`、`line-webhook-ingress`、`line-friendship-sync`、`line-webhook-command-dispatch`、`linked-recipient-delivery`
- **Downstream**: 月間利用量ダッシュボード、チャネル疎通診断、運用監査画面

## Existing Spec Touchpoints

- **Extends**: なし
- **Adjacent**: 配信画面が利用するチャネル選択情報と同じ公開DTOを再利用できるが、配信操作と資格情報更新を同じAPIにしない

## Constraints

- 資格情報フィールドはAPI schema上もwrite-onlyとし、既存値をFrontendへ返さない
- 空欄は変更なしとし、資格情報の削除・無効化は明示操作に分ける
- エラー報告、監査ログ、テスト失敗出力でPOST値と復号済み変数をマスキングする
- 配信先や配信履歴に参照されるチャネルの物理削除を防ぎ、無効化後も監査記録を保持する
- 接続確認結果は秘密値やLINE raw responseを含まない安全な分類で返す
