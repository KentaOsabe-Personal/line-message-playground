# Brief: line-webhook-command-dispatch

## Problem

個人開発者がLINEトーク上で疎通確認やボタン操作を試したくても、検証済みmessage／postbackを安全に意味解釈する境界がない。任意テキストやpostback dataをそのままコマンドとして扱うと、意図しない処理、二重reply、後続機能との密結合を招く。

## Current State

`line-webhook-ingress`がチャネルごとの真正性とイベント重複を保証する予定だが、イベント種別の振り分け、許可済みaction、reply tokenを使う応答境界はない。既存のLINE gatewayは固定宛先push用で、1回限りのreply tokenを扱わない。後続`linked-recipient-delivery`はpostbackによる明示的受取確認を必要とする。

## Desired Outcome

検証済みのuser sourceに対するtext messageとpostbackだけを、型付けされた許可リストから既知のactionへ振り分けられる。初期の固定疎通確認コマンドには一度だけ安全にreplyし、未知・不正・対象外イベントは外部作用なしで終了する。後続specは受付や任意data実行を再実装せず、固有action handlerだけを追加できる。

## Approach

検証済みevent envelopeをtext commandまたはpostback actionへ正規化し、完全一致する固定command／actionだけをdispatcherへ登録する。先行して`line-webhook-ingress`へView入口起点のabsolute deadline、deadline-aware handler context、未dispatch専用receipt結果を追加する。初期scopeでは疎通確認用の固定text commandとcancellable total watchdogを持つreply gatewayを提供し、postbackは許可されたaction名から型付けhandlerへ渡す拡張契約を提供する。reply結果はイベント受付と分けて監査し、結果不明時に同じtokenを自動再利用しない。

## Scope

- **In**: View入口起点の2秒deadlineとhandler予算、未dispatch専用receipt／audit結果、user sourceのtext message、固定の疎通確認コマンド、postback actionの許可リスト、入力正規化と上限、型付けdispatcher契約、チャネル別reply資格情報、reply tokenの一回利用、reply結果の安全な分類、未知・不正・対象外イベントのno-op監査、後続handler拡張点
- **Out**: 汎用自然言語ボット、任意コマンド実行、SQLや動的import、画像・動画・音声処理、group／room、配信固有postback tokenの検証と配信記録更新、push配信、replyの自動再試行、queue／worker

## Boundary Candidates

- 検証済みmessage／postbackの正規化と対象制限
- 固定command／actionの許可リスト型dispatcher
- 1回限りのreply tokenを使う外部API境界
- 後続specが登録する型付けpostback handler契約

## Out of Boundary

- 受信テキストまたはpostback dataをコード、SQL、URL、module名として直接実行すること
- 未連携user、group、roomのイベントへreplyまたは業務actionを実行すること
- タイムアウトや結果不明を理由に同じreplyを自動再送すること
- `linked-recipient-delivery`が所有する受取確認tokenの検証、冪等性、配信記録更新を先取りすること

## Upstream / Downstream

- **Upstream**: `line-webhook-ingress`、`line-channel-foundation`、`line-account-linking`
- **Downstream**: `linked-recipient-delivery`の明示的受取確認action、将来の応答ボット・リッチメニュー操作

## Existing Spec Touchpoints

- **Extends**: `line-webhook-ingress`のView／handler／receipt／safe audit契約をdeadline-awareに拡張する。既存`delivery`のpush gatewayとは分離し、用途別アクセストークン取得と安全な外部エラー分類の原則だけを揃える
- **Adjacent**: Webhook真正性と重複排除は`line-webhook-ingress`、友だち状態は`line-friendship-sync`、配信固有postback処理は`linked-recipient-delivery`が所有する

## Spec Size Assessment

- **Verdict**: PASS (single-spec)
- **Projected executable tasks**: 22〜26件（生成結果23件）
- **Independent responsibility seams**: 3（deadline-aware ingress実行基盤、interaction dispatch、command reply／audit）
- **Rationale**: 23件は通常の単一Spec候補に収まり、3つの内部ワークストリームが一つの疎通確認／action拡張成果へ収束する。タスクグラフは境界を結合せず、`deadline contract → dispatch → reply/audit → signed integration`の依存順を保ったままbounded reviewと独立sanity reviewを通過した。

## Constraints

- 初期commandとpostback actionは完全一致する有限の許可リストとし、未知入力を推測して実行しない
- reply tokenは一度だけ使い、受信後の短い有効時間内に有限timeoutで処理する
- reply外部通信は永続化transactionの外で行い、結果不明時に成功または失敗を推測しない
- 同じ`webhookEventId`の再処理でreplyまたはhandlerを重複実行しない
- text、postback data、reply token、LINEユーザーID、アクセストークン、外部APIの生例外を通常ログまたは公開応答へ含めない
