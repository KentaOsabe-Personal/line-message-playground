# LINE連携の詳細契約

LINE送信、LIFF／LINE Login、チャネル資格情報、Webhook、リッチメニューの実装・設計・画面・API契約・検証に適用します。core steeringの安全境界を具体化する文書です。

LINE ミニアプリの文章判定ラボは、以下の管理画面向け session 認証とは別の Bearer 認証境界です。専用 ID token の検証と Jev 判定の契約は [text-judgment-lab.md](text-judgment-lab.md) を参照します。

## LINE 配信

送信処理は Backend のサービス境界に閉じ込めます。プレビュー時に正規化済み内容を確認トークンへ結び付け、送信時に内容の一致を再検証します。トークンは不透明な値とし、本文や操作 ID を含めません。

操作 ID を LINE retry key と監査レコードに一貫して使用し、同じ操作は保存済み結果へ収束させます。外部通信はデータベース transaction の外で行い、処理中レコードの一意制約と条件付き更新で並行送信や結果の上書きを防ぎます。

配信状態は `processing`、`succeeded`、`failed`、`unknown` を区別します。タイムアウト等の結果不明時は自動再送せず、状態確認 API で既存操作を確認してから明示的な再試行を許可します。LINE SDK の生の例外や認証情報、固定宛先は公開 API や通常ログへ出さず、安全なエラー分類へ変換します。

push 送信は選択された登録済みチャネルのアクセストークンと、本人連携済み配信先の LINE user ID を実行時に解決します。固定設定の宛先やアクセストークンを参照する旧 runtime は使用しません。チャネルシークレットは push 送信では使用せず、Webhook 境界だけが署名検証のために参照します。利用上限確認は将来の運用機能として扱います。

確認トークンにはチャネル・配信先・本文の revision を結び付け、送信直前に live target と再照合します。配信成功後の受取確認は署名済み capability を postback action へ渡し、配信状態とは別の一回限りの transition として確定します。

## LINE アカウントとチャネル資格情報

LIFF から得た token は Backend の LINE Login 境界で検証し、provider と owner allowlist に一致した identity だけをサーバー側 session へ結び付けます。Frontend は session cookie を直接解釈せず、session API の安全な状態表現を使います。

複数 Messaging API チャネルの資格情報は DB へ暗号化して保存し、復号可能な値を repository 境界の外へ不必要に広げません。keyring の先頭を現用鍵とし、旧鍵を残した再暗号化、検証、撤去の順でローテーションします。鍵を失った DB は復号できないため、バックアップと旧鍵の保持期間を一体で判断します。

owner 向けチャネル管理では、資格情報を create／replace 入力だけの write-only 値とし、read model と API response は設定状態と更新日時だけを返します。read と mutation は active owner と同一 provider を transaction 内で fence し、mutation は `updatedAt` revision による optimistic concurrency を使います。削除時はチャネルを lock した後に配信・配信先・Webhook 等の参照を再確認し、参照中の削除を拒否します。

接続確認は access token と期待 bot user ID の同一 revision snapshotを使う read-only な一回の外部照会です。外部通信中は DB lock を保持せず、戻り時に revision が変化していれば結果を採用しません。token、secret、生の LINE 応答を永続化または公開せず、安全な状態分類だけを返します。

## Webhook

チャネル別の不透明な UUID から有効な資格情報を選び、生の request body に対する HMAC-SHA256 署名検証を JSON 解析より先に行います。署名後も `destination` と payload 上限を検証し、検証前後の失敗を安全な公開エラーへ縮約します。

`webhookEventId` はイベント台帳の一意キーとして重複を排除し、検証済みの immutable envelope だけを静的 handler registry へ渡します。受付は軽量な同期処理とし、未対応イベントも台帳へ明示的に記録します。将来重い処理が必要になった場合はレスポンス返却から分離します。

follow／unfollow handler は、active owner、provider、LINE subject、チャネルが完全一致する既存配信先だけを状態 projection の対象にします。未連携、不正、group／room source から identity や配信先を作成せず、安全な非更新結果として監査します。

友だち状態、最終イベントの順序 cursor、PII を含まない同期監査は、行ロックを使った同一 transaction で確定します。登録時刻を baseline とし、`(occurred_at_ms, webhookEventId の ASCII 順)` を比較して、遅延、重複、同時刻、同状態のイベントを到着順に依存しない単一状態へ収束させます。message／postback、reply、配信は別 handler の責任です。

message／postback handler は、完全一致の静的 command／action registry と既存の owner・provider・recipient 照合を通過した入力だけを処理します。現在の command は `/ping` から固定 `pong` 一件への reply に限定します。production の postback action registry は、Webhook の composition root が `delivery.received` を明示登録し、署名済み capability による受取確認を配信 app の handler へ委譲します。その他の action も明示登録されたものだけを扱い、未知、不正、未連携、group／room source は identity や recipient を作らず、外部作用のない結果として扱います。

Webhook request は View 入口から単一の monotonic deadline を共有し、handler を local と deadline-managed external の実行プロファイルへ分けます。LINE reply は同一チャネルの資格情報と一回限りの reply token を使い、自動再試行せず、期限不足なら開始しません。accepted、rejected、unknown を区別し、受信内容、token、LINE user ID、access token を保存しない interaction 監査へ収束させます。

## LINE リッチメニュー資源

リッチメニュー画像は、版付きテンプレート、固定 geometry、同梱日本語フォントから決定的に生成します。入力文字の glyph、寸法、比率、形式、1 MB 上限を LINE への外部作用前に検証し、画像内容は encoder の偶然に依存しない canonical pixel digest で結び付けます。フォントの版、ライセンス、digest と画像生成依存は repository に固定し、起動時とテストで差し替えや欠落を検出します。

LINE の rich-menu mutation には retry key がないため、タイムアウト、5xx、429、解釈不能な応答を自動再試行しません。operation、管理資源、段階遷移、ownership marker を永続化し、list／get／default／画像 download の保守的な観測から既存操作へ収束させます。`unknown` や `cleanup_required` は成功・失敗へ推測せず、明示的な recheck または cleanup まで新規変更を禁止します。保存済み ID または強い所有権証明がない外部資源は削除しません。

外部通信中はデータベース lock を保持せず、戻り時に owner、provider、チャネル revision、operation stage を再検証します。段階導入は `read_only`、`recovery_only`、`enabled` を区別し、下流の reference probe、履歴 purge、承認済み統合 marker が揃わない限り mutation を fail-closed で拒否します。

チャネル無効化は、チャネルごとに一意な intent と operation ID を永続化し、LINE 上の実状態確認、必要な適用解除、明示的な再確認、完了へ段階的に収束させます。同じ操作の再実行は保存済み状態を返し、pending 中は同じ無効化に承認された回復・後片付け以外のチャネル更新とリッチメニュー変更を transaction 内の fence で拒否します。

無効化前の評価と解除はリッチメニュー app の headless typed port を介し、所有権を証明できるチャネル既定資源だけを対象にします。外部既定、結果不明、後片付け待ち、revision 競合ではチャネルを無効化せず、確認待ちとして実状態の再取得を要求します。再有効化も owner、provider、revision を再検証し、必要な場合は資格情報ペアの修復と同じ操作で行います。mutation の有効化には reference probe、履歴 purge、無効化ライフサイクル、統合 marker の全条件を要求します。

_移行日: 2026-09-27。tech.mdの既存契約を内容を変えず条件付き参照へ移動。_

_更新日: 2026-10-05。受取確認 action の production 登録を現行 composition root へ同期し、Mini App ラボの独立した認証契約への参照を追加。_
