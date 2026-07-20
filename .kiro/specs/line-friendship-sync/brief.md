# Brief: line-friendship-sync

## Problem

LIFFに直接紐づかないLINE公式アカウントでは、ownerが友だち追加、ブロック解除、ブロックしてもチャネル別recipientの友だち状態を一貫して確認できない。Webhookは再送や順序逆転があり得るため、単純な最終書き込みでは古いイベントが新しい状態を上書きする。

## Current State

`line-account-linking`は検証済みLINE identityとチャネル別`DeliveryRecipient`を保持し、友だち状態を`friend`、`not_friend`、`unknown`で区別する。LIFFに直接紐づかないチャネルは`unknown`で始まり、利用者が管理する有効状態とLINE上の友だち状態は分離されている。一方、Webhook由来の状態更新、イベント時刻、順序競合の解決契約はない。

## Desired Outcome

`line-webhook-ingress`が検証したuser sourceのfollow／unfollowイベントから、同一プロバイダー・チャネルに属する既存recipientだけを特定し、イベント時刻順に友だち状態を更新できる。重複、遅延、順序逆転、連携解除と競合しても、新しい状態や利用者自身の有効／無効設定を古いイベントで上書きしない。

## Approach

検証済みevent envelopeからチャネル、source user、イベント種別、発生時刻を受け取り、既存のidentity×channel関係を解決する。recipientには最後に反映した友だち状態イベントの順序情報を保持し、新しいイベントだけを原子的に反映する。未連携userのイベントはidentityやrecipientを自動作成せず、安全な非対応分類として監査する。

## Scope

- **In**: user sourceのfollow／unfollow、followのブロック解除情報、既存identity／recipient照合、`friend`／`not_friend`遷移、イベント時刻による順序制御、同時更新、連携解除との競合、未連携イベントの安全な非対応分類、状態更新監査
- **Out**: LINE identityまたはrecipientの新規登録、表示名取得、利用者設定の有効／無効変更、message／postback処理、reply、group／room、配信結果・既読・到達状態の変更

## Boundary Candidates

- 検証済みuser sourceと既存identity／recipientの照合
- follow／unfollowから友だち状態へのprojection
- イベント順序、同時更新、unlink後イベントの整合性
- 未連携userを作成しない非対応イベント処理

## Out of Boundary

- Webhookだけを根拠にLINE identity、owner、recipientを新規作成すること
- `follow.isUnblocked`を完全な過去履歴として扱うこと
- 友だち状態の更新を理由に利用者が設定したrecipientの有効状態を変更すること
- push成功やpostbackを友だち状態、端末到達、既読として扱うこと

## Upstream / Downstream

- **Upstream**: `line-webhook-ingress`、`line-account-linking`
- **Downstream**: `linked-recipient-delivery`の配信可否判定、`line-channel-admin-ui`の状態表示

## Existing Spec Touchpoints

- **Extends**: なし。`line-account-linking`が後続Webhookへ委譲した既存`DeliveryRecipient`の友だち状態projectionを所有する
- **Adjacent**: identity／recipientの登録、利用者による無効化・再有効化・連携解除は`line-account-linking`の契約を維持する。配信状態は既存`line-message-delivery`および後続`linked-recipient-delivery`が所有する

## Spec Size Assessment

- **Verdict**: PASS (single-spec)
- **Projected executable tasks**: 7〜9件（順序情報migration、照合契約、follow／unfollow遷移、競合制御、未連携・解除済み処理、単体・MySQL統合テストを含む）
- **Independent responsibility seams**: 1（検証済み友だちイベントを既存recipient状態へ時系列どおり投影する状態機械）
- **Rationale**: identity登録、Webhook受付、command、配信を除外し、1つの既存集約に対する状態projectionへ限定するため

## Constraints

- 未連携userのイベントからidentity、owner、recipientを自動作成しない
- LINEユーザーIDは照合のためBackend内だけで扱い、画面、公開API、通常ログへ出さない
- 同じ時刻または順序逆転を決定的に処理し、古いイベントで新しい状態を上書きしない
- recipientの連携解除またはowner全連携解除と競合したイベントは、削除済み関係を復元しない
- 状態更新は軽量な同期処理とし、外部API通信を追加しない
