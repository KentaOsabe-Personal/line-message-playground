---
name: kiro-spec-batch
description: Generate the new specifications in a Kiro roadmap by dependency wave, then review cross-spec contracts. Requires assessed briefs and preserves the project sizing policy; existing-spec updates and direct implementation are outside batch execution.
---

# Kiro仕様の一括生成

controllerはroadmap、サイズ方針、briefの存在とSpec Size Assessmentを読み、生成を各featureのworkerへ渡します。

## 入力と依存wave

1. `.kiro/steering/roadmap.md` の `## Specs (dependency order)` から新規feature、説明、依存、完了checkboxを読む。`Existing Spec Updates` と `Direct Implementation Candidates` はcontextとして扱い、batchへ混入させない。
2. pending featureの `.kiro/specs/<feature>/brief.md` とサイズ判定が必要。 `.kiro/steering/spec-sizing.md` の現行基準で `PASS (single-spec)` と見積り・境界・根拠が有効か確認する。
3. brief欠落、判定不足、`SPLIT_REQUIRED` はdispatch前に停止してdiscoveryへ返す。循環依存も停止する。
4. 依存完了済みfeatureをwaveへまとめて提示する。前waveの前提が完了するまで後続を開始しない。同一waveは独立featureを並列dispatchし、worker上限を守る。

## feature workerへの契約

brief、roadmap、サイズ方針を読み、以下の各SKILL.mdを正本として実行します。

- `.agents/skills/kiro-spec-init/SKILL.md`
- `.agents/skills/kiro-spec-requirements/SKILL.md`
- `.agents/skills/kiro-spec-design/SKILL.md`
- `.agents/skills/kiro-spec-tasks/SKILL.md`

既存batch契約どおりauto-approveを使い、spec.jsonのapprovalsをtrueにします。これはサイズgateを迂回しません。完了file、実行task数、最終サイズ判定を返します。featureごとに所有specを限定し、他workerの変更を戻しません。

wave終了時にspec.json／requirements／design／tasksの存在と結果を確認します。失敗featureがあっても同じwaveの独立作業は回収します。失敗した前提を持つ後続を成功扱いして開始せず、その依存waveを停止して報告します。multi-agentを使えない場合は同じ依存順で逐次実行します。

## cross-spec review

生成後はfreshな独立reviewerを1つ使います。利用可能なら `.codex/agents/spec-reviewer.toml` の `spec-reviewer` を使い、親modelを継承しreasoning highの既存設定を保持します。

reviewerは生成specと関連する隣接specを直接読み、data model、interface、命名、重複機能、依存完全性、共有基盤、task境界、上流下流の責務、revalidation trigger、現行サイズ方針への適合を確認します。designを中心に、requirementsの受入条件、tasksの境界、roadmap全体との対応を見ます。

Critical／Importantは所有specへ修正を戻し、最大3回再reviewします。分解自体の問題は局所修正で隠さずdiscoveryへ返します。minorは明示します。

## 完了

fileとphase／approvals、サイズ判定とreview結果を確認したspecだけroadmapで `[x]` にします。失敗・blocker・非batch項目を別に報告します。既存 `[x]` またはtasks.mdのあるspecは既存のskip規則を維持します。残存taskや失敗を全完了と報告しません。

結果はfeatureごとの成果物・task数、cross-spec review、残作業を示します。roadmap欠落はdiscoveryへ、失敗featureは `$kiro-spec-quick <feature> --auto` 等の対象復旧へ案内します。
