# プロジェクト構造

## 組織化の方針

リポジトリ直下を実行サービスと運用責務で分ける service-first 構成です。新規コードは所有するサービス内へ置き、Frontend と Backend の接続は HTTP API、サービスの統合は Docker Compose を介します。

## ディレクトリパターン

- `/frontend/`: React UI、ブラウザの状態、ビルド・テスト。詳細は [Frontend AGENTS](../../frontend/AGENTS.md)。
- `/backend/config/`: Django project設定。`/backend/<app>/` は各ドメイン。詳細は [Backend AGENTS](../../backend/AGENTS.md)。

### アーキテクチャ決定記録

**場所**: `/docs/adr/`
**目的**: 複数機能へ影響する長期的な制約や、不可逆な外部資源の扱いを短い決定記録として残す

ADR は連番付きファイル名を使い、実装の網羅説明ではなく、判断の背景、採用した原則、結果を記録します。

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
- 複数 app の参照整合性を伴う削除は、各 app の公開 reference contract を composition root で束ね、削除側から相手 Model を直接探索しない
- Frontend と Backend など実行サービス間の契約は HTTP API で表現し、同一 Backend 内の app 間は公開 typed contract を composition root で合成する

## コード配置の原則

- 生成物（`dist`、`node_modules`、`*.tsbuildinfo`、`__pycache__` 等）をソース配置先にしない
- 新しい機能は、まず Frontend、Backend app、コンテナ運用のどの責務かを決める
- 現在サンプルがないサービス層、repository 層、状態管理、CSS 設計を既存標準として仮定しない
- 新しいコードが既存パターンに従う限り、この文書へファイル単位の追記を必要としない

_移行日: 2026-09-27。サービス固有の配置・命名・import・テスト規則を各AGENTSへ移動。_
