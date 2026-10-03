# Backendの指示

`backend/` の設計・実装・レビューに適用します。共通の安全規則と実行コマンドはrootのAGENTSとtech steeringに従います。

## プロジェクト設定

**場所**: `/backend/config/`  
**目的**: Django settings、ルート URL、ASGI/WSGI などプロジェクト全体の構成

機能実装を `config` へ置かず、Django app に分離します。

## ドメインapp

**場所**: `/backend/<app>/`  
**目的**: 1つの機能領域に属する View、URL、Model、テスト等

各 app は app-local な URLConf を持ち、`backend/config/urls.py` から include します。API の公開パスはルートの `/api/` prefix と app 内の resource path を組み合わせます。

複数の責務を持つ app では、View と Serializer は HTTP 境界、Service はユースケースと transaction、Model は永続化、Gateway は外部 API 境界を担当します。外部 SDK の型や例外を View や Model まで伝播させません。

複雑な app では、`types.py` に immutable な値・結果型、`repository.py` または `repositories.py` に `Protocol` と Django adapter、`services.py` にユースケース、`container.py` に実行時の依存合成を置きます。純粋な状態遷移、外部状態との照合、表示変換などが独立して複雑な場合は、責務名の module へ切り出します。この分割は必要な境界がある app にだけ適用し、固定ファイル一式として小さな app へ空の層を増やしません。

検証済み Webhook の拡張では、受付 app が immutable event と最小の handler 契約、event type ごとの registry を所有します。下流の機能 app はその契約を実装し、受付の composition root が handler を明示登録します。受付 service に個別イベントの業務処理を追加せず、状態 projection、reply、配信をそれぞれ独立した handler 責任に保ちます。

message／postback の interaction app は、入力解析、静的 command／action registry、外部 reply gateway、PII を含まない監査を一つの機能境界にまとめます。業務 action は interaction app へ動的 import せず、起動時の composition root から typed handler として明示登録します。受付台帳と interaction 監査は event ID で論理相関し、app 間の外部キーで永続化を密結合させません。

既存 app に owner 管理面を加える場合は、一般利用の service／repository と管理用の `admin_*` 境界を分けます。管理境界は owner authorization、write-only 入力、秘密を含まない presenter、revision-aware repository、外部確認 gateway を合成し、通常のチャネル参照や配信経路へ管理用 DTO を漏らしません。Frontend でも管理 Component、`*Api.ts`、`*Dto.ts`、`*State.ts` を同じ責務分離で揃え、秘密入力を共有 state や read model に保存しません。

別 app の永続化詳細へ直接依存せず、公開された型、Protocol、builder を介して連携します。循環 import を避けるため、View が必要な composition root は遅延 import できます。管理用ワークフローは Django management command に置き、対話入力、処理本体、repository をテスト可能な境界へ分離します。

同一 Backend 内の app 横断ライフサイクルは、`headless.py` 等で公開する typed port、reference probe、cleanup contract を `container.py` から合成できます。HTTP View を経由せず、呼び出し先 app の Model や非公開 repository を直接 import しないことを境界とします。

app の実行に必要な固定フォント等の資産は `/backend/<app>/assets/` に所有させ、ライセンスを隣接配置します。生成物や利用者 upload と混在させず、版・digest・実行可能性を app の起動境界で検証します。

## API

- HTTP API は `/api/` 配下に置く
- Django REST Framework の View と Response を使い、公開契約を HTTP テストで検証する
- 外部サービス呼び出しは Backend に閉じ込め、Frontend から LINE API を直接呼ばない
- owner 向け API はサーバー側 session で本人状態を確認し、状態変更では exact origin と CSRF token の両方を検証する
- 公開 Webhook は owner session の対象外とし、署名検証前の body や識別情報を信頼しない
- owner 向けチャネル管理 API は、read を含めて active owner session と同一 provider を transaction 内で再検証する
- 更新・有効化・無効化・削除は timezone-aware な `updatedAt` を revision として受け取り、stale な操作を明示的に拒否する

## 命名

- モジュール、関数、メソッド: `snake_case`
- クラス: `PascalCase`
- Django app と URL name: 小文字の簡潔なドメイン名
- テストクラス: 対象名 + `Tests`
- テストメソッド: `test_...`

## import

標準ライブラリと第三者ライブラリを空行で分けます。同じ app 内は明示的相対 import、Django や別パッケージは絶対 import を使います。

```python
import os
from pathlib import Path

from django.urls import path

from .views import HealthView
```

## テスト

コード・テスト・設定・依存の変更後は、rootから `sh scripts/check.sh backend` を実行し、migrationを含むBackend全体のRuff Lint・整形チェックを通します。準備・修正・対象範囲は [READMEのローカル品質チェック](../README.md#ローカル品質チェック) が正本です。Ruffは静的型検査の代わりにはならず、`--unsafe-fixes`を標準手順に含めません。

- Django test runnerとDRF `APITestCase` を使い、status codeとresponse bodyの両方を検証する。
- テストは対象Django app内に置く。小規模appは `tests.py`、複数責務のappは `tests/test_<責務>.py` へ分ける。
- 各テスト定義の直前に日本語コメントで `テストケース:` と `期待値:` を1行ずつ記載し、入力・操作と観測可能な期待結果を示す。
- 外部作用、状態projection、並行更新はリスクに応じて競合、安全性、処理時間、query budgetも検証する。
- LINE連携・資格情報・Webhook・リッチメニューでは [line-integration.md](../.kiro/steering/line-integration.md) の該当契約を読む。
