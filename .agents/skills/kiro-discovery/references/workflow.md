# Kiroワークフロー

新しい作業を既存specの更新、直接実装、新規spec、roadmap分割へ振り分ける際に参照します。承認とサイズ判定の正本はrootのAGENTSと `.kiro/steering/spec-sizing.md` です。

| 目的 | 入口 | 成果物・条件 |
|---|---|---|
| 共通知識の作成・同期 | `$kiro-steering` | core steering。custom作成は `$kiro-steering custom <topic>` |
| 分解・範囲決定 | `$kiro-discovery` | brief、必要ならroadmap。次工程は自動実行しない |
| 初期化 | `$kiro-spec-init` | spec.json、requirementsの初期文書 |
| 要件 | `$kiro-spec-requirements` | EARS要件、数値ID、要件review gate |
| 既存との差分調査 | `$kiro-validate-gap` | 任意。researchへ保存 |
| 設計 | `$kiro-spec-design` | requirementsの承認後。design、research |
| 設計再レビュー | `$kiro-validate-design` | 任意。生成済み設計のGO／NO-GO |
| タスク | `$kiro-spec-tasks` | requirements・design承認後。tasksと承認状態 |
| 単一specの連続生成 | `$kiro-spec-quick` | 対話／明示的な `--auto`。サイズgateは必須 |
| roadmapの新規spec一括生成 | `$kiro-spec-batch` | 依存wave順。既存spec更新・直接実装は別扱い |
| 実装 | `$kiro-impl <feature> [tasks]` | 引数なしは自律実装と親review単位commit、タスク指定は手動・自動commitなし |
| 全体の再検証 | `$kiro-validate-impl` | 統合、smoke、網羅性のGO／NO-GO |
| 進捗照会 | `$kiro-spec-status` | 承認・残タスク・依存・blocker |

実装時の単位レビューは `kiro-review`、原因調査は `kiro-debug`、完了証拠の契約は `kiro-impl/references/completion-gate.md` を使います。判定契約と委譲の詳細は `kiro-impl/SKILL.md` にあります。
