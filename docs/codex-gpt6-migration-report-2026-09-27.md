# Codex GPT-6 Migration Report

実施日: 2026-09-27（Asia/Tokyo）。Migration ID: `gpt6-20260927T003825Z`。対象repository: `/Users/osabekenta/myStudy/line-message-playground`。

承認済みの高confidence変更を適用し、Apply／Verifyを完了した。最終構成は **Sol-first / Astra-ready**。通常運用の推奨開始地点は **GPT-6 Sol + medium**、判断そのものが難しい設計・不確実性の高い作業は **GPT-6 Astra** へ上げる。モデル設定ファイルは変更していない。

この報告書は、指定された最後のfilesystem変更であるmodernizerの自己退役の**直前**に確定する。以下のAfter値と最終構成はその移動後の状態を表す。移動後は報告書を含むファイルを更新せず、移動結果・active側不在・バックアップ保持を読み取り確認し、最終応答で確定する。

## Before → After

| 対象 | Before | After |
|---|---:|---:|
| AGENTS.md | 2 | 4（frontend／backendを追加） |
| AGENTS.override.md | 0 | 0 |
| repository Skills | 23 | 21 |
| user authored Skills | 2 | 1（hatch-pet） |
| system Skills | 6 | 6 |
| 対象のactive Skill合計 | 31 | 28 |
| root AGENTS.mdの行数 | 76 | 27 |
| gh-stack入口の行数 | 891 | 31 |
| hatch-pet入口の行数 | 923 | 33 |
| root／user AGENTS + core steeringのbyte数 | 41,783 | 21,173 |
| repository SKILL.md合計byte数 | 203,231 | 59,506 |
| user／system SKILL.md合計byte数 | 158,862 | 62,078 |

byte数の比較は入口文書の読み込み量を示す。referencesへ移した内容を含む全ファイルの削減率ではない。プラグイン提供Skill 18件はmetadataを確認した対象外領域として保持し、plugin cache・system Skill・アプリケーションsourceは変更していない。

## 適用した分類と理由

| 対象・指示 | 分類 | 理由・保持した固有価値 | Confidence |
|---|---|---|---|
| user AGENTS | THIN | 独立tool呼出しのbatch化というユーザー方針を短く保持 | HIGH |
| root AGENTS | THIN | 言語、承認、サイズgate、安全、参照先に絞る | HIGH |
| rootの工程一覧 | MOVE_TO_SKILL | Kiro工程選択時にだけ必要。discovery/references/workflow.mdへ移動 | HIGH |
| create-prの共通Git安全規則 | MOVE_TO_AGENTS | 公開Skillが選ばれなくても既存差分・秘密・破壊操作の制約が必要 | HIGH |
| domain-modelingの用語参照先 | MOVE_TO_AGENTS | 通常設計でもCONTEXTとADRへ到達する必要がある。記録作業のみSkillへ残す | HIGH |
| tech／structureのサービス固有規則 | LOCALIZE | frontend／backendのAGENTSへ配置し、root起点の設計・委譲にもroutingを追加 | HIGH |
| LINE詳細契約 | LOCALIZE | LINE作業に限定したline-integration.mdへ移動。送信・認証・Webhook・rich menuの契約を保持 | HIGH |
| kiro-steering-custom | MERGE | custom作成はsteering保守の1モード。独立Skillに固有の実行scriptはなく、template選択と保護規則を参照へ保持 | HIGH |
| kiro-verify-completion | MERGE | 実装受入・統合判定が共有する証拠契約。claim種別と3状態をcompletion-gate.mdへ保持 | HIGH |
| publish-history.md | DELETE | 他repositoryの履歴で、現repositoryの固有規則ではないことを確認。実際の公開条件はcreate-prへ保持 | HIGH |
| 汎用思考の説明・重複template手順 | DELETE | 独自のscript、schema、project知識、承認条件がない反復を除去。判定契約と停止条件は保持 | HIGH |
| gpt6-codex-modernizer | DELETE（移動による退役） | 一回限りの移行用Skill。通常運用に固有価値を持たず、明示された最終操作として退役 | HIGH |
| hatch-pet | THIN | v2生成、9行＋16方向、決定的処理、独立QAに固有価値。手順とworker promptを分離 | HIGH |

### 残るrepository Skills

| Skill | 分類 | 変更内容 |
|---|---|---|
| [caveman](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/caveman/SKILL.md) | THIN | 明示的な文体指定へtriggerを絞り、6モードと終了条件を保持。一般的な短文化依頼では自動選択しない。 |
| [create-pr](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/create-pr/SKILL.md) | THIN | このrepoのdevelop→main、CLI認証、account、安全確認、PR形式を保持。別repoの履歴を退役。 |
| [domain-modeling](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/domain-modeling/SKILL.md) | THIN | 用語・ADRを変更する場面へ限定し、CONTEXT_FORMAT.md／ADR_FORMAT.mdの実在pathへ修正。 |
| [gh-stack](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/gh-stack/SKILL.md) | THIN | 非対話flag、部分push、conflict、exit code、merge queue契約を参照へ分離。 |
| [kiro-debug](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-debug/SKILL.md) | THIN | 原因分類、retry／block／停止の出力契約と境界を残し、汎用的な思考の説明を整理。 |
| [kiro-discovery](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-discovery/SKILL.md) | THIN | A〜Eの分岐、brief／roadmap schema、サイズgateを残し、工程一覧を参照へ分離。 |
| [kiro-impl](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-impl/SKILL.md) | THIN | 実装・review単位・承認・bounded remediation・commit規則を保持。単独major leafに対応し、完了gateを共通化。 |
| [kiro-review](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-review/SKILL.md) | THIN | 必須検証・境界・RED証拠・severity・構造化判定を正本化し、templateの重複を除去。 |
| [kiro-spec-batch](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-batch/SKILL.md) | THIN | 依存waveとcross-spec reviewを保持。20件の旧基準を正本のサイズ方針へ統一。UI metadata形式を修正。 |
| [kiro-spec-design](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-design/SKILL.md) | THIN | 調査の深さを作業別に選択し、設計契約・File Structure Plan・サイズgate・metadataを保持。 |
| [kiro-spec-quick](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-quick/SKILL.md) | THIN | 各phaseのSkillへ処理を委譲し、重複した初期化手順を除去。既存の-y意味を保持。 |
| [kiro-spec-requirements](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-requirements/SKILL.md) | THIN | EARS・数値ID・境界・review gate・承認状態を保持し、重複説明を整理。 |
| [kiro-spec-tasks](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-tasks/SKILL.md) | THIN | leaf task、依存、境界、optional test、サイズgateを保持。30〜39件の独立review必須を明確化。 |
| [kiro-steering](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-steering/SKILL.md) | THIN | bootstrap／sync／customを一つに統合し、通常実装と全steering同期の適用範囲を区別。 |
| [kiro-validate-design](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-validate-design/SKILL.md) | THIN | 任意の生成後reviewと生成時gateを区別。最大3件の重要指摘制限は保留判断に従い保持。 |
| [kiro-validate-gap](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-validate-gap/SKILL.md) | THIN | 既存資産・不足・不明点・選択肢・researchへの追記を保持。 |
| [kiro-validate-impl](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-validate-impl/SKILL.md) | THIN | feature統合、full suite、runtime smoke、網羅性、ownershipを保持。未検証をGOにしない共有gateへ接続。 |
| [grilling](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/grilling/SKILL.md) | KEEP | 固有の対話／初期化／状態確認の契約を保持。内容は変更しない。 |
| [grill-with-docs](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/grill-with-docs/SKILL.md) | KEEP | 統合の判断をMANUAL_REVIEWに残し、内容を変更しない。 |
| [kiro-spec-init](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-init/SKILL.md) | KEEP | 固有の対話／初期化／状態確認の契約を保持。内容は変更しない。 |
| [kiro-spec-status](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-status/SKILL.md) | KEEP | 固有の対話／初期化／状態確認の契約を保持。内容は変更しない。 |

全17件のTHINはHIGH confidenceの適用。user側は [hatch-pet](/Users/osabekenta/.codex/skills/hatch-pet/SKILL.md) を保持し、system側は `imagegen`, `openai-docs`, `plugin-creator`, `review-agent`, `skill-creator`, `skill-installer` の6件をKEEPした。

## 統合・移動した指示

| 元の場所 | 現在の正本 | 保持した条件 |
|---|---|---|
| rootのMinimal Workflow／Skills Structure | [Kiro workflow](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-discovery/references/workflow.md) | 工程の順序・承認・各入口 |
| kiro-steering-custom | [custom steering](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-steering/references/custom-steering.md) | `$kiro-steering custom <topic>`、既存template、秘密を残さない条件 |
| kiro-verify-completion | [completion gate](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-impl/references/completion-gate.md) | TASK／FIX／TEST_OR_BUILD／FEATURE_GO、証拠と未検証状態 |
| implのreviewer／debugger template内の重複規則 | kiro-review／kiro-debugのSKILL.md | exactな出力field・severity・retry／block／stop。templateは入力と所有範囲を渡す |
| tech／structureのFrontend規則 | [Frontend AGENTS](/Users/osabekenta/myStudy/line-message-playground/frontend/AGENTS.md) | 型、route寿命、画面外先読み禁止、UI、命名、import、test |
| tech／structureのBackend規則 | [Backend AGENTS](/Users/osabekenta/myStudy/line-message-playground/backend/AGENTS.md) | app責務、typed contract、API、命名、import、test、安全な境界 |
| techのLINE節 | [LINE integration](/Users/osabekenta/myStudy/line-message-playground/.kiro/steering/line-integration.md) | 本文の契約が移動前と一致することを検証 |
| gh-stackのCLI詳細と例 | references/commands.md、references/workflows.md | non-interactive、部分成功、recovery、queue、exit code |
| hatch-petの生成・QA・委譲詳細 | references/workflow.md、references/look-direction-qa.md、templates/worker-prompts.md | v2 geometry、全状態、direction gate、minor/major区分、包装条件 |

hatch-petでは旧モデル名の例を、利用可能で作業に十分なGPT-6 Sol + mediumへ変更した。17 scriptsと6 test filesはSHA-256一致で保持した。qa-rubricの方向schemaは正本の`verdict`へ揃え、修復jobのcomplete判定と完全look行での修復を一貫させた。CLI・画像生成・公開の実操作はこの移行では行っていない。

## 変更ファイル

既存ファイルの更新は30件、新規の指示・参照は11件。この報告書とバックアップ／検証記録は別途作成。active側からの移動は、2 Skillの5ファイル＋公開履歴1ファイル＋最後のmodernizer 1ファイル。

### 更新した既存ファイル

- [/Users/osabekenta/.codex/AGENTS.md](/Users/osabekenta/.codex/AGENTS.md)
- [/Users/osabekenta/.codex/skills/hatch-pet/SKILL.md](/Users/osabekenta/.codex/skills/hatch-pet/SKILL.md)
- [/Users/osabekenta/.codex/skills/hatch-pet/references/qa-rubric.md](/Users/osabekenta/.codex/skills/hatch-pet/references/qa-rubric.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/caveman/README.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/caveman/README.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/caveman/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/caveman/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/create-pr/SKILL.ja.review.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/create-pr/SKILL.ja.review.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/create-pr/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/create-pr/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/domain-modeling/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/domain-modeling/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/gh-stack/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/gh-stack/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-debug/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-debug/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-discovery/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-discovery/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-impl/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-impl/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-impl/templates/debugger-prompt.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-impl/templates/debugger-prompt.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-impl/templates/implementer-prompt.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-impl/templates/implementer-prompt.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-impl/templates/reviewer-prompt.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-impl/templates/reviewer-prompt.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-review/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-review/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-batch/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-batch/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-batch/agents/openai.yaml](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-batch/agents/openai.yaml)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-design/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-design/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-quick/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-quick/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-requirements/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-requirements/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-tasks/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-spec-tasks/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-steering/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-steering/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-steering/rules/steering-principles.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-steering/rules/steering-principles.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-validate-design/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-validate-design/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-validate-gap/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-validate-gap/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-validate-impl/SKILL.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-validate-impl/SKILL.md)
- [/Users/osabekenta/myStudy/line-message-playground/.kiro/steering/structure.md](/Users/osabekenta/myStudy/line-message-playground/.kiro/steering/structure.md)
- [/Users/osabekenta/myStudy/line-message-playground/.kiro/steering/tech.md](/Users/osabekenta/myStudy/line-message-playground/.kiro/steering/tech.md)
- [/Users/osabekenta/myStudy/line-message-playground/AGENTS.md](/Users/osabekenta/myStudy/line-message-playground/AGENTS.md)

### 新規ファイル

- [/Users/osabekenta/.codex/skills/hatch-pet/references/look-direction-qa.md](/Users/osabekenta/.codex/skills/hatch-pet/references/look-direction-qa.md)
- [/Users/osabekenta/.codex/skills/hatch-pet/references/workflow.md](/Users/osabekenta/.codex/skills/hatch-pet/references/workflow.md)
- [/Users/osabekenta/.codex/skills/hatch-pet/templates/worker-prompts.md](/Users/osabekenta/.codex/skills/hatch-pet/templates/worker-prompts.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/gh-stack/references/commands.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/gh-stack/references/commands.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/gh-stack/references/workflows.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/gh-stack/references/workflows.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-discovery/references/workflow.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-discovery/references/workflow.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-impl/references/completion-gate.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-impl/references/completion-gate.md)
- [/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-steering/references/custom-steering.md](/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-steering/references/custom-steering.md)
- [/Users/osabekenta/myStudy/line-message-playground/.kiro/steering/line-integration.md](/Users/osabekenta/myStudy/line-message-playground/.kiro/steering/line-integration.md)
- [/Users/osabekenta/myStudy/line-message-playground/backend/AGENTS.md](/Users/osabekenta/myStudy/line-message-playground/backend/AGENTS.md)
- [/Users/osabekenta/myStudy/line-message-playground/docs/codex-gpt6-migration-report-2026-09-27.md](/Users/osabekenta/myStudy/line-message-playground/docs/codex-gpt6-migration-report-2026-09-27.md)
- [/Users/osabekenta/myStudy/line-message-playground/frontend/AGENTS.md](/Users/osabekenta/myStudy/line-message-playground/frontend/AGENTS.md)

### active側から移動したファイルと退役Skill

| 元の場所 | 移動先 | 状態 |
|---|---|---|
| `/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-steering-custom` | [/Users/osabekenta/myStudy/line-message-playground/.migration-backups/gpt6-20260927T003825Z/retired/kiro-steering-custom](/Users/osabekenta/myStudy/line-message-playground/.migration-backups/gpt6-20260927T003825Z/retired/kiro-steering-custom) | 移動・バックアップ確認済み |
| `/Users/osabekenta/myStudy/line-message-playground/.agents/skills/kiro-verify-completion` | [/Users/osabekenta/myStudy/line-message-playground/.migration-backups/gpt6-20260927T003825Z/retired/kiro-verify-completion](/Users/osabekenta/myStudy/line-message-playground/.migration-backups/gpt6-20260927T003825Z/retired/kiro-verify-completion) | 移動・バックアップ確認済み |
| `/Users/osabekenta/myStudy/line-message-playground/.agents/skills/create-pr/references/publish-history.md` | [/Users/osabekenta/myStudy/line-message-playground/.migration-backups/gpt6-20260927T003825Z/retired/publish-history.md](/Users/osabekenta/myStudy/line-message-playground/.migration-backups/gpt6-20260927T003825Z/retired/publish-history.md) | 移動・バックアップ確認済み |
| `/Users/osabekenta/.codex/skills/gpt6-codex-modernizer` | [/Users/osabekenta/.codex/migration-backups/gpt6-20260927T003825Z/retired/gpt6-codex-modernizer](/Users/osabekenta/.codex/migration-backups/gpt6-20260927T003825Z/retired/gpt6-codex-modernizer) | 本報告確定後の最後のfilesystem変更 |

## Verify

| 検証 | 結果 |
|---|---|
| バックアップ | 元の91ファイルすべてSHA-256一致。modernizer原本コピーを含む |
| YAML | active 29入口（自己退役前）と26 metadataファイルをparse。変更した入口はskill-creator validator合格。既存grill-with-docsの追加keyだけ下記MANUAL_REVIEW |
| Markdown参照 | 67件の実ファイル参照が解決。templateや将来生成するartifact例は除外し、用途を確認 |
| scripts | 対象のPython helper 30本を構文解析。hatch-petの17 scripts＋6 testsのbyte一致、全17 script参照を確認 |
| 同梱test | bundled Pythonでunittest discover実行。28 tests、9.156秒、exit 0 |
| 重複と退役参照 | active Skill名の重複なし。統合した旧Skill名へのactive参照なし |
| Kiro契約の机上実行 | 単独major leaf、子task、34件のspec、runtime smoke不能、quickの承認状態を独立review。発見した30〜39件の独立review必須条件を修正し再確認 |
| pet契約の机上実行 | 既存8x9、row9修復、中間方向の曖昧判定から包装まで独立review。complete timing／完全行修復／schemaを正本へ統一 |
| 適用範囲 | rootから局所AGENTSとLINE詳細へ到達。service固有規則が設計時にも読まれるroutingを確認 |
| 保護した領域 | product、spec-sizing、roadmap、spec-reviewer.toml、system Skills、Kiro rules/templatesの非対象ファイルが基準hashと一致 |
| Git差分 | bundled Gitでdiff --check合格。差分は指示基盤・新規report・バックアップに限定、application source差分なし。commit／pushなし |

実行commandは `PYTHONDONTWRITEBYTECODE=1 <bundled-python> -B -m unittest discover -s /Users/osabekenta/.codex/skills/hatch-pet/tests -v`。検証中にPillowの既存`getdata`非推奨警告が出たが、testは全件合格。scripts自体の更新は行っていない。アプリケーションsourceを変更していないため、アプリのDocker test／runtime起動はこの移行検証には含めない。

### Sol通常運用の情報充足

静的な情報充足レビューはPASS。core3文書にproduct、stack、Docker command、共通安全を残し、frontendではAPI／DTO／State、route寿命、UI、test規約へ、backendではHTTP／service／repository／gateway、typed port、transaction、test規約へ到達できる。LINE作業では認証・所有権・署名・revision・結果不明・自動再試行禁止・外部通信中のlockの契約を参照できる。移動前のLINE本文の一致と、サービス規則の移動対応を確認した。

### Astra使用時の指示

静的な適用範囲レビューはPASS（保留項目を除く）。常時指示はprojectの事実・承認・安全・routingに限定し、一般的な思考順序や全Skill読み込みを要求しない。Skill内の具体的schema、工程gate、独立review、QA制約はそれぞれの作業に限定した。旧モデル固有のworker例、重複したレビュー手順、常時の長いCLI／生成手順を入口から除いた。

Sol／Astraの確認は指示の情報量・適用範囲・契約に対するレビューであり、両モデルを切り替えた性能比較試験ではない。

## MANUAL_REVIEWとして残した項目

| 項目 | 変更案のconfidence | 今回の扱い |
|---|---|---|
| behavioral taskでの一時Feature Flag Protocolの廃止 | LOW | OFF→RED、ON→GREEN、flag除去→再検証を保持 |
| grill-with-docsをgrilling／domain-modelingへ統合 | MEDIUM | 独立wrapperをKEEP |
| cavemanそのものの退役 | LOW | 6モードを保持。明示triggerへの限定だけ適用 |
| batch auto-approvalとquick対話内-yの承認意味 | MEDIUM | 既存の承認状態遷移を保持。rootの人間承認原則との整理は別判断 |
| design reviewの最大3件という指摘数制限の廃止 | MEDIUM | 既存制限を保持 |
| agents/openai.ymlから.yamlへの一括rename | MEDIUM | 既存拡張子を保持 |
| grill-with-docsのdisable-model-invocation key | MEDIUM（互換性判断） | YAML構文は有効。現行quick validatorは追加keyを非対応として警告。未変更の原本hashを確認し、呼出し方針を勝手に変更せず保留 |

これらは承認済み高confidence変更の未実施ではなく、承認案に従って維持した判断待ち事項。既存frontmatter keyの互換性警告は検証で明示し、変更対象のエラーとして隠していない。

## バックアップと復元

- Repository: [/Users/osabekenta/myStudy/line-message-playground/.migration-backups/gpt6-20260927T003825Z](/Users/osabekenta/myStudy/line-message-playground/.migration-backups/gpt6-20260927T003825Z)
- User: [/Users/osabekenta/.codex/migration-backups/gpt6-20260927T003825Z](/Users/osabekenta/.codex/migration-backups/gpt6-20260927T003825Z)
- 各`original/`は変更前コピー。`manifest.json`にsource、相対backup path、bytes、SHA-256を記録。
- 各`retired/`はactive側から移動した実体。modernizerには変更前の`original/skills/gpt6-codex-modernizer/`と最後に移動する`retired/gpt6-codex-modernizer/`の両方を残す。
- Repositoryの`instruction-baseline.json`に180ファイルの移行前hash、`retirement-map.json`に2 Skillと公開履歴の移動対応を保存。
- 復元はmanifestのsourceへoriginalを戻す。新規の11指示ファイルはこの報告の一覧で識別する。復元前に移行後の利用者編集を別途保護し、無関係な変更を上書きしない。
- modernizerのみ戻す場合もbackupを移動・削除せず、`original/skills/gpt6-codex-modernizer/`からuser skillsへコピーする。これは将来の明示的な復元操作として行う。
- バックアップはactive discoveryの`~/.codex/skills/`、repo `.agents/skills/`／`.codex/skills/`の外。repository側のbackupは未追跡であり、この移行ではstage／commitしない。

## 最終構成

```text
~/.codex/
  AGENTS.md                     共通のtool batching方針
  skills/
    .system/                    6 Skillを保持
    hatch-pet/                  薄い入口＋references／templates＋既存scripts／tests
  migration-backups/gpt6-20260927T003825Z/
    original/                   modernizerを含む変更前コピー
    retired/gpt6-codex-modernizer/
repository/
  AGENTS.md                     共通制約とrouting
  frontend/AGENTS.md            Frontend固有規則
  backend/AGENTS.md             Backend固有規則
  .kiro/steering/
    product.md                  KEEP
    tech.md / structure.md      共通の事実と参照先
    line-integration.md         LINE作業時だけの詳細契約
    spec-sizing.md / roadmap.md KEEP
  .agents/skills/               21 Skill、2 Skillを統合
  .codex/agents/spec-reviewer.toml KEEP
  .codex/skills/                存在なし
  docs/codex-gpt6-migration-report-2026-09-27.md
  .migration-backups/gpt6-20260927T003825Z/
```

## 最終操作の契約

ApplyとVerifyは上記の保留警告を記録したうえで正常完了。報告書・検証証跡を保存してhashを固定した後、modernizer原本コピーとactive実体のhash一致を再確認する。

最後のfilesystem変更は次の単一renameとする。完全削除は行わない。

```text
/Users/osabekenta/.codex/skills/gpt6-codex-modernizer
  → /Users/osabekenta/.codex/migration-backups/gpt6-20260927T003825Z/retired/gpt6-codex-modernizer
```

その後は読み取りだけで、移動先と原本コピーの一致、`~/.codex/skills/`・repo `.codex/skills/`・repo `.agents/skills/`にactiveなmodernizerが残っていないことを確認する。移動後のファイル追記、追加cleanup、設定変更は行わない。
