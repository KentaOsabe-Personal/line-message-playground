---
description: Manage dependent branch and pull-request stacks with the gh-stack extension. Use for explicit stack creation, navigation, publication, synchronization, restructuring, or merge requests; ordinary single-PR publishing uses create-pr.
metadata:
    author: github
    github-path: skills/gh-stack
    github-ref: refs/tags/v0.1.0
    github-repo: https://github.com/github/gh-stack
    github-tree-sha: c95c8b5b4dd850f3fef007b304428f5684f2fb87
    version: 0.0.9
name: gh-stack
---

# gh-stack

依存するbranchをtrunkからbottom→topへ並べ、各PRのbaseを直下のbranchにします。stackは直列です。独立した作業は別stackにし、下位の変更はそのbranchで行います。

## 実行条件

- `gh`（v2.0以降）と認証済みの `github/gh-stack` extensionを確認する。不足時の導入commandは `gh extension install github/gh-stack`。
- root AGENTSの公開・削除・mergeの許可範囲と秘密情報の規則に従う。特にpush／submitはforce-with-leaseを使用する。閲覧依頼を公開やmergeの許可と扱わない。
- 非対話実行を徹底する。`init`／`add` はbranch名、`checkout` は対象を渡し、`submit` は `--auto`、`view` は `--json` を必須にする。
- `init` 前にrepositoryの `rerere.enabled` を確認し、必要ならtrueへ設定する。複数remoteでは選択したremoteを明示する。対応commandの `--remote`、またはrepositoryの `remote.pushDefault` を使い、推測でoriginへ送らない。
- `checkout`／`modify`／`trunk` に `--remote` はない。共有branchの曖昧性は非共有branchへ移動して解消する。
- stageは対象fileを明示する。branch名はCLIへ渡した文字列のまま作られるため、命名規則は呼び出し側で満たす。

## 参照する手順

- 各commandのflag、部分成功、JSON、終了code、merge queueの扱いは [commands.md](references/commands.md)。状態を変えるcommandの該当節を実行前に確認する。
- 新規stack、途中層の修正、conflict、同期の中断、再構成は [workflows.md](references/workflows.md)。必要な経路だけを読む。

操作後は終了codeとstderrに加え `gh stack view --json` のbranch／PR／head／baseを確認します。`sync` は中断でもcode 0を返すため、codeだけで成功と扱いません。作成・更新したPRのURLと残作業を報告します。
