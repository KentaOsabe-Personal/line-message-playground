# Brief: line-webhook-interaction

## Problem

複数のLINE公式アカウントを配信元として使うと、利用者が各アカウントを友だち追加、ブロック解除、ブロックした状態をLIFFだけでは一貫して確認できない。また、トーク上のメッセージやボタン操作をアプリへ取り込み、再送や重複に耐えて処理する受信境界がない。

## Current State

BackendにはWebhook URL、署名検証、イベントモデル、イベントdispatcherが存在しない。チャネルシークレットは送信処理で使用しておらず、配信先は固定で友だち状態を保持しない。queue／workerも未導入である。

## Desired Outcome

各Messaging APIチャネルが専用の不透明なWebhook URLを持ち、LINEから届く生ボディを該当チャネルのシークレットで検証してから処理できる。同じ`webhookEventId`の重複処理を防ぎ、イベント時刻の前後関係を考慮して、チャネル別recipientの友だち状態を更新できる。テキストメッセージとpostbackは限定されたdispatcher契約から安全に処理できる。

## Approach

`POST /api/line/webhooks/{channel_public_key}/`で署名検証候補のチャネルを選び、資格情報repositoryから一時的に復号したシークレットと未加工request bodyでHMAC-SHA256を検証する。検証後に`destination`もチャネルのbot user IDと照合し、`WebhookEvent`を`webhookEventId`の一意制約で受け付ける。初期実装は軽量なDB更新と限定コマンドだけを同期処理し、速やかに2xxを返す。

## Scope

- **In**: チャネル別Webhook URL、raw body署名検証、`destination`照合、疎通確認の空イベント、イベント重複排除、再送識別、時刻順序、`follow`／`unfollow`、限定テキストコマンド、postback dispatcher、処理結果の安全な監査
- **Out**: 汎用自然言語ボット、画像・動画の恒久保存、group／room管理、Beacon、queue／worker、配信到達・既読Webhook、別サービスとの公式アカウント連携イベント

## Boundary Candidates

- チャネル選択と署名検証
- Webhook受付・重複排除・処理状態
- follow／unfollowによるrecipient状態遷移
- message／postbackの許可リスト型dispatcher
- 応答トークンを使う即時replyの外部API境界

## Out of Boundary

- 公開パスキーだけを認証根拠とすること
- 署名検証前のJSON本文や`destination`を信用すること
- 受信した任意テキストをコマンドやSQLとして実行すること
- Webhook再送を完全な配信保証とみなすこと

## Upstream / Downstream

- **Upstream**: `line-channel-foundation`、`line-account-linking`、ngrok公開HTTPS URL
- **Downstream**: `linked-recipient-delivery`の明示的受取確認、将来の応答ボット・リッチメニュー操作

## Existing Spec Touchpoints

- **Extends**: なし
- **Adjacent**: `DeliveryRecipient`のチャネル別状態を更新する。既存`line-message-delivery`のpush結果状態はWebhookで上書きしない

## Constraints

- 生のrequest bodyを変更・JSON解析する前に署名検証する
- 同一イベントの再送とイベント順序の逆転を前提にする
- 署名、チャネルシークレット、LINEユーザーID、受信本文を通常ログへ出さない
- 正常受付後は速やかに2xxを返し、初期同期処理に長時間の外部通信を含めない
- `follow.isUnblocked`を完全に正確な履歴情報とみなさない
