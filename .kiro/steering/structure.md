# プロジェクト構造

## 組織化の方針

リポジトリ直下を実行サービスと運用責務で分ける service-first 構成です。新規コードは所有するサービス内へ置き、Frontend と Backend の接続は HTTP API、サービスの統合は Docker Compose を介します。

## ディレクトリパターン

### Frontend アプリケーション

**場所**: `/frontend/`  
**目的**: React UI、ブラウザ側の状態、Frontend のビルド・テスト設定  
**実装場所**: `/frontend/src/`
**テスト場所**: `/frontend/test/`

`main.tsx` はアプリケーション起動とグローバル CSS 読み込み、`App.tsx` は画面ルートを担当します。現在は flat 構造を維持しているため、feature directory、共通 components、hooks 等の分割規則はまだ固定しません。

flat 構造でも、UI とイベント接続、状態遷移、HTTP 通信、境界 DTO の検証はモジュールの責務として分離します。複雑な画面状態は純粋な遷移関数へ切り出し、Component へ通信状態や再試行判断を埋め込みません。

確立済みの責務接尾辞を使い、Component は表示と操作の接続、`*Api.ts` は HTTP 手順と安全なエラー変換、`*Dto.ts` は `unknown` な境界データの実行時検証、`*State.ts` は純粋な状態遷移を担当します。cookie、CSRF、共通 fetch 設定や LIFF SDK は専用 adapter に閉じ込め、各 Component から直接扱いません。

### Backend プロジェクト設定

**場所**: `/backend/config/`  
**目的**: Django settings、ルート URL、ASGI/WSGI などプロジェクト全体の構成

機能実装を `config` へ置かず、Django app に分離します。

### Backend ドメイン app

**場所**: `/backend/<app>/`  
**目的**: 1つの機能領域に属する View、URL、Model、テスト等

各 app は app-local な URLConf を持ち、`backend/config/urls.py` から include します。API の公開パスはルートの `/api/` prefix と app 内の resource path を組み合わせます。

複数の責務を持つ app では、View と Serializer は HTTP 境界、Service はユースケースと transaction、Model は永続化、Gateway は外部 API 境界を担当します。外部 SDK の型や例外を View や Model まで伝播させません。

複雑な app では、`types.py` に immutable な値・結果型、`repositories.py` に `Protocol` と Django adapter、`services.py` にユースケース、`container.py` に実行時の依存合成を置きます。この分割は必要な境界がある app にだけ適用し、小さな app へ空の層を増やしません。

検証済み Webhook の拡張では、受付 app が immutable event と最小の handler 契約、event type ごとの registry を所有します。下流の機能 app はその契約を実装し、受付の composition root が handler を明示登録します。受付 service に個別イベントの業務処理を追加せず、状態 projection、reply、配信をそれぞれ独立した handler 責任に保ちます。

message／postback の interaction app は、入力解析、静的 command／action registry、外部 reply gateway、PII を含まない監査を一つの機能境界にまとめます。業務 action は interaction app へ動的 import せず、起動時の composition root から typed handler として明示登録します。受付台帳と interaction 監査は event ID で論理相関し、app 間の外部キーで永続化を密結合させません。

別 app の永続化詳細へ直接依存せず、公開された型、Protocol、builder を介して連携します。循環 import を避けるため、View が必要な composition root は遅延 import できます。管理用ワークフローは Django management command に置き、対話入力、処理本体、repository をテスト可能な境界へ分離します。

### コンテナ固有の補助処理

**場所**: `/docker/`  
**目的**: データベース初期化など、特定コンテナの起動・開発支援処理

アプリケーションの業務ロジックはここへ置きません。

## 依存境界

- Frontend は相対 URL `/api/...` を使い、Docker 内ホスト名や Backend の絶対 URL をブラウザコードへ埋め込まない
- Vite の開発 proxy が `/api` を Backend サービスへ転送する
- Frontend は MySQL や LINE Messaging API へ直接アクセスしない
- Backend の機能 app は Django project 設定から分離し、ルート URLConf は app の URLConf を合成する
- Backend app 間は相手 app の Model ではなく、公開型と明示的な adapter／builder を依存境界にする
- サービス横断の契約は暗黙の内部 import ではなく HTTP API で表現する

## 命名規則

### Python / Django

- モジュール、関数、メソッド: `snake_case`
- クラス: `PascalCase`
- Django app と URL name: 小文字の簡潔なドメイン名
- テストクラス: 対象名 + `Tests`
- テストメソッド: `test_...`

### React / TypeScript

- Component ファイルと Component: `PascalCase`（例: `App.tsx` / `App`）
- 型: `PascalCase`
- 変数、関数、state: `camelCase`
- Component テスト: `<Component>.test.tsx`

## import の構成

### TypeScript

外部パッケージを先に置き、同じ `src` 内は相対 import を使います。拡張子は省略します。path alias は現在設定されていないため、`@/` 等を前提にしません。

```typescript
import { renderToString } from 'react-dom/server'
import App from './App'
```

### Python

標準ライブラリと第三者ライブラリを空行で分けます。同じ app 内は明示的相対 import、Django や別パッケージは絶対 import を使います。

```python
import os
from pathlib import Path

from django.urls import path

from .views import HealthView
```

## テスト配置

- Frontend テストは `frontend/test/` 配下に置き、`*.test.ts` または `*.test.tsx` とする
- Backend テストは対象 Django app 内に置き、公開 HTTP 契約を検証する
- 小規模な Backend app は `tests.py`、複数責務を持つ app は `tests/` package と `test_<責務>.py` で分割する

## コード配置の原則

- 生成物（`dist`、`node_modules`、`*.tsbuildinfo`、`__pycache__` 等）をソース配置先にしない
- 新しい機能は、まず Frontend、Backend app、コンテナ運用のどの責務かを決める
- 現在サンプルがないサービス層、repository 層、状態管理、CSS 設計を既存標準として仮定しない
- 新しいコードが既存パターンに従う限り、この文書へファイル単位の追記を必要としない

---
_更新日: 2026-07-23。Webhook interaction の静的 registry、typed action 登録、疎結合監査パターンを反映。配置判断に使えるパターンを記録し、ディレクトリツリーの網羅表にはしない。_
