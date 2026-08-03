# Research & Design Decisions

## Summary

- **Feature**: `line-rich-menu-admin-lifecycle`
- **Discovery Scope**: Complex Integration（既存foundationのowner UI化とチャネルライフサイクル統合）
- **Key Findings**:
  - `linerichmenus` はtemplate、期限付きpreview、状態、operation、history、headless lifecycle、reference probe、rollback-only history purge、`read_only | recovery_only | enabled` readinessを既に公開している。管理画面はこの契約をstrict DTOで利用し、所有権判定・画像生成・LINE失敗分類を複製しない。
  - 既存`linechannels`の無効化は単一DB transaction内の`is_active`更新であり、外部解除中もactiveを維持する永続的な「無効化要確認」を表現できない。外部I/Oの前後を短いtransactionで囲む`ChannelDeactivationCoordinator`と一件のcurrent stateが必要である。
  - 無効化統合には、foundation内でunlink対象選定とoperation acceptを一体化するatomic reservation、subjectなしのdisable再assessment、channel inactive化とdeactivation完了の単一transactionが必要である。既存headless委譲と二段CASのままでは安全に収束しない。
  - 物理削除のreference probeだけではterminal historyが残る。`DjangoHeadlessReferenceContracts.purge_history`をchannel deleteと同じtransactionへ組み込み、途中失敗時は両方をrollbackする必要がある。

## Research Log

### 要件・サイズ・境界の分析

- **Context**: 12要件、106受入基準を一つの設計で実行可能にできるか確認した。
- **Sources Consulted**: `requirements.md`、`.kiro/steering/spec-sizing.md`、`line-rich-menu-foundation`のdesign/research。
- **Findings**:
  - 独立seamは、編集・preview、管理operationと回復、historyとinactive read、disable/reactivate、deleteとrolloutの5つである。
  - foundation内部を対象外に保てば、1〜3時間単位で34〜39 taskへ収まる。40件未満だがreview-attention帯である。
  - DB statusの再取得はLINE外部再確認ではない。後者は`recheck`の明示operationだけで開始する必要がある。
- **Implications**: file owner、依存順、統合testを設計で固定し、task生成時に独立task-graph sanity reviewを必須とする。

### 既存Backendの統合境界

- **Context**: foundationを再実装せず、公開済み契約で全owner flowを構成できるか調査した。
- **Sources Consulted**: `backend/linerichmenus/{types,services,views,presenters,serializers,headless,container}.py`、`backend/linechannels/{admin_services,admin_types,reference_fence,container}.py`。
- **Findings**:
  - owner APIは`/api/line/rich-menus/`配下にtemplate、preview、state、operation、operation detail、cursor historyを持つ。preview responseだけがconfirmation token、完全URL、base64 imageを返す。
  - `RichMenuLifecyclePort`はguard、unlink、recheckを公開するが、現在のguardは`clear_to_disable | blocked | unavailable`だけで、disable coordinatorが安全にunlink対象を選ぶためのtyped detailを持たない。現行unlinkはcaller由来targetを受け取り、target選定とoperation acceptを同じrich state lockへ束ねるrepository契約もない。
  - reference probeはapplied/processing/unknown/cleanup待ちをblockしterminal history-onlyをblockしない。purgeはtransaction必須かつ失敗時rollback-onlyである。
  - readinessのintegration markerは既に`line-rich-menu-admin-lifecycle-v1`として固定されている。
- `ChannelStateResponse.nextAllowedActions`はdomain状態からの候補で、現状はreadiness適用後のeffective actionではない。`read_only`でも`apply`が見え得るため、modeとeffective actionsの公開projection追加が必要である。
- **Implications**: `headless.py`へ所有権判定を漏らさないdisable assessment/recovery contractを追加し、`services.py`と`repository.py`へtarget選定・ownership再検証・operation acceptのatomic reservationを追加する。coordinatorからfoundation model/repositoryを直接参照しない。

### 既存Frontendの統合境界

- **Context**: チャネルカードから専用管理画面へ遷移し、memory-only入力を安全に扱う方法を確認した。
- **Sources Consulted**: `frontend/src/{App,ChannelAdminConsole,ChannelActions,channelAdminApi,channelAdminDto,channelAdminState,httpApi}.tsx|ts`と対応test。
- **Findings**:
  - routerは未導入で、`OwnerConsole`配下にチャネル一覧と各機能を並べる構成である。新規依存なしのin-memory screen selectionが既存構成に最も小さく適合する。
  - 通信、`unknown` DTO検証、純粋状態遷移、Componentの分離パターンが確立している。
  - session失効は`onSessionInvalid`から上位refreshへ戻せる。draft/previewをWeb StorageやURLへ置く必要はない。
- **Implications**: `RichMenuAdminConsole`、`richMenuAdminApi.ts`、`richMenuAdminDto.ts`、`richMenuAdminState.ts`を同じ責務分離で追加し、route変更や状態管理libraryは採用しない。

### LINE Messaging API契約の再確認

- **Context**: foundation契約に依存するadmin UIが誤った端末表示保証や自動retryを追加しないよう、公式制約を再確認した。
- **Sources Consulted**: LINE Developers Messaging API reference、rich menu guide、rate limit reference（公式URLはReferencesに記載）。
- **Findings**:
  - create、image upload、set default、cancel default、deleteは個別の不可逆または部分完了し得るcallで、rich menu mutationにはretry keyがない。429、5xx、timeoutをUIから自動反復してはならない。
  - imageはJPEG/PNG、2500px幅、特定height、最大1MB等の制約を受けるが、foundation renderer/gatewayが検証済みであるためadminは独自検証を追加しない。
  - defaultの優先順位は利用者別、Messaging API既定、Official Account Manager既定である。変更はchat再open時かつ最大約1分遅延し得るため、API成功を実機即時表示や到達確認として表示できない。
  - default取得の`403`はOfficial Account Manager等の外部既定、`404`はMessaging API既定なしを表し、通常の認可失敗と一括分類できない。
  - 同一Official AccountのAPI資源・rate limitは他toolの利用と共有される。rich menu aliasも外部toolから同一channel資源へ関連付け得るが、alias管理はfoundationの明示的対象外である。
- **Implications**: owner画面は保存operationと観測結果だけを断定し、実機表示保証をしない。alias契約や削除安全性が変わる場合はfoundationを再検証し、このspecでalias gatewayを増設しない。

### Browserのmemory-onlyと離脱確認

- **Context**: 未適用入力とpreview binaryを永続化せず、reload/close時に警告する必要がある。
- **Sources Consulted**: React 19のeffect cleanup、ブラウザ`beforeunload`/object URLの標準挙動、既存Frontend test環境。
- **Findings**:
  - reload/close時にcustom文言は保証できないため、dirty時だけ`beforeunload`を登録し、ブラウザ標準確認を利用する。
  - 「消去」は観測可能な基準としてReact state参照破棄、preview object URLの`URL.revokeObjectURL`、component unmount、cache非使用で定義する。JS heapの物理上書きは保証しない。
  - 手動link testは一件だけ`window.open`し、`noopener,noreferrer`を指定する。preview renderはURLをfetchしない。
- **Implications**: unload/session/unmount cleanupを状態機械とComponent testで検証し、Service Worker、Web Storage、URL queryへdraftを置かない。

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Verdict |
|--------|-------------|-----------|---------------------|---------|
| Foundation API + typed lifecycle coordinator | UIは公開HTTP契約、channel lifecycleはheadless portを使用 | 既存ownership/sagaを再利用、境界が明確 | coordinator状態と統合testが必要 | 採用 |
| Frontend主導の解除後無効化 | UIがrich menu解除とchannel disableを順番に呼ぶ | Backend変更が少ない | reload/session失効/競合で同一操作を保証できない | 却下 |
| `linechannels`からfoundation modelを直接参照 | 同一DBのmodelを照会・削除 | 実装が短い | app境界、所有権、rollback契約を破る | 却下 |
| 新規router/state library | URL routeと共有storeを追加 | 大規模UIには拡張しやすい | draft URL残留と不要な依存・移行 | 却下 |

## Design Decisions

### Decision: foundationを唯一のrich menu状態正本とする

- **Context**: 画面側にもaction matrixと表示stateが必要だが、domain stateを二重実装できない。
- **Alternatives Considered**: Frontend独自状態機械、Backend BFF projection、新APIなしの既存DTO利用。
- **Selected Approach**: 既存state/operation/history DTOをstrictにdecodeし、`nextAllowedActions`とreadiness modeをserver authorityとして描画する。Frontend reducerはloading、dirty、preview validity、dialog、request generationだけを所有する。
- **Rationale**: domain判断のdriftを避け、protocol error時にfail closedにできる。
- **Trade-offs**: DTO parserは大きくなるが、`any`やunsafe castを使わない。

### Decision: 無効化を永続coordinator stateへ一般化する

- **Context**: disableの外部解除、unknown、再認証後resumeを同一owner操作へ収束させる必要がある。
- **Selected Approach**: `ChannelDeactivationState`をchannelごとのcurrent stateとして保存し、`ChannelDeactivationCoordinator`がreserve、foundation assessment/unlink/recovery、final channel updateを短いtransactionの間で進める。保存subjectがある明示recheckだけが元operationをreconcileし、subjectがないexternal default/cleanup/revision競合は新operationを作らず最新assessmentだけを行う。LINE call中はchannel lockを保持しない。
- **Rationale**: active表示を成功確認まで維持し、部分完了を再確認可能にする最小の永続境界である。
- **Trade-offs**: deactivation自体の全履歴台帳は作らず、監査詳細は対応するfoundation operation historyを参照する。

### Decision: disable unlinkの対象確定とoperation予約をfoundation内で原子的にする

- **Context**: headless assessment後にcurrent resource、observation、ownershipが変わると、callerが保持したtargetでunlinkを開始するTOCTOUが生じる。
- **Selected Approach**: callerはtarget resource IDをstart commandへ渡さない。foundation service/repositoryがrich stateをlockし、assessment proof、最新observation、current managed resource、ownership、channel revisionを再検証してtarget選定とoperation acceptを一transactionで行う。外部I/Oはreservation後にlock外で実行し、LINE現在既定が予約targetと異なれば作用前に停止する。
- **Rationale**: rich menu ownershipの正本をfoundation内に保ち、stale assessmentから誤対象へ作用する経路を構造的に閉じる。
- **Trade-offs**: `headless.py`だけでなく`services.py`と`repository.py`の公開内部契約変更、および二接続競合testが必要になる。

### Decision: channel inactive化とdeactivation完了を一回のrevision CASで確定する

- **Context**: 既存channel更新は`updated_at`を変更するため、inactive更新後に旧revisionでdeactivation完了をCASすると必ずstaleになる。
- **Selected Approach**: `linechannels` repositoryの`complete_inactive`が更新前revisionを一度だけ検証し、channel inactive化、新channel revision、deactivation completed化を同じtransactionで保存する。競合時は部分更新せず、旧expected revisionを保持した`confirmation_required: stale_channel`へ記録し、同じ明示recheckだけが最新active revisionへ前進できる。
- **Rationale**: channelとdeactivation projectionの部分成功を防ぎ、既に成功したunlinkを再送せず同じowner intentへ収束できる。
- **Trade-offs**: inactive化は汎用channel serviceではなくdeactivation repositoryの専用atomic commandに限定し、再有効化だけを既存serviceへ委譲する。

### Decision: delete transactionへrollback-only purgeを組み込む

- **Context**: terminal historyだけは削除をblockしないが、channel削除と同じ完了単位で消す必要がある。
- **Selected Approach**: reference directoryへrich menu probeを登録し、unreferenced確認後、同じ`transaction.atomic`内でhistory purge、credential/channel deleteを実行する。
- **Rationale**: DB内の部分削除を構造的に不可能にし、foundationの既存安全契約を採用できる。
- **Trade-offs**: purgeとchannel lock順序を固定した競合testが必要である。

### Decision: rollout capabilityは既存3 modeを採用する

- **Context**: code/schemaの一部だけが配備された状態でmutationを開けない。
- **Selected Approach**: `read_only | recovery_only | enabled`と既存integration markerを採用し、reference probe、history purge、lifecycle coordinatorのcomposition validationが揃うときだけmodeを有効とみなす。
- **Rationale**: custom feature flagや別のmode stateを作らずfoundation guardへ収束できる。
- **Trade-offs**: 設定変更後はstate再取得が必要で、UIは以前のaction可否を保持しない。

### Decision: UIを一つのscreen aggregateと小さなpresentation panelへ単純化する

- **Context**: 編集、状態、回復、履歴が同じchannel contextを共有する。
- **Selected Approach**: screen ownerを`RichMenuAdminConsole`一つにし、Editor、Status/Operation、History panelはtyped propsだけを受ける。追加store/router/BFFは作らない。
- **Rationale**: 現在のflat React構成に適合し、draft cleanup ownerを一箇所に保てる。
- **Trade-offs**: URL直リンクは提供しないが、要求はチャネルカードからの専用画面遷移で満たす。

## Design-stage Spec Size Assessment

- **Verdict**: PASS (single-spec)
- **Projected executable tasks**: 34〜39件（1〜3時間単位）
- **Independent responsibility seams**: 5（編集/preview、operation/recovery、history/inactive read、disable/reactivate、delete/rollout）
- **External/stateful workflows**: LINE preview/state/apply、recheck/cleanup、deactivation unlink、reactivation refresh、atomic deleteの5系統。ただし全て同じchannel owner outcomeへ収束する。
- **File ownership and review order**:
  1. public headless/HTTP contractとDTO fixture
  2. deactivation state/coordinatorとdelete/purge composition
  3. Frontend API/DTO/reducer
  4. screen/panelsとnavigation
  5. rollout、failure injection、security、cross-seam integration
- **Rationale**: atomic unlink reservation、subjectなしreassessment、atomic deactivation completionを独立taskとして明示しても39件以下に収まる。foundationの状態機械、renderer、gateway、ownership判断自体は再実装せず、各seamはtyped contractと明示的integration taskでreview可能である。Tasks段階では30〜39件帯の独立task-graph sanity reviewを再実施する。

## Design Review Gate

- **Verdict**: PASS
- **Mechanical checks**: 全106 numeric acceptance criteria IDをtraceabilityへ収録し、boundary四項目、具体file structure、component ownership、design-stage size verdictにplaceholderまたは欠落なし。
- **Architecture readiness**: owner HTTP、foundation headless、deactivation persistence、atomic purge/delete、readinessのinterfaceと依存方向を固定した。
- **Repair pass 1**: component/file mapping、unlink成功後の再assessment、settings ownershipを明確化した。
- **Repair pass 2**: headless command field、deactivation repository CAS、channel summary、purge resultのclosed contractを具体化した。
- **Validation repair 2026-08-02**: 無効化完了の二段CAS矛盾を単一`complete_inactive`へ変更し、unlink targetのatomic reservation、subjectなしdisable reassessmentのclosed unionを追加した。
- **Spec gap**: 指摘された3件は型、sequence、repository、data invariant、競合testまで反映済み。browser memory消去、status refreshとexternal recheckの区別、aliasの運用前提も設計判断として明示済み。

## Risks & Mitigations

- **disable中のchannel revision競合** — 外部I/O前後でowner/provider/revisionとcoordinator operation IDを再lockする。完了は更新前revisionの一回CASでchannel/deactivationを同時確定し、競合時はactiveと旧expected revisionを維持して同じ明示recheckだけに前進を許可する。
- **assessment後のunlink対象変更** — foundationのrich state lock下でtarget選定とoperation acceptを一体化し、reservation後のLINE現在既定不一致は作用前に停止する。
- **UI action matrixのdrift** — serverの`nextAllowedActions`以外を推測せず、未知enum/余分keyをprotocol errorとして画面全体をfail closedにする。
- **preview/draft残留** — state cleanup、object URL revoke、`Cache-Control: no-store`、storage不使用をcanary testする。
- **deleteとpurgeのlock順序不整合** — channel reference fenceを先に取得する順序を固定し、deadlock/retryable分類とrollback testを追加する。
- **外部tool、alias、per-user menuによる見え方の差** — app外資源を操作せず、端末表示を保証しない。alias契約をscopeへ入れる変更はfoundation再設計のtriggerとする。
- **task数の増加** — 40件以上または2 repair passで境界が収束しない場合は`SPLIT_REQUIRED`へ戻す。

## References

- [LINE Messaging API reference: Rich menus](https://developers.line.biz/en/reference/messaging-api/#rich-menu) — rich menu object、create/upload/default/cancel/delete契約。
- [Rich menus overview](https://developers.line.biz/en/docs/messaging-api/rich-menus-overview/) — default優先順位と端末反映の公式説明。
- [Get default rich menu ID](https://developers.line.biz/en/reference/messaging-api/#get-default-rich-menu-id) — `200/403/404`の観測意味。
- [Upload rich menu image](https://developers.line.biz/en/reference/messaging-api/#upload-rich-menu-image) — image形式、寸法、1MB上限。
- [Messaging API rate limits](https://developers.line.biz/en/reference/messaging-api/#rate-limits) — endpoint別rate limitと429の扱い。
- [Rich menu list rate-limit change](https://developers.line.biz/en/news/2026/05/26/get-rich-menu-list-rate-limit-change/) — 2026-05-26以降のlist 10回/秒。
- [Retry failed API requests](https://developers.line.biz/en/docs/messaging-api/retrying-api-request/) — rich menu mutationがretry key対象外である根拠。
- [Rich menu alias reference](https://developers.line.biz/en/reference/messaging-api/#rich-menu-alias) — aliasの同一channel制約と上限。
- [Stop using the Messaging API](https://developers.line.biz/en/docs/messaging-api/stop-using-messaging-api/) — アプリ内無効化とLINE側停止の区別。
- `.kiro/specs/line-rich-menu-foundation/design.md` — 採用するowner API、headless、purge、readiness契約。
- `.kiro/steering/{product,tech,structure,spec-sizing}.md` — security、type、app境界、サイズゲート。
