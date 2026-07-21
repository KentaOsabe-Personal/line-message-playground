# 要件定義書

## はじめに

本仕様は、検証済みのLINE Webhook `follow`／`unfollow`イベントを、同一プロバイダー・同一Messaging APIチャネルに属する既存のチャネル別recipientへ反映し、ownerが確認する友だち状態を時系列どおりに保つ。Webhookの再送、遅延、順序逆転、同時処理、および連携解除との競合があっても、より新しい状態と利用者自身の有効／無効設定を保護する。

## 境界コンテキスト

- **対象範囲**: 検証済みuser sourceの`follow`／`unfollow`、既存LINE identityとチャネル別recipientの照合、`friend`／`not_friend`への遷移、登録・再登録時点を含むイベント順序制御、同時更新、連携解除との競合、未連携・対象外・不正イベントの安全な分類、状態更新結果の監査
- **対象外**: LINE identity、owner、recipientの新規登録、表示名取得、利用者が設定するrecipientの有効／無効変更、message／postback／reply、group／roomの状態管理、配信結果・端末到達・既読状態の変更、失敗イベントの自動再実行、外部キューまたはワーカーによる復旧
- **隣接する前提**: `line-webhook-ingress`がチャネル、署名、destination、共通イベント属性、重複受付を検証し、検証済みイベントを一度だけ本機能へ渡す。`line-account-linking`がLINE identityとチャネル別recipientの登録、利用者による無効化・再有効化・連携解除を所有する。後続の`linked-recipient-delivery`と`line-channel-admin-ui`は、本機能が更新した友だち状態を配信可否判定と状態表示に利用する。

## 要求事項

### Requirement 1: 対象イベントの判定

**目的:** ownerとして、信頼済みかつ本機能の対象となる友だちイベントだけを状態更新へ使用したい。これにより、不正または無関係なイベントで配信先状態が変わることを防げる。

#### 受入基準

1. When `line-webhook-ingress`から検証済みの`follow`または`unfollow`イベントを受け取る, the 友だち状態同期機能 shall イベント固有のsource情報を検査して対象可否を判定する
2. When 検証済みイベントが有効なuser sourceとLINEユーザーIDを含む, the 友だち状態同期機能 shall 既存recipientとの照合へ進む
3. If `follow`または`unfollow`イベントのuser sourceまたはLINEユーザーIDが欠落、不正、もしくは解釈不能である, the 友だち状態同期機能 shall recipientを変更せず不正イベントとして分類する
4. If `follow.isUnblocked`が存在するが真偽値ではない, the 友だち状態同期機能 shall recipientを変更せず不正イベントとして分類する
5. If 検証済みイベントのsourceがgroupまたはroomである, the 友だち状態同期機能 shall recipientを変更せず対象外sourceとして分類する
6. Where 必須のイベント情報が有効で未知の追加フィールドだけが含まれる, the 友だち状態同期機能 shall 未知フィールドを友だち状態の根拠にせず対象イベントの処理を継続する
7. The 友だち状態同期機能 shall `follow`および`unfollow`以外のイベント種別を友だち状態の根拠として扱わない

### Requirement 2: 既存identityとrecipientの限定照合

**目的:** ownerとして、イベントが属するプロバイダーとチャネルに対応する既存recipientだけを更新したい。これにより、未連携userの自動登録と異なる関係への誤反映を防げる。

#### 受入基準

1. When 対象となるuser sourceイベントを照合する, the 友だち状態同期機能 shall イベントのチャネルが属するプロバイダーとLINEユーザーIDの組み合わせに一致する既存LINE identityだけを候補とする
2. When 一致するLINE identityが存在する, the 友だち状態同期機能 shall そのidentityとイベントのチャネルの組み合わせに一致する既存recipientだけを更新候補とする
3. If 同じLINEユーザーIDを持つidentityが異なるプロバイダーに存在する, the 友だち状態同期機能 shall そのidentityまたはrecipientへイベントを反映しない
4. If 一致するLINE identityが存在しない, the 友だち状態同期機能 shall LINE identity、owner、またはrecipientを作成せず未連携イベントとして分類する
5. If 一致するLINE identityは存在するが対象チャネルのrecipientが存在しない, the 友だち状態同期機能 shall recipientを作成せず未連携イベントとして分類する
6. If イベントのチャネルまたはそのプロバイダーを安全に特定できない, the 友だち状態同期機能 shall いずれのrecipientも変更せず照合不能として分類する
7. When 一致するrecipientを特定する, the 友だち状態同期機能 shall 同じidentityの他チャネルおよび同じチャネルの他identityに属するrecipientを変更しない

### Requirement 3: follow／unfollowによる友だち状態遷移

**目的:** ownerとして、LINE上の現在の友だち関係をチャネル別recipientの状態として確認したい。これにより、後続機能が配信対象を安全に判断できる。

#### 受入基準

1. When 適用対象となる`follow`イベントを受け取る, the 友だち状態同期機能 shall 一致するrecipientの友だち状態を`friend`にする
2. When 適用対象となる`unfollow`イベントを受け取る, the 友だち状態同期機能 shall 一致するrecipientの友だち状態を`not_friend`にする
3. When `isUnblocked`がtrueの`follow`イベントを受け取る, the 友だち状態同期機能 shall 現在の遷移をブロック解除による`friend`として識別できるようにする
4. The 友だち状態同期機能 shall `follow.isUnblocked`を現在のイベントの補助情報としてのみ扱い、完全なブロック・解除履歴を推定または生成しない
5. When `unknown`、`friend`、または`not_friend`のrecipientへ適用対象イベントを反映する, the 友だち状態同期機能 shall イベント種別が示す現在状態へ収束させる
6. When 現在と同じ友だち状態を示すより新しいイベントを受け取る, the 友だち状態同期機能 shall 友だち状態を維持しつつ、そのイベントより古い反対状態のイベントによる上書きを防ぐ

### Requirement 4: 登録境界とイベント順序の決定

**目的:** ownerとして、再送、遅延、順序逆転、または同時処理があっても最も新しい有効イベントに対応する状態を確認したい。これにより、受信順に依存した状態の巻き戻りを防げる。

#### 受入基準

1. When recipientが登録または再登録される, the 友だち状態同期機能 shall その登録時点を新しい関係へ適用できるイベントの基準時刻とする
2. If イベントの発生時刻が現在のrecipientの登録または再登録の基準時刻以前である, the 友だち状態同期機能 shall そのイベントを古い関係に属するものとして無視し現在状態を変更しない
3. When 登録基準時刻より後で、かつ最後に適用したイベントより新しい発生時刻のイベントを受け取る, the 友だち状態同期機能 shall そのイベントを現在状態へ反映する
4. If イベントの発生時刻が最後に適用したイベントより古い, the 友だち状態同期機能 shall staleイベントとして無視し現在状態を変更しない
5. When 異なるイベントが同じ発生時刻を持つ, the 友だち状態同期機能 shall `webhookEventId`の辞書順が大きいイベントを新しいイベントとして扱う
6. If 同じ`webhookEventId`のイベントが複数回処理対象として提示される, the 友だち状態同期機能 shall 追加の状態変更を行わず同じ最終状態へ収束させる
7. While 複数の対象イベントが同時に処理される, the 友だち状態同期機能 shall 発生時刻と`webhookEventId`で決まる最も新しい適用可能イベントに対応する単一の最終状態へ収束させる
8. The 友だち状態同期機能 shall イベントの到着順および再送表示をイベントの新旧判定に使用しない

### Requirement 5: 利用者設定および連携解除との独立性

**目的:** ownerとして、LINE上の友だち状態と自分が設定した配信先の有効状態を独立して管理したい。これにより、Webhookが利用者の設定や解除済み関係を復元することを防げる。

#### 受入基準

1. While 一致するrecipientが利用者によって無効化されている, when 適用対象イベントを受け取る, the 友だち状態同期機能 shall 友だち状態だけを更新し利用者の無効設定を維持する
2. When 友だち状態を更新する, the 友だち状態同期機能 shall recipientの利用者設定、チャネルの有効状態、配信結果、端末到達状態、および既読状態を変更しない
3. If チャネル別recipientの連携解除とイベント処理が競合する, the 友だち状態同期機能 shall 解除済みrecipientを再作成せず最終的に解除された状態を維持する
4. If ownerの全連携解除とイベント処理が競合する, the 友だち状態同期機能 shall 削除済みLINE identity、owner、recipient、またはsessionを復元しない
5. When 解除済みのidentityとチャネルの関係が後から再登録される, the 友だち状態同期機能 shall 再登録の基準時刻以前に発生したイベントを新しいrecipientへ適用しない
6. The 友だち状態同期機能 shall 友だち状態の更新を理由としてLINEへのreplyまたはメッセージ配信を開始しない

### Requirement 6: 安全な監査、失敗処理、および同期実行

**目的:** 個人開発者として、友だち状態イベントがどのように処理されたかを個人情報を露出させず追跡したい。これにより、同期漏れや対象外イベントを安全に診断できる。

#### 受入基準

1. When 対象イベントの処理を完了する, the 友だち状態同期機能 shall 適用、状態維持、staleまたは重複、未連携、対象外、不正、および失敗を後から区別できる安全な監査結果として保持する
2. When 状態更新結果を監査する, the 友だち状態同期機能 shall チャネルの不透明な識別情報、`webhookEventId`、イベント種別、発生時刻、および安全な結果分類を関連付ける
3. The 友だち状態同期機能 shall LINEユーザーIDを画面、公開API、通常ログ、監査結果、またはエラー詳細へ含めない
4. If 状態更新または監査結果の確定に失敗する, the 友だち状態同期機能 shall 友だち状態と順序情報の一部だけを確定せず、失敗結果を`line-webhook-ingress`へ返す
5. When 未連携、対象外、stale、重複、または現在状態を維持するイベントを安全に処理できる, the 友だち状態同期機能 shall 処理失敗と区別できる正常な非更新結果を`line-webhook-ingress`へ返す
6. The 友だち状態同期機能 shall 友だち状態同期のためにLINEまたは他の外部サービスへ問い合わせない
7. The 友だち状態同期機能 shall 標準Backend実行環境において1イベントの処理を100ミリ秒以内に完了する
8. While 1つの有効なWebhook要求に最大10イベントが含まれる, the 友だち状態同期機能 shall `line-webhook-ingress`の2秒以内の受付応答契約を維持する
