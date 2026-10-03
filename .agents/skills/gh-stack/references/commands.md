# gh-stack command契約

このSkillに同梱されていたCLI契約を保つ参照です。導入版のhelpと矛盾する場合は、そのcommandの一次情報を確認してから操作します。以下の例のbranch・remote・番号は実際の対象へ置き換えます。

## 初期化と追加

| Command | 契約 |
|---|---|
| `gh stack init --base <trunk> <branches...>` | 1件以上の名前が必須。既存branchを採用し、不足branchはtrunkから作成。最後のbranchへcheckout。base省略時はrepository既定branch。rerere有効化のpromptを事前設定で避ける。 |
| `gh stack add <branch>` | top、またはまだbranchのないtrunkから追加。作業treeの未commit変更は新branchへ引き継ぐ。別層に残す変更は先にcommit等で保護する。 |
| `gh stack add -Am <message> <branch>` | untrackedを含め全変更stage＋commit。`-um` はtrackedだけ。`-A`／`-u` は `-m` 必須・相互排他。通常は明示的なgit add／commitを使う。 |

現在branchにcommitがないと `add -Am` は新branchを作らず現在branchへcommitします。top以外からのaddはcode 5。`-m` だけでの自動命名には依存せず、常に名前を渡します。

## push・submit・link

- `gh stack push [--remote <name>]`: merged／queuedを除いたactive branchを、branchごとの明示的なforce-with-lease付きの**非atomicなmulti-ref push**で送る。PRは作成・更新しない。一部拒否でも他branchは更新され得る。
- `gh stack submit --auto [--open] [--remote <name>]`: 各active branchを順にforce-with-leaseでpushし、PRとmetadataを作成・同期する。処理全体は非atomicで、途中失敗前のpush／PR更新は残る。PRのbaseは最初の未merge祖先。通常draft、`--open` は既存・新規をreadyにする。
- submitはPRをGitHub stackへ連結する。既存stack全PRがmerge済みなら、未merge branchをtrunk起点の新stackに分け、旧stackを保つ。stack未対応repoでは非対話code 9。
- `--auto` のtitleは単一commitならsubject、bodyはcommit body。複数commitはbranch名から生成。submitにtitle／body指定flagはなく、必要なら作成後に `gh pr edit` を使う。
- `gh stack link [--base <trunk>] [--open] [--remote <name>] <branch-or-pr>...`: 外部ツールで管理するbranch／PR向け。通常2件以上をbottom→topで渡す。local stack追跡状態を作らない。
- linkのbranch pushは**non-force・atomic**。PRを自動作成し、既存PRのbaseも依存順に修正する。GitHub groupingは追加のみで既存PRを除去しない。
- linkの最初の数値が既存stack番号なら、残りをそのtopへ追加する。既存memberはskipし、別stackのmemberは拒否する。それ以外の数値はPR番号、存在しなければbranch名として解決する。

push／submitの一部拒否では実際に更新されたbranchとPRを確認し、拒否原因を解消して再実行します。成功済みの処理を巻き戻したと仮定しません。

## syncとrebase

`gh stack sync [--remote <name>] [--prune]` はfetch→GitHub stackをlocalへ同期→trunk fast-forward→必要時cascade rebase→active branchのatomic push→PR状態とstack grouping同期を行います。新PRは作成しません。2件以上のPRをgroupingし、既存memberは追加のみです。

- GitHub側で追加されたbranchは取得する。localとremoteの構成が異なる場合、非対話では `Sync aborted` を出してcode 0でも中断する。
- trunk分岐は警告。rebase conflictでは全branchをrebase前へ復元してcode 3。続行用rebaseが残っていると仮定しない。
- 非対話でmerged local branchを削除するのは `--prune` 明示時だけ。branch削除の承認範囲を確認する。
- squash mergeは自動検知し、`--onto` で未merge commitだけを新しい親へ適用する。

| `gh stack rebase` の指定 | 対象 |
|---|---|
| 指定なし／`[branch]` | 既定は現在branch。remote取得を含むcascade rebase |
| `--downstack` | trunkから現在branchまで |
| `--upstack` | 現在branchからtopまで |
| `--no-trunk` | fetchもtrunkとのrebaseもせず、branch間だけを揃える |
| `--continue` | conflictを解決・stageして続行 |
| `--abort` | 全branchをrebase前へ復元 |
| `--remote <name>` | 取得先remote |

## 閲覧・移動

`gh stack view --json` はstdoutへ `trunk`、`currentBranch`、`branches` を返します。branchのfieldは `name`、`head`、`base`（最終sync時の親HEAD）、`isCurrent`、`isMerged`、`isQueued`、`needsRebase`。存在時だけ `pr.number`／`url`／`state`（OPEN／MERGED／QUEUED）が付きます。

`up [n]`／`down [n]` はtrunkから離れる／近付く方向、`top`／`bottom` は最上位／最初の未merge branch、`trunk` はtrunkへ移動します。範囲外へ進まず、active branchからの移動ではmerged branchをskipします。

`gh stack checkout <stack-number|pr-number|pr-url|branch>` は引数必須。裸の数値はstack番号→PR番号→branch名の順。番号／PR URLはGitHubからbranchと追跡情報を取得します。branch名はlocal追跡stackだけを解決します。

localとremoteのstack構成が異なるcheckoutはflagで回避できない対話promptになるため、必要なlocal構成を保護したうえで `gh stack unstack --local`、対象checkoutの順に復旧します。

## unstack・merge

- `gh stack unstack [<stack-number>]`: local追跡とGitHub groupingを除去する。PR・branch自体は削除しない。番号指定はlocal checkout不要のremote API操作。local追跡があればそれも除去する。
- `--local`: GitHubへ接続せずlocal追跡だけ除去。localにない番号との併用はerror。未知のremote番号はcode 2。
- merge依頼時は `gh stack merge --yes`。`gh pr merge` はstacked PR向けではない。無指定ではstack全体をbottom→topでmergeする。
- PR番号指定はそのPRまで、stack番号指定はそのstack（local checkout不要）。`--squash`／`--rebase`／`--merge`／`--merge-method <method>` を使い、省略時は最後に使った方式。
- 通常mergeはatomicで、1件でも不可なら全件未merge。事前確認はopen／非draft等の基本状態で、merge requirementの迂回はできない。
- baseがmerge queueを使う場合はqueueへ一括追加する。指定方式はwarning付きで無視されqueueが選択する。実際の着地はqueue処理で分割され得る。

## 終了code

statusはstderr、JSON等のdataはstdoutです。codeとstatus両方を確認します。

| Code | 意味／復旧 |
|---|---|
| 0 | 成功。ただしsync中断のstatusを除外する |
| 1 | 一般error。stderrでcommit／push等の原因を確認 |
| 2 | stackなし、または指定stack不在 |
| 3 | rebase conflict。syncの自動復元と明示rebaseの停止を区別 |
| 4 | GitHub API失敗。認証等を確認して再試行 |
| 5 | 引数不正、またはtop以外からadd |
| 6 | 複数stackでbranch共有。非共有branchへ移動 |
| 7 | rebase進行中。解決してcontinue、またはabort |
| 8 | stack lock。他processの完了を待つ。lock timeoutは5秒 |
| 9 | stacked PR未対応。repositoryで有効化が必要 |
| 10 | modify中断。通常このSkillではmodifyを使わない。既存sessionは `gh stack modify --abort` で復元 |
