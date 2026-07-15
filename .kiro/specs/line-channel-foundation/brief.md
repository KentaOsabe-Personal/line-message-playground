# Brief: line-channel-foundation

## Problem

個人開発者が今後複数のLINE公式アカウントを学習に使う際、単一の環境変数に固定されたアクセストークンとチャネルシークレットでは、チャネルの追加、切り替え、無効化、更新を安全かつ追跡可能に行えない。平文をDBへ保存すると、DB、管理画面、ログ、デバッグ出力から資格情報が漏れる危険がある。

## Current State

既存の`line-message-delivery`は、環境変数の`LINE_CHANNEL_ACCESS_TOKEN`と`LINE_USER_ID`を使って単一チャネル・固定宛先へ送信する。チャネルシークレットはWebhook未導入のため送信処理では利用しておらず、公式アカウントやチャネルを表す永続モデル、資格情報の暗号化、鍵ローテーション、複数チャネルを解決する境界は存在しない。

## Desired Outcome

複数のMessaging APIチャネルをDBへ登録し、アクセストークンとチャネルシークレットを認証付き暗号の暗号文として保存できる。送信処理とWebhook処理は共通の資格情報repositoryから必要な資格情報だけを一時的に復号し、平文をモデル表示、APIレスポンス、通常ログへ露出しない。管理UIが完成する前でも、対話式管理コマンドから安全に初期登録でき、鍵を段階的にローテーションできる。

## Approach

`LineChannel`と1対1の`LineChannelCredential`を新しいBackendドメインappに置き、`cryptography`のFernet／MultiFernetを専用`CredentialCipher`とrepository境界から利用する。暗号文だけをMySQLへ保存し、新しい鍵を先頭に並べた専用環境変数を復号と再暗号化に使用する。チャネル別Webhook候補を署名検証前に選べるよう、チャネルごとに推測困難な公開キーも発行する。

## Scope

- **In**: 複数チャネルのメタデータ、暗号化資格情報、チャネル有効／無効、資格情報repository、fail-fastな鍵設定検証、対話式初期登録・更新コマンド、鍵ローテーションコマンド、マスキング、単体・DBテスト
- **Out**: React管理画面、LIFFログイン、配信先管理、Webhook受信、可変宛先配信、外部KMS、複数管理者権限

## Boundary Candidates

- 公式アカウント／Messaging APIチャネルの識別情報と有効状態
- 資格情報の暗号化・復号・ローテーション
- 平文資格情報を必要なユースケースだけへ渡すrepository
- 管理UI完成前の対話式bootstrap操作

## Out of Boundary

- 資格情報をAPIレスポンスや管理コマンド出力へ返す機能
- Djangoの`SECRET_KEY`と暗号化キーの共用
- DBだけで鍵まで管理する構成
- 異なるLINEプロバイダー間のユーザー統合

## Upstream / Downstream

- **Upstream**: MySQL、Django設定、既存のLINE SDK gateway、ngrok開発導線
- **Downstream**: `line-account-linking`、`line-webhook-interaction`、`linked-recipient-delivery`、`line-channel-admin-ui`

## Existing Spec Touchpoints

- **Extends**: なし。既存`line-message-delivery`を固定環境変数構成の実装済み基準線として保持する
- **Adjacent**: 後続の`linked-recipient-delivery`が既存gatewayの資格情報取得を本repositoryへ移行する。このspecは送信契約自体を変更しない

## Constraints

- 暗号文列は長さ増加を許容する型とし、暗号化失敗・復号失敗・鍵不足を黙殺しない
- 専用マスターキーは環境変数へ残し、暗黙の既定鍵を設けない
- ローテーションは新鍵追加、全暗号文再暗号化、検証、旧鍵撤去の順で中断可能にする
- 平文資格情報、暗号文、チャネルシークレットを`__str__`、例外、ログ、テスト失敗出力へ含めない
- 本specの実装時に、LINE秘密情報をすべて環境変数へ置く現行`tech.md`の記述を新方針へ更新する
