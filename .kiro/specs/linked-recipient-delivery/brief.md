# Brief: linked-recipient-delivery

## Problem

個人開発者がLIFFで連携した配信先と複数のLINE公式アカウントを使ってテスト配信したくても、現在の機能は環境変数に固定した単一ユーザー・単一チャネルにしか送信できない。単純に宛先入力を追加すると、確認後の対象差し替え、異なるチャネルへの誤送信、冪等キーの衝突、監査情報不足が発生する。

## Current State

既存`line-message-delivery`は、件名と本文の検証、整形後プレビュー、確認トークン、固定宛先への同期push、LINE retry key、二重送信防止、`processing`／`succeeded`／`failed`／`unknown`、LINE request ID記録を実装済みである。一方、`DeliveryAttempt.target_mode`はDB制約を含め`fixed_user`だけで、チャネルと宛先を監査記録、確認トークン、fingerprintへ含めていない。

## Desired Outcome

認証済み利用者が、登録済みで有効なチャネルとそのチャネルで配信可能なrecipientを選び、実際の配信元・配信先・整形済み内容を確認してから送信できる。確認後に対象または内容が変われば再確認を要求し、同じ操作は同じ対象・内容の結果へ収束する。配信記録から使用チャネル、recipient、送信時点の対象状態、LINE結果、利用者による明示的受取確認を追跡できる。

## Approach

既存の配信サービスと状態機械を維持しながら、送信commandへ内部`channelId`と`recipientId`を追加する。Backendで所有権、有効状態、チャネル別友だち状態を検証し、確認トークンとrequest fingerprintへowner principal、送信時identity、対象contextを含める。状態照会はunlink後も残るsingleton owner slotで認可し、削除されるidentity UUIDは監査snapshotへ分離する。Gatewayは環境変数を直接読まず、選択チャネルの資格情報をrepositoryから受け取る。必要に応じてpostback capability candidateをaccept前のmemory上で生成し、勝者のdigestだけをattemptと原子的に保存して「受け取りました」操作へ使用する。

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

## Spec Size Assessment

- **Policy verdict before exception**: `SPLIT_REQUIRED`（設計具体化後の上限見積りが40件以上）
- **Effective verdict**: `PASS (single-spec, user-approved size exception)`。2026-07-23にユーザーがサイズ超過リスクを受容して単一Spec継続を明示した
- **Projected executable tasks**: 35〜42件（選択API、対象検証、migration、確認・冪等性、チャネル別push、受取確認action、Frontend、競合・統合・セキュリティテストを含む）
- **Independent responsibility seams**: 5（選択可能なチャネル・recipient一覧、対象込み確認、対象contextを含む冪等性・監査、チャネル別LINE gateway、明示的受取確認）
- **Independently deliverable outcomes**: 2（登録済みrecipientへの選択配信、当該配信に対する明示的受取確認）。ただし、両者は同じ配信記録と利用者向けの一連の確認体験へ収束する
- **External/stateful workflows**: 2（確認後のpush配信、Webhook postbackによる受取確認）。pushのterminal状態と受取確認属性を分離し、後続Webhookが配信結果を上書きしない
- **Internal workstreams and dependency order**: `公開契約・migration → 選択肢取得と対象検証 → 対象込み確認・冪等性・gateway → 受取確認action → Frontend・境界横断統合`
- **Review and validation strategy**: RequirementsとDesignで責任境界、共有する配信記録、外部通信前後の状態遷移を個別にレビューする。Tasksでは実数を隠さずworkstream別task-graph sanity reviewを行い、file owner、contract、依存順、integration checkpointを明示する
- **Rationale**: policy上は上限42件で分割対象だが、ユーザーがレビュー負荷・実装期間・手戻りリスクを理解して単一Spec継続を承認した。5つの内部境界は一つの「選択した登録済みrecipientへ安全に送り、その配信を追跡する」成果を共同で構成し、pushと受取確認も同じ配信記録、status API、Frontend、rolloutへ収束する
- **Exception boundary**: 例外は現在のサイズだけに適用する。汎用複数recipient、非同期worker、別receipt ledger、owner再割当て、独立rolloutが追加される場合は再度`$kiro-discovery`へ戻す

## Constraints

- チャネル・recipientの変更を確認済み内容の変更として扱う
- 操作ID、LINE retry key、監査記録を引き続き一貫させる
- 外部通信をDB transaction内で実行しない
- terminal状態を後続Webhookや再試行で上書きしない。明示的受取確認は別属性として記録する
- 既存の失敗分類、unknown状態、安全な概要、秘密情報非露出を維持する
