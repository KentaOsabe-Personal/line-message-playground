# Brief: line-webhook-ingress

## Problem

複数のLINE公式アカウントを使う個人開発者には、インターネットから届くWebhookをチャネルごとに真正なLINEイベントとして検証し、再送や重複に耐えて後続処理へ渡す共通の受信境界がない。公開URLの値や署名検証前の本文を信用すると、第三者の偽造イベントや別チャネル宛てイベントを処理する危険がある。

## Current State

`line-channel-foundation`はチャネルごとの不透明な公開識別子、bot user ID、暗号化済みチャネルシークレット、用途別資格情報取得を提供している。ngrokから`/api`へ到達するHTTPS導線もあるが、Webhook URL、raw body署名検証、`destination`照合、イベント台帳、重複排除、後続handler向けの検証済みイベント契約は存在しない。

## Desired Outcome

各有効チャネルが専用Webhook URLを持ち、LINEから届いた未加工bodyを該当チャネルのシークレットで検証してからだけ内容を信頼できる。検証済みイベントは`webhookEventId`単位で一度だけ受け付けられ、再送、空イベント、未知イベントを安全に処理し、後続specが署名検証を繰り返さず利用できる。

## Approach

`POST /api/line/webhooks/{channel_public_key}/`を公開受付とし、公開識別子は検証候補の選択だけに使う。raw bodyの署名検証後にJSONと`destination`を検証し、イベント台帳の一意性を受付の線形化点とする。受付結果と安全な処理分類だけを監査し、検証済みの正規化イベントenvelopeを同期handlerへ渡せる境界を用意する。

## Scope

- **In**: チャネル別Webhook URL、raw body署名検証、`destination`照合、LINE Developersコンソールの空イベント疎通、payload基本検証、`webhookEventId`重複排除、再送識別、イベント受付状態、後続handler向け検証済みevent envelope、安全な監査、速やかな2xx応答
- **Out**: follow／unfollowによるrecipient更新、message／postbackの意味解釈、reply送信、画像・動画本文の恒久保存、group／room管理、Beacon、queue／worker、配信到達・既読保証

## Boundary Candidates

- 公開識別子によるチャネル候補選択とfail-closedな資格情報取得
- raw body署名検証と検証後の`destination`照合
- イベント台帳による受付・重複・再送・処理分類
- 後続機能へ渡す検証済みevent envelope

## Out of Boundary

- 公開識別子、送信元IP、署名検証前のJSONを認証根拠にすること
- 受信本文、署名、チャネルシークレット、LINEユーザーIDを通常ログや公開応答へ出すこと
- Webhook再送を完全な配信保証またはhandlerの無制限再実行として扱うこと
- イベント種別固有の業務状態を更新すること

## Upstream / Downstream

- **Upstream**: `line-channel-foundation`、ngrok公開HTTPS導線
- **Downstream**: `line-friendship-sync`、`line-webhook-command-dispatch`、`linked-recipient-delivery`、Webhook接続状態を表示する`line-channel-admin-ui`

## Existing Spec Touchpoints

- **Extends**: なし。`line-channel-foundation`の用途別チャネルシークレット取得を利用し、署名検証後の`destination`照合に必要な非秘密チャネル情報の取得契約だけを接続点とする
- **Adjacent**: `line-account-linking`のidentity／recipient状態は変更しない。既存`line-message-delivery`のpush結果も変更しない

## Spec Size Assessment

- **Verdict**: PASS (single-spec)
- **Projected executable tasks**: 9〜11件（route・公開permission、チャネル解決、署名／payload検証、migration、重複・競合、event envelope、HTTP・MySQL・セキュリティテスト、運用文書を含む）
- **Independent responsibility seams**: 1（真正性を確認したイベントを一度だけ受け付ける境界。署名／destination検証とイベント台帳はこの保証に不可分）
- **Rationale**: 固有イベントの状態更新や外部replyを除外し、1つのセキュリティ成果と一意な受付保証へ収束するため

## Constraints

- 生のrequest bodyを変更・JSON解析する前に署名を検証する
- 無効、不明、資格情報欠損、署名不正、`destination`不一致のチャネルはfail closedとする
- 同じイベントの並行到着と再送を前提にし、後続handlerを重複実行しない
- 正常に検証・受理した要求は軽量な同期処理に限定し、速やかに2xxを返す
- raw本文、署名、秘密値、LINEユーザーID、下位層の生例外を通常ログまたは公開応答へ含めない
