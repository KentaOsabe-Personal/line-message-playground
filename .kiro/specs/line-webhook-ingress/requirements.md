# 要求仕様書

## はじめに

LINE Message Playground に、複数の LINE 公式アカウントから届く Webhook をチャネルごとに真正性確認し、検証済みイベントを一度だけ受け付ける共通境界を追加する。公開識別子や署名検証前の本文を認証根拠にせず、未加工本文の署名と `destination` を照合した後だけイベントを信頼する。空イベントによる疎通確認、再送、重複、未知イベント、後続処理の失敗を安全に分類し、後続仕様が署名検証を繰り返さず利用できる検証済みイベントを提供する。

## 境界コンテキスト

- **対象範囲**: 有効チャネルごとの専用 Webhook URL、未加工本文の署名検証、`destination` 照合、payload 基本検証、空イベント疎通、`webhookEventId` 単位の一意な受付、再送と重複の識別、検証済みイベント envelope、同期 handler の処理分類、安全な受付監査、速やかな HTTP 応答
- **対象外**: follow／unfollow による recipient 更新、message／postback の意味解釈、reply 送信、画像・動画本文の恒久保存、group／room 管理、Beacon、queue／worker、自動再実行、配信到達・既読保証
- **隣接する前提**: `line-channel-foundation` は不透明な公開識別子、bot user ID、および有効チャネルの Webhook 検証用シークレット取得を提供する。後続の `line-friendship-sync` と `line-webhook-command-dispatch` は本仕様の検証済みイベント envelope を利用し、署名を再検証しない。後続 handler の業務状態更新と外部作用は各後続仕様が所有する。

## 要求事項

### Requirement 1: チャネル別の公開受付

**目的:** 個人開発者として、各 LINE 公式アカウントに専用の Webhook URL を設定したい。これにより、複数チャネルのイベントを混同せず受け付けられる。

#### 受入基準

1. When 有効チャネルの Webhook を設定する, the LINE Webhook 受付 shall `POST /api/line/webhooks/{channel_public_key}/` でそのチャネル専用の受付先を提供する
2. The LINE Webhook 受付 shall URL の公開識別子を署名検証候補となるチャネルの選択だけに使用し、真正性の根拠として扱わない
3. If 公開識別子が不正、未知、または無効チャネルを示す, the LINE Webhook 受付 shall イベントを受け付けず、後続 handler を呼び出さない
4. If 対象チャネルの Webhook 検証用資格情報を安全に取得できない, the LINE Webhook 受付 shall イベントを受け付けず、資格情報やチャネル状態を明かさない安全なエラーを返す
5. If 専用 Webhook URL が POST 以外の HTTP メソッドで要求される, the LINE Webhook 受付 shall Webhook として処理せず、イベントを受け付けない

### Requirement 2: 未加工本文による真正性確認

**目的:** 個人開発者として、LINE から送られたと確認できる要求だけを信頼したい。これにより、第三者が偽造したイベントの処理を防げる。

#### 受入基準

1. When Webhook 要求を受信する, the LINE Webhook 受付 shall JSON 解析、文字列変換、または本文の正規化より先に、受信した未加工本文と署名ヘッダーを対象チャネルのシークレットで検証する
2. If 署名ヘッダーが欠落、不正な形式、または未加工本文と不一致である, the LINE Webhook 受付 shall 要求全体を拒否し、イベントを受け付けず、後続 handler を呼び出さない
3. The LINE Webhook 受付 shall 送信元 IP、公開識別子、または署名検証前の JSON 内容を署名検証の代替として使用しない
4. When 未加工本文の署名検証に成功する, the LINE Webhook 受付 shall 本文を解析し、`destination` が選択したチャネルの bot user ID と一致することを確認する
5. If `destination` が欠落、不正な形式、または選択したチャネルの bot user ID と不一致である, the LINE Webhook 受付 shall 要求全体を拒否し、いずれのイベントも受け付けない

### Requirement 3: Payload 基本検証と将来互換性

**目的:** 後続機能の開発者として、最低限の共通契約を満たすイベントだけを利用したい。これにより、不正な構造を拒否しながら LINE の互換性のある拡張を受け入れられる。

#### 受入基準

1. When 署名と `destination` の検証に成功する, the LINE Webhook 受付 shall 本文が JSON オブジェクトであり、`events` が配列であることを確認する
2. When `events` にイベントが含まれる, the LINE Webhook 受付 shall 各イベントに有効な `webhookEventId`、イベント種別、イベント発生時刻、および再送表示が存在することを確認する
3. If JSON、トップレベル構造、またはいずれか一件のイベントが必須の基本契約を満たさない, the LINE Webhook 受付 shall 要求全体を拒否し、その要求内の正常なイベントも含めて一件も受け付けない
4. When 署名と `destination` が有効で `events` が空である, the LINE Webhook 受付 shall LINE Developers コンソールからの疎通確認として受け付け、イベント受付記録を作らず、後続 handler を呼び出さない
5. When 受付上限内で必須の基本契約を満たす payload に未知のフィールド、フィールド順序、列挙値、またはイベント種別が含まれる, the LINE Webhook 受付 shall 既知イベントの受付を妨げず、未知イベントを未対応として安全に分類する
6. When 一つの有効な要求に複数のイベントが含まれる, the LINE Webhook 受付 shall 各イベントを `webhookEventId` 単位で受付、重複、および処理分類の対象にする
7. If 署名検証に成功した未加工本文が 256 KiB を超える、または `events` に 10 件を超えるイベントが含まれる, the LINE Webhook 受付 shall 要求全体を拒否し、イベント受付記録を作らず、後続 handler を呼び出さない

### Requirement 4: イベントの一意な受付と再送処理

**目的:** 個人開発者として、同じ Webhook イベントが複数回届いても後続処理を重複実行したくない。これにより、LINE の再送や並行到着を安全に扱える。

#### 受入基準

1. When 検証済みの `webhookEventId` を初めて受信する, the LINE Webhook 受付 shall そのイベントを一意に受け付け、受付時刻と初期処理分類を記録する
2. If 同一要求内、別要求、または並行要求から同じ `webhookEventId` を複数回受信する, the LINE Webhook 受付 shall 一件だけを新規受付として確定し、残りを既存イベントの重複へ収束させる
3. When 既に受け付けた `webhookEventId` を再び受信する, the LINE Webhook 受付 shall 後続 handler を再実行せず、重複受付として処理する
4. The LINE Webhook 受付 shall `deliveryContext.isRedelivery` を監査情報として保持し、重複判定は `webhookEventId` に基づいて行う
5. When 再送イベントを受信する, the LINE Webhook 受付 shall 受信順序でイベント発生時刻を書き換えず、元のイベント発生時刻を維持する
6. The LINE Webhook 受付 shall LINE の再送を完全な配信保証または失敗した handler の再実行保証として扱わない

### Requirement 5: 検証済みイベント envelope

**目的:** 後続機能の開発者として、真正性とチャネル対応を確認済みのイベントを共通形式で受け取りたい。これにより、各機能が署名検証を重複実装せず、イベント固有の処理へ集中できる。

#### 受入基準

1. When 基本契約を満たす既知イベントを初めて受け付ける, the LINE Webhook 受付 shall 検証済みチャネル識別情報、`webhookEventId`、イベント種別、イベント発生時刻、再送表示、および検証済みイベントデータを含む envelope を後続 handler へ渡す
2. The LINE Webhook 受付 shall 署名、`destination`、および payload 基本契約の検証をすべて完了したイベントからだけ検証済み envelope を生成する
3. The LINE Webhook 受付 shall 検証済み envelope に未加工の要求本文、署名、チャネルシークレット、または検証前のデータを含めない
4. The LINE Webhook 受付 shall 後続機能が署名を再検証せずに検証済みイベントであることを識別できる契約を提供する
5. If イベント種別が未知または未対応である, the LINE Webhook 受付 shall 種別固有の後続 handler を呼び出さず、イベントを未対応として記録する

### Requirement 6: 後続処理の結果分類

**目的:** 個人開発者として、受け付けたイベントが後続処理で成功したか失敗したかを安全に把握したい。これにより、内容を露出せず障害を調査できる。

#### 受入基準

1. When 後続 handler が正常に完了する, the LINE Webhook 受付 shall イベントを処理済みとして記録する
2. If 後続 handler が失敗する, the LINE Webhook 受付 shall イベントを失敗として記録し、下位層の生例外を安全な失敗分類へ置き換える
3. If 失敗として記録したイベントを重複受信する, the LINE Webhook 受付 shall 後続 handler を自動再実行せず、保存済みの失敗分類を維持する
4. The LINE Webhook 受付 shall follow／unfollow による状態更新、message／postback の意味解釈、reply 送信、またはその他のイベント種別固有の外部作用を実行しない

### Requirement 7: データ最小化と安全な監査

**目的:** 個人開発者として、Webhook の受付と障害を追跡しつつ、秘密情報や不要な LINE ユーザーデータを残したくない。これにより、安全なローカル学習環境を維持できる。

#### 受入基準

1. When イベントを受け付ける, the LINE Webhook 受付 shall 非秘密のチャネル識別情報、`webhookEventId`、イベント種別、イベント発生時刻、再送表示、受付時刻、および受付・処理分類だけを監査可能な情報として保持する
2. When 要求を拒否する、空イベントを受け付ける、重複を検出する、または未知イベントを分類する, the LINE Webhook 受付 shall 内容を露出せず、結果分類と時刻を運用者が区別できるようにする
3. The LINE Webhook 受付 shall 未加工の要求本文、署名、チャネルシークレット、LINE ユーザー ID、message／postback の内容、reply token、または下位層の生例外を通常ログもしくは永続的な受付監査へ含めない
4. If 公開応答または通常ログへエラーを出力する, the LINE Webhook 受付 shall 秘密値、受信内容、チャネルの内部状態、および下位層の生例外を含まない安全な分類を使用する
5. The LINE Webhook 受付 shall 後続仕様が明示的な目的、保持期間、および削除方法を定めない限り、イベント種別固有のユーザーデータを恒久保存しない

### Requirement 8: LINE への応答契約

**目的:** 個人開発者として、LINE に受付結果を速やかに返し、不要な再送を抑えたい。これにより、タイムアウト後の重複にも耐えながら疎通状態を確認できる。

#### 受入基準

1. When 署名、`destination`、256 KiB 以下の未加工本文、10 件以下の `events`、および payload 基本契約が有効な要求を受信する, the LINE Webhook 受付 shall 受付と安全な処理分類を完了し、要求受信から 2 秒以内に HTTP 200 を返す
2. When 有効な空イベント、重複イベント、または未対応イベントを処理する, the LINE Webhook 受付 shall 成功した受付結果として HTTP 200 を返す
3. If 検証後に後続 handler が失敗する, the LINE Webhook 受付 shall 失敗分類を記録したうえで HTTP 200 を返し、LINE の再送による自動再実行を要求しない
4. If チャネル選択、資格情報取得、署名、`destination`、受付上限、または payload 基本契約の検証に失敗する, the LINE Webhook 受付 shall HTTP 2xx 以外の安全な応答を返し、イベントを一件も受け付けない
5. When タイムアウトその他の理由で同じイベントが再送される, the LINE Webhook 受付 shall 保存済みの受付結果へ収束させ、後続 handler を重複実行しない
