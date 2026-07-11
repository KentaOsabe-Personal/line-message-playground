---
name: create-pr
description: Commit scoped local changes, push this repository's develop branch, and create or update a GitHub pull request. Use when the user asks to commit, push, publish, open a PR, create a pull request, or perform the repeated commit, push, and PR flow for copilot-cli-history-v2 after implementation or documentation work.
---

# create-pr

## Overview

Use this skill to publish completed local work from `copilot-cli-history-v2` with the project's usual discipline: inspect scope, commit only intended files, push `develop`, and create a ready PR to `main` unless the user explicitly asks otherwise.

Read `references/publish-history.md` when selecting titles, branch/base defaults, or PR body style from past repository practice.

## Defaults

- Repository: `KentaOsabe-Personal/copilot-cli-history-v2`.
- Normal branch flow: `develop` -> `main`.
- PR state: ready for review by default. Create a draft only when the user asks for draft or the work is explicitly incomplete.
- Language: use Japanese for user-facing summaries and PR prose unless an existing title or user request is English.
- PR body headings: use Japanese headings by default, for example `## 概要`, `## 検証`, and `## 補足`.
- Scope safety: never stage unrelated user changes silently.

## Prerequisites

- Require a local git checkout with `origin` pointing at the GitHub repository.
- Require GitHub CLI `gh` to be installed and authenticated before creating or updating a PR.
- Do not require files to be staged before this skill runs. Treat staging as part of this workflow after diff review.
- If files are already staged, inspect `git diff --staged` and verify that staged content still belongs to the requested publish scope.
- For BigQuery or GCP work, assume credentials live outside the repository by default:
  - Prefer ADC from `~/.config/gcloud/application_default_credentials.json`.
  - Accept `GOOGLE_APPLICATION_CREDENTIALS` only as a path to a credentials file outside the repository unless the user explicitly confirms a safe test fixture.
  - In Docker Compose, `~/.config/gcloud` is mounted read-only into the backend container; do not copy those credential files into the repo.

## Credential Safety

Before staging or committing, scan status and diffs for credential risk. Stop and ask the user before proceeding if any suspicious file or diff appears.

Treat these as high-risk by default:

- `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`
- `*service-account*.json`, `*credentials*.json`, `*secret*.json`
- `application_default_credentials.json`
- files under `.config/gcloud/`
- diffs containing `private_key`, `client_email`, `client_secret`, `api_key`, `access_token`, `refresh_token`, `password`, `BEGIN PRIVATE KEY`, or `GOOGLE_APPLICATION_CREDENTIALS`

If a high-risk file is already staged, do not commit. Ask whether to unstage it or whether it is an intentional sanitized fixture. Only proceed when the user explicitly confirms it is safe.

## Workflow

1. Inspect repository state.
   - Run `git status --short` and `git branch --show-current`.
   - Read the relevant diff before staging: `git diff` and, when staged content exists, `git diff --staged`.
   - If the worktree contains unrelated or unclear changes, ask which paths belong to this publish flow.
   - Apply the credential safety checks before deciding what to stage.

2. Confirm branch and base.
   - Prefer publishing from `develop` to `main`.
   - If already on `develop`, stay there.
   - If on another feature branch, publish that branch only when the user requested it or the branch name clearly belongs to the current work.
   - If on `main`, do not commit directly there; create or switch to an appropriate working branch.

3. Validate before commit when feasible.
   - Use the checks that match the changed area.
   - For backend changes, prefer project test commands found in repository docs, specs, package scripts, or prior task notes.
   - For frontend changes, run the relevant package test, lint, typecheck, or build command.
   - If validation cannot run because dependencies or services are unavailable, record the exact blocker in the final summary and PR body.

4. Stage intentionally.
   - Use explicit file paths when unrelated changes exist.
   - Use `git add -A` only when all current changes are confirmed in scope.
   - Re-run `git status --short` after staging.

5. Commit with a concise message.
   - Use a short Japanese summary or a project-style task message.
   - Prefer formats seen in the repository history, such as `<feature> taskN done`, `<feature> taskN~M done`, `Steering資料更新`, or a terse fix summary.
   - Include a conventional prefix only when it adds clarity or matches the changed area, for example `feat(...)`, `fix(...)`, or `refactor(...)`.

6. Push.
   - Push the current branch to origin with upstream tracking when needed.
   - For the usual flow, `git push origin develop` is acceptable after confirming the current branch is `develop`.

7. Create or update the PR.
   - First check for an existing open PR for the current branch: `gh pr list --head <branch> --state open`.
   - If a PR exists, update it only if the user asked or the title/body is clearly stale.
   - If no PR exists, create one targeting `main`.
   - Use a title that summarizes the whole published diff. `[codex] ...` is acceptable but not mandatory; follow recent repository history and user wording.
   - Write a PR body with Japanese headings by default:
     - `## 概要`
     - `## 検証`
     - `## 補足` only for blockers, skipped checks, intentional exclusions, or follow-up context
   - In the validation section, do not list only raw command names. Pair each command with the purpose or result it verifies, so reviewers can understand why it matters.
     - Good: ``- `jq empty lsp.json`: repo-level LSP 設定が valid JSON であることを確認``
     - Good: ``- `docker compose config --quiet`: Compose 設定が構文上有効で、サービス定義として解釈できることを確認``
     - Bad: `- jq empty lsp.json`

8. Report the result.
   - Include branch, commit hash, PR URL, validation run, and any uncommitted files intentionally left untouched.
   - Emit Codex git directives only for actions that actually succeeded.

## Safety Rules

- Do not use `git reset --hard`, `git checkout --`, force push, or delete branches unless the user explicitly asks.
- Do not include `.env`, secrets, credential files, local runtime artifacts, editor files, or unrelated generated files.
- Do not claim tests passed without fresh command output from the current tree.
- Do not create a PR when commit or push failed.
- Do not merge the PR unless the user explicitly asks.
