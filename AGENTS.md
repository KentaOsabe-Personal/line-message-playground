# プロジェクトの指示

LINE Message Playground は Kiro の spec-driven development を使います。

## 言語と参照先

- ユーザーへの応答は日本語。spec成果物のMarkdownは `spec.json.language` に従う。このリポジトリの共通指示・文書は日本語で記述する。
- core steering は [.kiro/steering/product.md](.kiro/steering/product.md)、[tech.md](.kiro/steering/tech.md)、[structure.md](.kiro/steering/structure.md)。既に読んだ内容は重ねて読み込まない。
- 対象サービスの設計・変更・レビュー前に [frontend/AGENTS.md](frontend/AGENTS.md) または [backend/AGENTS.md](backend/AGENTS.md) を読む。root起点の作業や委譲先にも必要な局所規則を渡す。
- LINE送信、認証・資格情報、Webhook、リッチメニューとそれらの画面・契約を扱う場合は [line-integration.md](.kiro/steering/line-integration.md) を読む。他のcustom steeringは話題が関連する場合に読む。
- 用語を使う設計・要件・API変更では [CONTEXT.md](CONTEXT.md) を参照する。長期的な判断の背景は [docs/adr/](docs/adr/) を確認し、用語・決定の記録には `$domain-modeling` を使う。
- 既存specは `.kiro/specs/`、進捗確認は `$kiro-spec-status [feature]`。共通方針に影響する変更ではsteeringも更新する。

## 開発と承認

- Requirements → Design → Tasks → Implementation の順に人間の承認を得る。`-y`／`--auto` は意図的なfast-trackに限る。
- 単一specの選択・初期化前、およびRequirements・Design・Tasksの確定前に [spec-sizing.md](.kiro/steering/spec-sizing.md) を適用する。fast-trackでも省略しない。
- `SPLIT_REQUIRED` ではフェーズ成果物を書かず `$kiro-discovery` へ戻る。件数を隠すために作業を圧縮しない。明示的な例外の条件もサイズ方針に従う。
- 新しい作業の分解は `$kiro-discovery`、仕様生成の連続実行は `$kiro-spec-quick`／`$kiro-spec-batch`、承認済み実装は `$kiro-impl`。詳細は [workflow.md](.agents/skills/kiro-discovery/references/workflow.md)。
- Skillは `.agents/skills/<name>/SKILL.md`。明示された場合、またはdescriptionが作業に直接合う場合に使う。単なる語句一致で選ばない。
- Skillが委譲を要求する場合は独立した範囲を渡す。調査の並列化を優先し、並列編集は所有ファイルが重ならない場合に限る。

## Gitと秘密情報

- 無関係なユーザー変更を勝手にstage・commitしない。既存差分を保護する。
- 明示的な依頼なしに破壊的reset／checkout、force push、branch削除、PR mergeを行わない。公開手順は `$create-pr`、stack操作は `$gh-stack` の対象範囲に従う。
- `.env`、実資格情報、秘密鍵、トークンをGit・API response・通常ログへ露出させない。安全なサンプルと実値を区別する。
