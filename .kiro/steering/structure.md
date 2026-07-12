# プロジェクト構造

## 組織化の方針

リポジトリ直下を実行サービスと運用責務で分ける service-first 構成です。新規コードは所有するサービス内へ置き、Frontend と Backend の接続は HTTP API、サービスの統合は Docker Compose を介します。

## ディレクトリパターン

### Frontend アプリケーション

**場所**: `/frontend/`  
**目的**: React UI、ブラウザ側の状態、Frontend のビルド・テスト設定  
**実装場所**: `/frontend/src/`
**テスト場所**: `/frontend/test/`

`main.tsx` はアプリケーション起動とグローバル CSS 読み込み、`App.tsx` は画面ルートを担当します。現在は小規模な flat 構造のため、feature、components、hooks 等の分割規則はまだ固定しません。

flat 構造でも、UI とイベント接続、状態遷移、HTTP 通信、境界 DTO の検証はモジュールの責務として分離します。複雑な画面状態は純粋な遷移関数へ切り出し、Component へ通信状態や再試行判断を埋め込みません。

### Backend プロジェクト設定

**場所**: `/backend/config/`  
**目的**: Django settings、ルート URL、ASGI/WSGI などプロジェクト全体の構成

機能実装を `config` へ置かず、Django app に分離します。

### Backend ドメイン app

**場所**: `/backend/<app>/`  
**目的**: 1つの機能領域に属する View、URL、Model、テスト等

各 app は app-local な URLConf を持ち、`backend/config/urls.py` から include します。API の公開パスはルートの `/api/` prefix と app 内の resource path を組み合わせます。

複数の責務を持つ app では、View と Serializer は HTTP 境界、Service はユースケースと transaction、Model は永続化、Gateway は外部 API 境界を担当します。外部 SDK の型や例外を View や Model まで伝播させません。

### コンテナ固有の補助処理

**場所**: `/docker/`  
**目的**: データベース初期化など、特定コンテナの起動・開発支援処理

アプリケーションの業務ロジックはここへ置きません。

## 依存境界

- Frontend は相対 URL `/api/...` を使い、Docker 内ホスト名や Backend の絶対 URL をブラウザコードへ埋め込まない
- Vite の開発 proxy が `/api` を Backend サービスへ転送する
- Frontend は MySQL や LINE Messaging API へ直接アクセスしない
- Backend の機能 app は Django project 設定から分離し、ルート URLConf は app の URLConf を合成する
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
_更新日: 2026-07-12。配置判断に使えるパターンを記録し、ディレクトリツリーの網羅表にはしない。_
