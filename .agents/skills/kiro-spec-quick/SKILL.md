---
name: kiro-spec-quick
description: Run the existing Kiro init, requirements, design, and tasks skills as one pipeline. Supports interactive phase prompts and explicit auto mode, with sizing and a final cross-artifact sanity review.
---

# Kiro仕様の連続生成

引数からdescription／featureと `--auto` を取り出します。`--auto` のない場合は既存の対話モード、ある場合だけ自動モードです。どちらもサイズgateと内部reviewを省略しません。

## Phase loop

| Phase | 正本Skill | 対話モードの遷移 |
|---|---|---|
| 1 初期化 | `$kiro-spec-init` | requirementsへ進む確認 |
| 2 要件 | `$kiro-spec-requirements <feature>` | designへ進む確認 |
| 3 設計 | `$kiro-spec-design <feature> -y` | tasksへ進む確認 |
| 4 タスク | `$kiro-spec-tasks <feature> -y` | 最終sanity review |

各phaseで対応するSKILL.mdを読んで実行し、初期化の命名・template・brief・metadata処理をこのSkillに複製しません。phase完了の進捗を示します。自動モードはstandalone用の次工程案内で停止せず連続実行します。エラー・サイズgate失敗・取消時は現在地と復旧commandを示して停止します。

対話モードも上表の `-y` を渡します。生成されたtasksは追加の承認待ちを設けず承認済みになります。

## 最終sanity review

生成済みrequirements、design、tasksを直接読み、可能ならfresh reviewerへpathと目的だけ渡します。briefは補助contextです。

- 3成果物の整合性、抜けた前提・必須設計作業、 `_Depends:_`／`_Boundary:_`／`(P)` とtask graphの妥当性を確認する。
- 最終task数と境界が `.kiro/steering/spec-sizing.md` に適合することを確認する。
- task-localな不足は1回修正して再reviewする。要件・設計の矛盾は完了とせず該当phaseへ返す。

任意のgap analysisと対話型design validationはこのpipelineでは省略されます。結果は生成file、要件・component・task数、sanity review、承認状態、必要な次の行動をspecの言語で示します。
