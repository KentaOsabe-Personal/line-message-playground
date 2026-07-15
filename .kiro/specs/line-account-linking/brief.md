# Brief: line-account-linking

## Problem

個人開発者がスマートフォンのLINEからアプリを利用する際、現在の未認証Web画面では本人だけが操作していることを確認できず、LINEユーザーと配信先を安全に結び付けられない。手入力したユーザーIDでは、なりすましや異なるプロバイダーのID混在を防げない。

## Current State

FrontendにはLIFF SDK、ログイン状態、セッション処理がなく、Backend APIはローカル専用として認証・permissionを無効にしている。固定の`LINE_USER_ID`だけが環境変数にあり、LINE identity、利用者、チャネル別配信先関係、連携解除を表すモデルは存在しない。

## Desired Outcome

利用者がngrok経由でLIFFを開き、LINEプラットフォームが発行したトークンをBackendで検証して、自分専用のDjangoセッションを確立できる。検証済みのLINEユーザーを最小限のidentityとして保存し、Messaging APIチャネルごとに配信先関係を登録、表示、無効化、連携解除できる。未認証または所有者以外の利用者は配信・管理APIを操作できない。

## Approach

LIFFを共通の操作入口とし、FrontendからIDトークンをBackendへ渡してLINE側で検証する。Backendは検証済み`sub`を同一プロバイダー内のLINEユーザーIDとして扱い、初期所有者に限定したDjangoセッションを発行する。`LineIdentity`と「identity × Messaging APIチャネル」の`DeliveryRecipient`を分け、友だち状態や配信可否をチャネル単位で保持できるようにする。

## Scope

- **In**: LIFF初期化、外部ブラウザ時のLINEログイン、IDトークンのBackend検証、自分専用認可、Djangoセッション、CSRF、LINE identity、チャネル別配信先登録・一覧・無効化・連携解除、最小限のプロフィール表示
- **Out**: 複数アプリ利用者、メール／パスワード認証、SNSアカウント統合、異なるプロバイダー間の本人統合、Webhook状態同期、メッセージ配信、チャネル資格情報管理画面

## Boundary Candidates

- LIFF SDKとFrontend認証状態
- IDトークン検証とDjangoセッション発行
- 自分専用owner認可
- LINE identityとチャネル別recipient関係
- 連携解除とユーザーデータ削除

## Out of Boundary

- `liff.getProfile()`の結果だけを信用したBackend登録
- LINEユーザーIDの手入力・表示・API返却
- 1つのLIFFから複数公式アカウントすべての友だち状態を直接取得すること
- LINE公式のMessaging APIアカウント連携フローによる別サービスアカウント統合

## Upstream / Downstream

- **Upstream**: `line-channel-foundation`、ngrokの同一origin開発導線、LINE Loginチャネル、LIFFアプリ
- **Downstream**: `line-webhook-interaction`、`linked-recipient-delivery`、`line-channel-admin-ui`

## Existing Spec Touchpoints

- **Extends**: なし
- **Adjacent**: 既存`line-message-delivery`の公開APIは、本specで確立する認証・permission境界の保護対象になるが、配信内容・冪等性契約は変更しない

## Constraints

- LINE LoginチャネルとMessaging APIチャネルは最初は同一プロバイダー配下を前提とする
- 1つのLINE Loginチャネルにリンクできる公式アカウントは1つであるため、それ以外の友だち状態は後続Webhookで管理する
- IDトークン、アクセストークン、LINEユーザーIDを通常ログへ出さない
- ngrok HTTPSを正しく認識し、公開originのCSRF、Secure Cookie、SameSite方針をテストする
- 自分専用ownerの初期確立方法は、第三者による先取り登録を防止する
