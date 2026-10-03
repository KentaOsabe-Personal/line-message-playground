---
name: create-pr
description: Publish completed line-message-playground changes when the user requests commit, push, or a GitHub pull request. Preserves this repository’s develop-to-main flow, CLI-only authentication, account checks, and Japanese PR conventions; stacked PRs use gh-stack.
---

# 変更の公開

`KentaOsabe-Personal/line-message-playground` の通常フローは `develop` → `main`、PRはreadyです。draftは依頼された場合または明確に未完了の場合。PR・要約は日本語（既存の英語titleやユーザー指定を優先）にします。

## GitHub認証

- `origin` が対象GitHub repositoryのcheckoutであることと、`gh` の利用可能性を確認する。
- 認証はローカルGitHub CLIを正本とし、失敗時にconnectorやbrowserへ迂回しない。
- networkアクセス可能な実行環境で `gh auth status --active --hostname github.com` と `gh api user --jq .login` を確認する。必要ならsandbox escalationを使う。sandbox内の到達不能をtoken無効の証拠にしない。
- 既定accountは `KentaOsabe-Personal`。別accountでの公開はユーザーに確認する。
- 両確認が認証固有の理由で失敗した場合だけ `gh auth login -h github.com -p ssh --web` で復旧し、完了後に両方を再確認する。
- CLIが成功しappの表示だけが未接続なら表示の不整合として再起動を案内し、別認証経路を試さない。

## 公開前の安全確認

root AGENTSのGit・秘密情報規則を適用する。status、差分、既存stagingを確認し、公開範囲が不明な変更は確認する。事前stagingは必須ではない。

- `.env`／`.env.*`、秘密鍵・証明書、credential／service-account／secret JSON、ADC、`.config/gcloud/` 配下は要確認対象。
- `private_key`、`client_email`、`client_secret`、`api_key`、`access_token`、`refresh_token`、`password`、秘密鍵header、`GOOGLE_APPLICATION_CREDENTIALS` を含む差分は内容を確認し、実秘密値を表示しない。
- 疑わしいファイルや差分があれば停止して確認する。既にstage済みでもcommitしない。sanitized fixtureはユーザーが安全と明示した場合だけ進める。
- credential fileや秘密をrepositoryへコピーしない。既存環境にないGCP／Compose構成を仮定しない。

## 公開手順

1. `git status --short`、current branch、unstaged／staged diffから対象を確定する。`develop`なら維持する。別feature branchは依頼対象と分かる場合に使用し、`main`へ直接commitしない。
2. 変更領域に対応する既存検証を行う。実行不能ならblockerを記録し、未実施を成功扱いしない。
3. 対象pathをstageする。`git add -A` は全変更が範囲内と確認できた場合だけ許容し、stage後のstatusを再確認する。Kiro自律実装内のより厳しいselective staging規則を緩めない。
4. 実差分を表す簡潔な日本語summaryまたはproject-style task messageでcommitする。通常branchをupstream付きでpushする。commit／push失敗時にPRを作らない。
5. `gh pr list --head <branch> --state open` で既存PRを調べる。更新は依頼または明確なtitle／bodyの陳腐化がある場合。新規は通常 `main` をbaseとする。stack操作は `$gh-stack` に従う。
6. PR本文は `## 概要`、`## 検証`、必要時だけ `## 補足`。検証commandには目的・結果を添え、未実施・blockerを明示する。titleは公開する差分全体を表す。
7. branch、commit、PR URL、検証結果、意図して残した変更を報告する。mergeは明示された場合だけ行う。
