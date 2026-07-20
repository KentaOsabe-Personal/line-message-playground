# Roadmap

## Overview

LINE Message Playground を、固定設定の自分宛て配信から、LIFF／LINEログイン、Webhook、複数のLINE公式アカウント、登録済み配信先を扱える自分専用の通知コンソールへ段階的に拡張する。

最初にDocker Composeへngrokを直接組み込み、スマートフォンのLINEアプリからローカルのFrontendとWebhookへ到達できる開発導線を作る。その後、チャネル資格情報の暗号化基盤を先行させ、LINEアカウント連携、Webhookによる友だち状態同期、連携先への安全な配信、専用管理画面の順で新規specを進める。

## Approach Decision

- **Chosen**: 基盤先行・管理UI後付け。ngrokを直接実装した後、複数チャネルと暗号化資格情報を共通基盤として作り、LIFF、Webhook、配信、管理UIを依存順に積み上げる
- **Why**: 認証のない段階で秘密情報管理画面を公開せず、配信とWebhookが同じ資格情報取得境界を利用できる。既存の固定宛先配信specを実装済みの基準線として保持し、後続specで意図的に移行できる
- **Rejected alternatives**: 管理UI先行は仮の認証実装や作り直しが発生する。公式アカウント単位の縦割り実装は、資格情報、本人性、Webhook、配信の共通化が遅れ、2アカウント目で再設計しやすい

## Scope

- **In**: ngrokによる開発用HTTPS導線、複数Messaging APIチャネル、暗号化したアクセストークンとチャネルシークレットのDB保存、LIFF／LINEログイン、配信先登録、チャネル別Webhook、友だち状態同期、登録済み連携先への配信、明示的な受取確認、チャネル管理画面
- **Out**: 不特定多数向けサービス、複数管理者のRBAC、異なるプロバイダー間の本人統合、broadcast／narrowcast、予約配信、汎用チャットボット、配信到達・既読の保証、本番公開基盤、外部KMSやワーカー基盤

## Constraints

- 自分だけが利用する個人学習環境を維持し、学習に不要なLINEユーザーデータを保存しない
- ngrokは開発用途に限定して通常のComposeサービスとして起動する。Compose起動中は現在の未認証APIも公開されるため、公開URLを共有せず、利用後は全サービスを停止する
- ngrokのauthtokenはLINE資格情報とは別のインフラ秘密情報として環境変数から注入し、DBへ保存しない
- LINEのアクセストークンとチャネルシークレットは認証付き暗号で暗号化してDBへ保存し、専用の暗号化マスターキーだけを環境変数へ残す
- 暗号化キーを失うと復号できないため、ローテーション手順とDB外バックアップ方針を持つ
- 最初は同一プロバイダー配下のLINE Login／Messaging APIチャネルを対象とする。同一人物でも異なるプロバイダーではユーザーIDが異なる
- 1つのLINEログインチャネルにリンクできるLINE公式アカウントは1つである。2つ目以降の友だち状態は各Messaging APIチャネルのWebhookを主情報とする
- Webhookはチャネル別の不透明な公開キーで候補を選び、生のrequest bodyに対する署名検証後にだけ内容を信頼する
- queue／workerを新設しない初期段階では、Webhook同期処理を重複記録と軽量な状態更新に限定して速やかに2xxを返す
- 通常のpush成功を端末到達や既読とみなさず、postbackは利用者による明示的な受取確認として区別する

## Boundary Strategy

- **Why this split**: チャネル資格情報、本人認証、受信イベント、送信操作、秘密情報管理UIはそれぞれ異なるセキュリティ境界と検証方法を持つ。共通基盤から依存順に分けることで、各specを独立してレビュー・検証できる
- **Shared seams to watch**: チャネルIDとユーザーIDの対応、資格情報の復号境界、チャネル別友だち状態、確認トークンと冪等性fingerprintに含める送信対象、Webhook postbackと配信記録の関連付け

## Direct Implementation Prerequisite

- [x] ngrok-compose-development-tunnel -- 公式ngrok Agentを通常のComposeサービスへ追加し、固定の開発用HTTPSドメインからViteと`/api`へ到達できるようにする。実装・ローカル疎通確認済み。外部トンネルの実機確認には利用者固有の`NGROK_AUTHTOKEN`と`NGROK_DOMAIN`が必要。Dependencies: none

## Specs (dependency order)

- [x] line-channel-foundation -- 複数Messaging APIチャネルと暗号化資格情報をDBで管理し、安全な取得・初期登録・鍵ローテーション境界を提供する。Dependencies: ngrok-compose-development-tunnel
- [x] line-account-linking -- LIFF／LINEログインで本人確認し、LINE identityとチャネル別配信先関係を登録・解除する。Dependencies: line-channel-foundation
- [ ] line-webhook-interaction -- チャネル別Webhookを検証・重複排除し、友だち状態、メッセージ、postbackを安全に処理する。Dependencies: line-channel-foundation, line-account-linking
- [ ] linked-recipient-delivery -- 登録済みチャネルと配信先を選び、既存の確認・冪等性・監査を維持してpushし、明示的な受取確認を追跡する。Dependencies: line-channel-foundation, line-account-linking, line-webhook-interaction
- [ ] line-channel-admin-ui -- 自分専用の認証済み画面からチャネルとwrite-only資格情報を登録・更新・無効化する。Dependencies: line-channel-foundation, line-account-linking, line-webhook-interaction, linked-recipient-delivery
