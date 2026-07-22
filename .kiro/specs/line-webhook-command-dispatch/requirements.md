# 要件定義書

## はじめに

本仕様を実装すると、連携済みの利用者は LINE トークへ固定の疎通確認コマンドを送り、Bot から固定メッセージを受け取れるようになる。これにより、チャネル設定、Webhook 受信、利用者との連携確認、LINE reply API までの経路が正しく動作することを実機で確認できる。

また、LINE メッセージ内のボタン操作を、事前登録された postback action へ安全に渡す共通経路を提供する。後続の `linked-recipient-delivery` などは、この経路へ固有の処理だけを追加することで、「受け取りました」ボタンによる受取確認などを実現できる。

本仕様は汎用チャットBotを作るものではない。固定コマンドと登録済みボタン操作以外は何も実行せず、reply の二重送信、未連携利用者による操作、受信データからの任意処理を防ぐ。

## このSpecが実現可能にすること

| 実現可能になること | 実装後の確認方法 | そのための要件 |
| --- | --- | --- |
| LINE トークから reply までの疎通確認 | 連携済み利用者が固定コマンドを送ると、同じ公式アカウントから固定メッセージが一度だけ返る | Requirement 1、3、5、7 |
| LINE 上のボタン操作を後続機能へ渡す | 登録済みボタンを押すと対応する action handler が一度だけ呼ばれ、未登録ボタンでは何も起きない | Requirement 2、3、4、7 |
| 不正入力や外部障害を安全に扱う | 未連携利用者、group／room、未知コマンド、不正データでは外部作用がなく、reply の結果不明時にも自動再送されない | Requirement 3〜7 |

## 境界コンテキスト

- **このSpec単体で利用者が確認できる成果**: 連携済み利用者が固定の疎通確認コマンドを送り、同じチャネルの Bot から固定 reply を一度だけ受け取る
- **後続Specが追加できる成果**: 検証済み postback を登録済み action handler へ渡し、受取確認などのボタン固有処理を実行する
- **対象範囲**: 検証済み text message／postback、連携済み利用者の照合、固定 command／action の許可リスト、チャネル別 reply、一回限りの reply token、安全な結果分類と監査、後続 handler の登録契約
- **対象外**: Webhook の署名検証と重複受付、利用者や recipient の新規登録、follow／unfollow による友だち状態同期、汎用自然言語Bot、画像・動画・音声処理、group／room、任意コード・SQL・URL・module の実行、push 配信、配信固有 token の検証と配信記録更新、reply の自動再試行、queue／worker
- **隣接する前提**: `line-webhook-ingress` は真正性を確認したイベントを `webhookEventId` ごとに一度だけ渡す。`line-channel-foundation` は同じチャネルから reply するための資格情報を提供する。`line-account-linking` はイベント送信者が既存の連携済み利用者であるかを確認できる情報を提供する。

## 要求事項

### Requirement 1: LINEトークからreplyまでの疎通確認

**実現可能にすること:** 個人開発者が LINE トークから固定コマンドを送り、Bot の固定 reply を受け取ることで、Webhook 受信から LINE への応答までの経路を実機で確認できるようにする。

#### 受入基準

1. When 連携済み利用者から初期の固定疎通確認コマンドを受け取る, the LINE Webhook コマンドディスパッチ shall 定義済みの固定 text message 一件だけを同じイベントへの reply として送る
2. When 固定 reply を送る, the LINE Webhook コマンドディスパッチ shall イベントを受け付けた同じ有効チャネルのアクセストークンと、そのイベントの reply token だけを使用する
3. When LINE reply API が固定 reply を受け付ける, the LINE Webhook コマンドディスパッチ shall 疎通確認を reply 受付済みとして記録する
4. The LINE Webhook コマンドディスパッチ shall 固定 reply に受信 text、LINE ユーザーID、reply token、チャネル資格情報、または内部エラーの詳細を含めない
5. The LINE Webhook コマンドディスパッチ shall 疎通確認への応答に push、multicast、broadcast、または narrowcast を使用しない
6. If text が固定疎通確認コマンドと完全一致しない, the LINE Webhook コマンドディスパッチ shall 応答内容を推測せず reply を送らない
7. If 対象チャネルの送信用資格情報を安全に取得できない, the LINE Webhook コマンドディスパッチ shall 別チャネルまたは固定環境の資格情報へフォールバックせず reply を開始しない

### Requirement 2: ボタン操作を登録済みpostback actionへ渡す

**実現可能にすること:** 後続機能が、LINE メッセージ内のボタン操作を受取確認などの固有処理へ安全に接続できるようにする。後続機能は Webhook 受付や利用者照合を再実装せず、action 固有の判定と状態変更へ集中できる。

#### 受入基準

1. When 連携済み利用者から登録済み action 名を含む postback を受け取る, the LINE Webhook コマンドディスパッチ shall 対応する action handler を一度だけ呼び出す
2. When 登録済み action handler を呼び出す, the LINE Webhook コマンドディスパッチ shall action 名、action 固有の不透明な payload、検証済みチャネル、`webhookEventId`、および検証済み利用者コンテキストを渡す
3. When action handler が成功、正常な非更新、拒否、または失敗を返す, the LINE Webhook コマンドディスパッチ shall その結果を区別できる安全な処理結果へ変換する
4. If postback の action 名が登録されていない, the LINE Webhook コマンドディスパッチ shall handler、reply、または他の外部作用を実行せず正常に終了する
5. If action handler が失敗または結果を返せない, the LINE Webhook コマンドディスパッチ shall 別の handler を呼び出さず、同じ action を自動再実行しない
6. If 同じ action 名に複数の handler が登録されようとする, the LINE Webhook コマンドディスパッチ shall 曖昧な振り分けを許可せず運用開始前に安全な設定エラーとして扱う
7. The LINE Webhook コマンドディスパッチ shall action 固有 payload の真正性、有効期限、冪等性、および業務状態変更を推測せず、登録した後続 handler の判定へ委ねる
8. The LINE Webhook コマンドディスパッチ shall `linked-recipient-delivery` が所有する配信固有 token の検証、受取確認の冪等性、および配信記録の更新を実行しない
9. When 新しい action handler が追加される, the LINE Webhook コマンドディスパッチ shall 既存の固定疎通確認、未知入力の正常終了、および reply token 一回利用の契約を変更しない

### Requirement 3: 操作できる利用者とイベントの限定

**実現可能にすること:** 疎通確認とボタン操作を、真正性確認済みかつ既存の連携関係に属する利用者だけへ提供する。未連携利用者や group／room から identity や recipient を作らない。

#### 受入基準

1. When `line-webhook-ingress` から検証済みの `message` または `postback` イベントを受け取る, the LINE Webhook コマンドディスパッチ shall イベント種別、source、reply token、および種別固有データを対象可否の判定に使用する
2. When 検証済みイベントが有効な user source と LINE ユーザーIDを含む, the LINE Webhook コマンドディスパッチ shall active owner、同一 provider の LINE identity、およびイベントのチャネルに対応する既存 recipient を完全一致で照合する
3. If user source、LINE ユーザーID、または reply token が欠落、不正、もしくは解釈不能である, the LINE Webhook コマンドディスパッチ shall command、action、および reply を実行せず不正イベントとして分類する
4. If イベントの source が group または room である, the LINE Webhook コマンドディスパッチ shall command、action、および reply を実行せず対象外 source として分類する
5. If 一致する active owner、LINE identity、または対象チャネルの recipient が存在しない, the LINE Webhook コマンドディスパッチ shall identity、owner、recipient を作成せず、command、action、および reply を実行しない
6. The LINE Webhook コマンドディスパッチ shall `message` と `postback` 以外のイベント種別を command または action の入力として扱わない
7. Where 必須のイベント情報が有効で未知の追加フィールドだけが含まれる, the LINE Webhook コマンドディスパッチ shall 未知フィールドを意味解釈せず対象イベントの処理を継続する

### Requirement 4: 登録済み操作だけを実行する安全な振り分け

**実現可能にすること:** 固定コマンドやボタンの追加を、完全一致する有限の許可リストとして管理できるようにする。通常の会話、入力ミス、過大または不正なデータから処理を推測しない。

#### 受入基準

1. The LINE Webhook コマンドディスパッチ shall text command と postback action を、それぞれ事前に定義された有限の許可リストだけから解決する
2. When `message` イベントの message type が `text` で、text が1以上5,000以下の UTF-16 code unit からなる文字列である, the LINE Webhook コマンドディスパッチ shall その text を command 候補として扱う
3. When `postback` イベントの data が1以上300以下の UTF-16 code unit からなる文字列である, the LINE Webhook コマンドディスパッチ shall その data から action 候補を判定する
4. If text または postback data が欠落、文字列以外、空、もしくは各上限を超える, the LINE Webhook コマンドディスパッチ shall command、action、および reply を実行せず不正イベントとして分類する
5. When command または action 候補を許可リストと比較する, the LINE Webhook コマンドディスパッチ shall 大文字小文字、前後空白、Unicode 表現、または部分一致を補正せず定義済み値との完全一致だけを採用する
6. If command 候補または action 候補が許可リストのいずれとも完全一致しない, the LINE Webhook コマンドディスパッチ shall handler と reply を呼び出さず未知入力として正常に終了する
7. The LINE Webhook コマンドディスパッチ shall text message を postback action として解釈せず、postback data を text command として解釈しない
8. The LINE Webhook コマンドディスパッチ shall 受信 text または postback data をコード、SQL、URL、ファイルパス、module 名、もしくは動的な呼び出し先として直接実行しない
9. The LINE Webhook コマンドディスパッチ shall 一つのイベントから複数の command または action handler を連鎖的に実行しない

### Requirement 5: replyの二重送信防止と結果の明確化

**実現可能にすること:** 一回限りの reply token を安全に使用し、成功、明示的失敗、結果不明を区別できるようにする。タイムアウトや再送が発生しても、同じ reply を自動的に二重送信しない。

#### 受入基準

1. When 一つのイベントに対する reply を開始する, the LINE Webhook コマンドディスパッチ shall その reply token を一回の LINE reply API 要求にだけ使用する
2. The LINE Webhook コマンドディスパッチ shall 一つのイベントに対して最大一件の text message だけを reply する
3. When LINE reply API が成功応答を返す, the LINE Webhook コマンドディスパッチ shall reply を `accepted` として分類する
4. If LINE reply API が明示的な失敗応答を返す, the LINE Webhook コマンドディスパッチ shall reply を `rejected` として分類し、同じ token を再利用しない
5. If reply 要求がタイムアウト、通信中断、または応答解釈不能により結果を確定できない, the LINE Webhook コマンドディスパッチ shall reply を `unknown` として分類し、成功または失敗を推測せず同じ token を再利用しない
6. If reply の開始前または開始後に処理、監査、もしくは受付結果の確定が失敗する, the LINE Webhook コマンドディスパッチ shall 同じ reply token による自動再試行を行わない
7. If 同じ `webhookEventId` が再送または並行到着で再び受け付けられる, the LINE Webhook コマンドディスパッチ shall `line-webhook-ingress` が保持する初回処理結果へ収束し、reply または handler を再実行しない
8. The LINE Webhook コマンドディスパッチ shall `accepted` を LINE API による受付として扱い、端末到達、表示、または既読の保証として扱わない

### Requirement 6: 処理結果を安全に確認できる監査

**実現可能にすること:** 個人開発者が、疎通確認やボタン操作が処理されたか、無視されたか、失敗したかを、受信本文や秘密情報を残さず確認できるようにする。

#### 受入基準

1. When interaction の処理を完了する, the LINE Webhook コマンドディスパッチ shall command または action の処理済み、正常な非更新、未知、不正、対象外、未連携、handler 失敗、および reply の `accepted`、`rejected`、`unknown`、未実行を後から区別できるようにする
2. When interaction または reply の結果を監査する, the LINE Webhook コマンドディスパッチ shall チャネルの不透明な識別情報、`webhookEventId`、イベント種別、許可済み command／action の非秘密識別子、および安全な結果分類だけを関連付ける
3. The LINE Webhook コマンドディスパッチ shall 受信 text、postback data、reply token、LINE ユーザーID、アクセストークン、Authorization header、または外部APIの生レスポンスを通常ログ、永続的な監査、公開応答、もしくは安全な結果表現へ含めない
4. If 外部API、登録済み handler、資格情報取得、または監査処理から例外を受け取る, the LINE Webhook コマンドディスパッチ shall 生例外を保持または公開せず安全な失敗分類へ置き換える
5. When 未知、不正、対象外、または未連携のイベントを外部作用なしで処理できる, the LINE Webhook コマンドディスパッチ shall 処理障害と区別できる正常な no-op 結果を `line-webhook-ingress` へ返す
6. If command、action handler、資格情報取得、reply、または安全な結果確定に失敗する, the LINE Webhook コマンドディスパッチ shall 安全な失敗結果を `line-webhook-ingress` へ返し、Webhook の公開 HTTP 応答判定は同受付機能へ委ねる
7. The LINE Webhook コマンドディスパッチ shall 受信 text、postback data、reply token、または LINE ユーザーIDを恒久保存しない

### Requirement 7: Webhook受付を遅延させない同期処理

**実現可能にすること:** reply や action 処理を追加しても LINE の Webhook 受付を速やかに完了し、受付遅延による不要な再送と重複処理を抑えられるようにする。

#### 受入基準

1. While 一つの有効な Webhook 要求に最大10イベントが含まれる, the LINE Webhook コマンドディスパッチ shall `line-webhook-ingress` が要求受信から2秒以内に HTTP 200を返す契約を維持する
2. When reply を開始する, the LINE Webhook コマンドディスパッチ shall Webhook 全体の2秒期限を超えて待機しない有限の時間内で reply を `accepted`、`rejected`、または `unknown` のいずれかへ分類する
3. If Webhook 全体の残り時間では reply を安全に開始できない, the LINE Webhook コマンドディスパッチ shall 外部要求を開始せず期限超過として分類し、同じ reply token を後から使用しない
4. The LINE Webhook コマンドディスパッチ shall 一つのイベントから LINE への外部要求を最大一回に制限する
5. The LINE Webhook コマンドディスパッチ shall Webhook の HTTP 応答後に queue、worker、または遅延処理で同じ command、action、もしくは reply を自動実行しない
