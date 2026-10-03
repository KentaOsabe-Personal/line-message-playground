# stack操作の経路

commandの副作用・非対話flag・部分成功は [commands.md](commands.md) を併用します。各操作はユーザーが依頼した範囲で行います。

## 新規stack

依存元をbottom、利用側をtopとする層を選び、`gh stack init --base <trunk> <first-branch>` で開始します。対象fileを明示してstage／commitし、次の層は `gh stack add <next-branch>`。1層に複数commitがあって構いません。別の関心事の変更を同じ層へ混ぜません。

公開依頼時は `gh stack submit --auto`（ready指定時は `--open`）。`gh stack view --json` で各PRのbase、head、状態を確認します。外部ツールでbranchを管理している場合はlocal追跡を作らない `link` を使います。

## 途中層の修正

`down`／`bottom`／`checkout <branch>` で責任を持つbranchへ移り、修正・commit後に `gh stack rebase --upstack`。必要な層へ戻ります。公開済みstackの更新依頼ならpushして実際のPR状態を確認します。

## 同期・squash merge後

`gh stack sync` はmerged branchを検知して残りを正しい親へrebaseします。JSONの `isMerged`、`needsRebase`、head／baseを確認します。local branchの削除が依頼範囲なら `--prune` を指定できます。

`Sync aborted` はcode 0でも未完了です。local／remoteの構成と変更を把握し、残す構成を決めて追跡の解除・再構築を行います。`unstack --local` はremote groupingを保持します。通常のunstackはGitHub groupingも除去するため、混同しません。

## conflict復旧

syncがcode 3ならbranchは自動復元済みなので `gh stack rebase` を明示的に開始します。明示rebaseのconflictではstderrと実際のconflict fileを確認し、解決fileだけ `git add` して `gh stack rebase --continue`。続くconflictも同様に扱います。解決不能・取消時は `gh stack rebase --abort` で復元します。

## 並べ替え・名前変更

現在の構成・PRを把握し、`unstack` で追跡／groupingを解除して、承認されたbranch操作を行い、`gh stack init --base <trunk> <ordered-branches...>` で再構築します。unstackそのものはPR・branchを削除しません。

## 確認に使うJSON

- 要rebase: `.branches[] | select(.needsRebase == true)`
- open PR: `.branches[] | select(.pr.state == "OPEN") | .pr.url`
- merged: `.branches[] | select(.isMerged == true) | .name`
- 現在branch: `.currentBranch`

read-onlyの閲覧結果から自動でrebase／push／mergeへ進まず、依頼範囲に必要な操作を選びます。
