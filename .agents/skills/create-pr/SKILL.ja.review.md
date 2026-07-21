# create-pr 日本語レビュー版

このファイルは人間レビュー用の日本語版です。Codex の Skill として自動検出される対象は同じディレクトリの `SKILL.md` であり、このファイルは `SKILL.md` から参照していないため、通常の Skill 実行時には読み込まれません。

## 概要

この Skill は `line-message-playground` の完了済みローカル変更を公開するために使います。通常の流れは、変更範囲を確認し、意図したファイルだけをステージし、`develop` を push し、`main` 向けの ready PR を作成または更新することです。

PR タイトル、branch/base の既定値、PR body の雰囲気を過去の運用に合わせる必要がある場合は、`references/publish-history.md` を参照します。

## 既定値

- リポジトリ: `KentaOsabe-Personal/line-message-playground`
- 通常のブランチフロー: `develop` から `main`
- PR 状態: 既定では ready。ユーザーが draft を指定した場合、または作業が未完了である場合だけ draft にする
- 言語: 既存タイトルやユーザー指定が英語でない限り、ユーザー向け要約と PR 本文は日本語
- PR 本文見出し: 既定では `## 概要`、`## 検証`、必要な場合だけ `## 補足` を使う
- スコープ安全性: 関係ないユーザー変更を黙って stage しない

## 実行前提

- `origin` が GitHub リポジトリを指すローカル git checkout で実行する
- PR 作成・更新には GitHub CLI `gh` がインストール済みかつ認証済みであること
- このリポジトリではローカルの GitHub CLI を GitHub 認証の正規経路として扱う。CLI 認証が失敗しても、GitHub connector、アプリ内 browser、その他の browser へ迂回しない
- GitHub CLI の認証確認は外部ネットワークへ接続できる状態で実行し、必要なら権限昇格を要求する。sandbox 内の `gh auth status` は GitHub へ到達できないだけでも `The token in default is invalid` と報告するため、その結果だけで保存済み credentials が無効だと判断しない
- network 制限外で `gh auth status --active --hostname github.com` と `gh api user --jq .login` の両方を事前確認する。sandbox 内で失敗済みの場合はその結果を破棄し、認証復旧を始める前に network access ありで再実行する
- 通常の repository workflow では `gh api user --jq .login` の結果が `KentaOsabe-Personal` であることを確認する。別 account で公開しようとしている場合は処理を止めてユーザーに確認する
- `gh auth login -h github.com -p ssh --web` は、network access ありの2つの事前確認が両方とも認証固有の理由で失敗した場合だけ実行する。device flow 完了後は両方を再実行する
- 正しい事前確認が成功しているのに Codex app が GitHub CLI を利用できないと表示する場合は、app 側の表示が stale だと扱い、ユーザーに app の再起動を依頼する。別の GitHub 認証経路は試さない
- 実行前に対象ファイルが stage 済みである必要はない。stage は diff 確認後にこの workflow 内で行う
- すでに stage 済みのファイルがある場合も、`git diff --staged` を確認して対象範囲に含めてよいか検証する
- BigQuery / GCP 作業では、認証情報はリポジトリ外にある前提にする
  - 既定では ADC `~/.config/gcloud/application_default_credentials.json` を使う
  - `GOOGLE_APPLICATION_CREDENTIALS` は、ユーザーが安全なテスト fixture と明示しない限り、リポジトリ外の credentials file へのパスとして扱う
  - Docker Compose では `~/.config/gcloud` を backend container へ read-only mount する。credentials file を repo 内へコピーしない

## 認証情報の安全確認

stage または commit の前に、`git status` と diff から認証情報リスクを確認します。疑わしいファイルや差分があれば、処理を止めてユーザーに確認します。

次は既定で高リスクとして扱います。

- `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`
- `*service-account*.json`, `*credentials*.json`, `*secret*.json`
- `application_default_credentials.json`
- `.config/gcloud/` 配下のファイル
- `private_key`, `client_email`, `client_secret`, `api_key`, `access_token`, `refresh_token`, `password`, `BEGIN PRIVATE KEY`, `GOOGLE_APPLICATION_CREDENTIALS` を含む差分

高リスクファイルがすでに stage 済みの場合は commit しません。unstage するか、意図した sanitization 済み fixture なのかをユーザーに確認し、安全であることが明示されてから進めます。

## Workflow

1. リポジトリ状態を確認する
   - `git status --short` と `git branch --show-current` を実行する
   - stage 前の diff は `git diff`、stage 済みの内容は `git diff --staged` で確認する
   - unrelated または曖昧な変更があれば、どの path を含めるかユーザーに確認する
   - stage 対象を決める前に認証情報の安全確認を行う

2. branch と base を確認する
   - 通常は `develop` から `main` へ公開する
   - すでに `develop` にいる場合はそのまま使う
   - 別 feature branch にいる場合は、ユーザー指定があるか、その branch が現在作業に明確に対応する場合だけ使う
   - `main` にいる場合は直接 commit せず、適切な作業 branch を作成または切り替える

3. commit 前に可能な範囲で検証する
   - 変更領域に合う確認コマンドを使う
   - backend 変更では repository docs、spec、package scripts、過去 task notes から適切な test command を選ぶ
   - frontend 変更では relevant package の test、lint、typecheck、build を実行する
   - 依存や service 不足で検証できない場合は、最終報告と PR body に blocker を明記する

4. 意図したファイルだけ stage する
   - unrelated 変更がある場合は明示的な file path で `git add` する
   - `git add -A` は全変更が対象だと確認できる場合だけ使う
   - stage 後に `git status --short` を再確認する

5. 簡潔な commit message で commit する
   - 短い日本語要約または project style の task message を使う
   - 例: `<feature> taskN done`, `<feature> taskN~M done`, `Steering資料更新`, 短い fix summary
   - `feat(...)`, `fix(...)`, `refactor(...)` などは、明確さに寄与する場合だけ使う

6. push する
   - 必要に応じて upstream tracking 付きで current branch を origin へ push する
   - 通常 flow では、current branch が `develop` であることを確認したうえで `git push origin develop` を使える

7. PR を作成または更新する
   - `gh pr list --head <branch> --state open` で current branch の既存 open PR を確認する
   - 既存 PR がある場合、ユーザーが求めた場合または title/body が明らかに古い場合だけ更新する
   - PR がない場合は `main` 向けに作成する
   - title は公開する差分全体を要約する。`[codex] ...` は許容するが必須ではない
   - PR body は既定で日本語見出しにする
     - `## 概要`
     - `## 検証`
     - blocker、skip した検証、意図的な除外、後続メモがある場合だけ `## 補足`
   - 検証欄にはコマンド名だけを列挙しない。何を確認したコマンドなのか、またはどの結果を確認したのかを併記する
     - 良い例: ``- `jq empty lsp.json`: repo-level LSP 設定が valid JSON であることを確認``
     - 良い例: ``- `docker compose config --quiet`: Compose 設定が構文上有効で、サービス定義として解釈できることを確認``
     - 悪い例: ``- `jq empty lsp.json``

8. 結果を報告する
   - branch、commit hash、PR URL、実行した検証、意図的に残した未コミットファイルを含める
   - Codex の git directive は実際に成功した action についてだけ出す

## 安全ルール

- ユーザーが明示しない限り、`git reset --hard`、`git checkout --`、force push、branch 削除をしない
- `.env`、secrets、credential files、local runtime artifacts、editor files、unrelated generated files を含めない
- 現在の tree で実行した fresh command output なしに「テストが通った」と主張しない
- commit または push が失敗した場合は PR を作成しない
- ユーザーが明示しない限り PR を merge しない
