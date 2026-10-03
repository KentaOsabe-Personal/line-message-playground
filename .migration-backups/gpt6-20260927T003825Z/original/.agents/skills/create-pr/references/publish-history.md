# Publish History Reference

Use this reference only when publishing `copilot-cli-history-v2` changes and you need past conventions for commit messages, branches, or PR shape.

## Observed Branch and PR Pattern

- Current long-running integration branch: `develop`.
- Default target branch: `main`.
- Remote default branch: `origin/main`.
- Recent PRs in `copilot-cli-history-v2` used `develop` as the head branch and `main` as the base branch.
- Recent PRs were ready PRs, not drafts.

## Recent PR Title Examples

- `[codex] Remove Rails runtime artifacts`
- `DeepWiki日本語化検証`
- `Spec方針変更と結合動作検証完了、Steering資料更新`
- `[codex] Django History API を実装`
- `bigquery-session-repository と presenter contract を追加`
- `Django Presenter 契約を実装`
- `[codex] Django backend と履歴 reader 基盤を追加`
- `BigQuery read model schema contract`
- `Django backend foundation`

## Commit Message Patterns

Common patterns in local history:

- Feature task completion: `<feature-name> taskN done`
- Multiple task completion: `<feature-name> taskN~M done`
- Task split or spec progress: `<feature-name> Task分割まで`
- Japanese fix or maintenance summary: `一覧画面の「一部欠損あり」のラベル削除`
- Steering or docs update: `Steering資料更新...`
- Conventional prefix when useful: `feat(...)`, `fix(...)`, `refactor(...)`

Prefer concise messages that describe the actual committed diff. Avoid broad messages like `update` unless the diff is genuinely miscellaneous and user-approved.

## PR Body Shape

Use short Japanese Markdown by default:

```md
## 概要
- ...

## 検証
- `command`: 何を確認したか
```

Add a `## 補足` section only for blockers, skipped checks, intentional exclusions, or follow-up context.

Do not list validation commands without context. The validation section should explain the verification intent or result, not just the literal command output.
