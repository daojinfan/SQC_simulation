# 07_1_14 校准候选决策覆盖独立验收矩阵

## 审查范围与结论口径

本文是 `07_1_14_calibration_candidate_decision_override` 的独立验收设计，基于详设、开发流程、
现有候选协议、配置事务、Python API、Web API/UI 测试建立。它不是实现方案，不修改生产代码或既有测试。

**当前结论：GO。** 实现、分层回归和两条已发布 artifact 的隔离端到端均已完成；详细命令、证据和
残余风险见文末“执行记录”与“最终结论”。本节后续的“基线观察”保留实现前状态，用于说明本阶段解决的
原始问题，不代表当前代码状态。

验收时必须在新的临时 configuration root 和独立实验 output root 执行。不得修改或重写已发布实验
目录来制造成功路径；artifact 篡改仅可用于拒绝路径。每个改变存储状态的成功用例都要重新构造
`PlatformConfigurationStore`，从磁盘断言结果，而非复用内存对象。

## 基线观察

| 观察项 | 当前位置 | 本阶段验收含义 |
| --- | --- | --- |
| Python 默认会筛选推荐候选，显式选择不推荐候选也会被拒绝。 | `src/sqvm/calibration/api.py` `apply_calibration_candidates_to_current_configuration` | 默认 fail-closed 必须保留；覆盖模式才可改变后半段。 |
| Store 的 current 与 draft 两条路径均在 `_apply_candidate_groups` 拒绝不推荐候选。 | `src/sqvm/web/configuration.py` | 两条路径都必须接收同一规范化 decision，不能只修 current。 |
| Web 仅传入 `eligibleCandidates`，且更新对话框没有覆盖原因。 | `src/sqvm/web/static/app.js` | 不推荐但结构合法的候选必须可见、可选、受额外确认约束。 |
| 事务 idempotency 以完整 request SHA 绑定 `operation_id`。 | `src/sqvm/web/configuration_transactions.py` | decision mode/source/reason/候选选择必须进入 canonical request，不能在 transaction 外补写。 |
| 候选协议会适配旧频谱行，并要求 `recommendation_eligible` 为布尔值。 | `src/sqvm/candidate_protocol.py` | 不得为本阶段给已发布 `calibration_candidate_v1` 增加必填 artifact 字段或改变 artifact hash。 |

## 共同夹具与判定

所有表中用到的候选及存储状态应满足以下最小条件。

| 名称 | 条件 |
| --- | --- |
| `R` | 已验证、非 synthetic、结构合法的推荐候选，`recommendation_eligible=true`。 |
| `N` | 与 `R` 来自同一类已注册 verifier、同样证据完整但质量策略结果为 false 的候选。其 artifact 必须由真实 workflow/测试专用受验证 runner 生成，不能事后编辑 workflow。 |
| `M` | 一次请求选择 `R` 与 `N`，参数路径互不重复。 |
| `S` | 已验证但 stale 的候选：其 `current_value` 与当前 editable 值不相等。 |
| `X` | synthetic demo 候选。 |
| `T` | artifact、receipt、manifest、dataset SHA 或 workflow verifier 绑定遭篡改的候选。 |
| `C0` | 已初始化、有效并激活的 current；记录 old content SHA、revision、active snapshot ID 和 audit 数量。 |

除明确验证 UI 文案的用例外，断言 machine-readable code，不依赖英文或中文错误消息。失败用例必须证明
`current` content SHA/revision、active pointer、snapshot 集合、transaction generation、audit 条目均保持
`C0` 状态。成功用例必须证明候选组内的全部 change 同时生效。

## A. 推荐默认与显式决策

| ID | 输入/动作 | 预期结果与最小断言 |
| --- | --- | --- |
| DEC-01 | Python 旧签名，只传确认短语，候选集含 `R`、`N`。 | 等价于 `recommended_only + automation`；仅 `R` 写入，`N` 不写入；provenance/audit 的 decision 明确保存默认值。 |
| DEC-02 | Python 默认 mode，显式 `candidate_ids=[R]`。 | 成功；行为与 DEC-01 对 `R` 的结果一致。 |
| DEC-03 | Python 默认 mode，显式 `candidate_ids=[N]`。 | 422 `candidate_not_recommended`；不得以“选择无效”掩盖推荐拒绝。 |
| DEC-04 | Python 默认 mode 以 `targets` 覆盖到 `N`。 | 422 `candidate_not_recommended`；targets 不能成为绕过通道。 |
| DEC-05 | Python `override_recommendation + notebook_user + [N] + 非空 reason`。 | 成功；`N` 写入，`overrode_recommendation=true`，推荐结论仍为 false。 |
| DEC-06 | Python `override_recommendation + ai_assisted + [N] + 非空 reason`。 | 成功；审计/provenance 保存 source `ai_assisted` 和原样 reason。 |
| DEC-07 | Python `override_recommendation + web_user + [R]`。 | 成功；`overrode_recommendation=false`，不能因 mode 名称而错误标记已覆盖。 |
| DEC-08 | Python `override_recommendation + web_user + [M]`。 | 成功并原子写入两个候选；操作整体记录 override，且 `overrode_recommendation=true`。 |
| DEC-09 | Python `recommended_only + [M]`。 | 422 `candidate_not_recommended`；不得静默滤除 `N` 后部分成功。 |
| DEC-10 | compatibility spectroscopy wrapper 未传 decision。 | 仍仅应用推荐候选，保持原确认短语与返回类型；wrapper 不得意外开放覆盖参数。 |
| DEC-11 | current 与 draft 分别提交 DEC-05 所对应决策。 | 两条路径均接受相同决策规则并保存带 decision/recommendation snapshot 的来源记录；draft 在单次 Store 调用内原子应用候选组，但首版不接受 operation ID 或跨请求幂等承诺。 |

## B. 决策输入校验与错误合同

| ID | 输入/动作 | 预期 code |
| --- | --- | --- |
| VAL-01 | mode 不在 `recommended_only`、`override_recommendation`。 | 422 `candidate_decision_invalid` |
| VAL-02 | source 不在四个枚举值。 | 422 `candidate_decision_invalid` |
| VAL-03 | `automation + override_recommendation`。 | 422 `candidate_override_source_invalid` |
| VAL-04 | override 缺 `candidate_ids`、为空列表，或仅提供 `targets`。 | 422 `candidate_override_selection_required` |
| VAL-05 | override 的 candidate IDs 重复、未知，或同时提交 `candidate_ids` 与 `targets`。 | 422 `candidate_update_invalid` |
| VAL-06 | override 的 reason 缺失、空字符串、或首尾空白去除后为空。 | 422 `candidate_override_reason_required` |
| VAL-07 | reason 含 Unicode General Category `Cc` 字符。 | 422 `candidate_decision_invalid`；不得过滤后提交。 |
| VAL-08 | reason 恰为 2048 或 2049 个 Unicode code point。 | 前者可接受并保存去首尾空白后的值；后者 422 `candidate_decision_invalid`，不得截断。 |
| VAL-09 | `recommended_only` 携带非空 reason。 | 成功；规范化 reason 写入 decision/audit，且 `overrode_recommendation=false`。 |
| VAL-10 | confirmation phrase、actor、operation ID 缺失/格式错误。 | Python 的 `CalibrationExperimentError.code`、Store 的 `ConfigurationManagementError.code` 和 Web JSON `code` 均稳定；decision/selection/schema 类为 422，stale/current/idempotency conflict 为 409。 |
| VAL-11 | Web API 伪造 `overrode_recommendation=true`，或只靠前端按钮推断 mode/source。 | 服务端忽略派生字段并重新规范化；不完整/伪造 decision 422，不产生写入。 |

## C. 不可绕过边界

每项均以合法 override payload 执行，证明覆盖只改变推荐门而不改变证据和配置安全门。

| ID | 输入/动作 | 预期 code 与状态 |
| --- | --- | --- |
| BND-01 | `X` synthetic demo 覆盖。 | 422 `candidate_update_invalid`；Web 不显示更新入口。 |
| BND-02 | `T` 的任一 artifact/receipt/manifest/dataset/hash 篡改。 | `candidate_update_invalid`；在候选选择前 fail closed。 |
| BND-03 | workflow 没有注册 candidate verifier。 | `candidate_update_invalid`。 |
| BND-04 | `S`，或在读取 `C0` 后由另一提交改变 current content SHA。 | 409 `candidate_stale` 或 `candidate_configuration_conflict`，并保留第二次提交的状态。 |
| BND-05 | change 的 parameter path 不可写、值类型不符、resource owner/type/id 不匹配。 | 422 `candidate_update_invalid`；无参数写入。 |
| BND-06 | 两个候选有同一路径，或候选组内 one change stale。 | 422/409 稳定 code；全组零写入、零 snapshot、零 audit success event。 |
| BND-07 | 候选值会导致 PlatformConfiguration Schema/物理配置校验失败。 | 422 `candidate_update_invalid`；当前、active、snapshot 不变。 |
| BND-08 | candidate ID、targets、resource 与 workflow 声明不匹配。 | 422 `candidate_update_invalid`；不能通过 client candidate payload 扩大更新范围。 |
| BND-09 | 覆盖成功后读取原 workflow/candidate artifact。 | 原 `recommendation_eligible`、reason、artifact SHA 全部不变；不得产生“追认推荐”。 |

## D. 事务、并发与幂等

| ID | 输入/动作 | 预期结果 |
| --- | --- | --- |
| TX-01 | DEC-05 带 UUID4 operation ID 首次提交。 | 仅增加一个 generation；receipt request/response、source-candidate、audit、snapshot、active pointer 为同一 committed bundle/generation。 |
| TX-02 | TX-01 相同 operation ID 与逐字等价 canonical request 重放。 | 返回首次 receipt/response；不执行 transform，不新增 revision、snapshot、audit 或 generation。 |
| TX-03 | 同 operation ID，仅改 mode、source、reason、candidate IDs、targets、expected SHA 任一字段。 | 409 `idempotency_conflict`；该测试须逐字段参数化。 |
| TX-04 | 同 operation ID，reason 仅首尾空白不同；再以不同非空 code point 提交。 | 前者在 reason 去首尾空白后为同一 canonical request，重放 TX-02；后者 409 `idempotency_conflict`。 |
| TX-05 | 两个操作使用同一 `C0` hash：一个 `R`，一个 `N` override，并发提交。 | 至多一个成功；另一个稳定返回 stale/conflict，不能丢失或合并更新。 |
| TX-06 | 在准备 bundle、切 head 前失败；在 head 后 projection 失败。 | 前者旧 generation 全可读；后者新 generation committed、projection pending 可恢复。重复请求依旧满足 TX-02。 |
| TX-07 | 覆盖多参数 `M` 在 schema/resource/change 中途触发失败。 | 无部分 editable 写入；无 partial provenance/audit/snapshot/active。 |

## E. 审计、来源与生命周期

对 DEC-05、DEC-06、DEC-07、DEC-08 分别检查 current、自动 snapshot、active pointer、transaction receipt 和
audit event；重启 Store 后重复检查。

| ID | 必须断言 |
| --- | --- |
| AUD-01 | 新 source-candidate 同时保存 `experiment_run_id`、`recommendation_id`、选择的 candidate IDs、targets、old/new content SHA、完整 decision 与每个候选的 recommendation snapshot；仅接受冻结的新严格键集合。 |
| AUD-02 | audit event 包含与 source-candidate 相同的 decision、候选 recommendation 状态、前后 SHA；actor 与 source 不互相替代。 |
| AUD-03 | `overrode_recommendation` 由所选候选计算：仅 R 为 false；含任一 N 为 true；客户端字段不能覆盖该值。 |
| AUD-04 | 覆盖模式选择 R 时，mode/reason 可以保留但 `overrode_recommendation=false`；不会伪称推荐被覆盖。 |
| AUD-05 | 成功后 current revision/content SHA、snapshot editable/content SHA、active snapshot ID、transaction receipt response 彼此一致；`requires_requalification` 保持现有规则。 |
| AUD-06 | 后续人工修改只保留仍与 editable 相等的 candidate provenance；保留记录不得丢失原 decision/recommendation snapshot。 |
| AUD-07 | 审计或 provenance 写入失败不能留下已激活但不可审计的覆盖更新；按事务边界恢复到 old 或完整 new generation。 |

## F. Web API 与交互验收

| ID | 操作 | 可观察验收 |
| --- | --- | --- |
| WEB-01 | 打开含 `N` 的已验证、非 synthetic 实验。 | 候选显示“不推荐，可人工确认”，显示 candidate reason 与失败质量门；不显示“不可更新”。 |
| WEB-02 | 打开 `R`。 | 显示“推荐更新”；原有普通确认和默认提交路径可用。 |
| WEB-03 | 打开 `X` 或结构/证据无效候选。 | 无更新入口，且原因与“不推荐”视觉/语义可区分。 |
| WEB-04 | 只选 R 后打开对话框并提交。 | 对话框只要求普通确认；POST 带 `recommended_only`、`web_user`、候选 IDs、current SHA、confirmation phrase 和 UUID4 operation ID。 |
| WEB-05 | 选择 N，或由 R 改为 R+N。 | 警告立即出现，展示 N 的 reason/gates；提交文案为“仍然更新”；必须勾选理解复选框并填写有效 reason。 |
| WEB-06 | WEB-05 未勾选、reason 空白、改回只选 R。 | 前两者按钮禁用或本地阻止，且服务端仍会拒绝；改回 R 后覆盖控件/强制字段消失，提交回默认 mode。 |
| WEB-07 | 直接构造 POST `/apply-current` 和 `/draft` 绕过 JS。 | 服务端执行 VAL/BND 全部规则；两条端点均不信任 UI；draft 不接受 operation ID 或暗示跨请求幂等。 |
| WEB-08 | 成功后刷新 current 页面和 management read model。 | 新 revision/SHA、source candidate、snapshot/active 状态可见；页面不保留旧 hash 供下一次写入。 |
| WEB-09 | 在窄屏与桌面选择多个候选、reason 达最大长度。 | 候选、警告、reason 和提交控件不重叠、不截断；可键盘操作。截图或浏览器测试须覆盖两种 viewport。 |

UI 自动化至少包含真实 HTTP round trip，而不应仅以 `app.js` 的字符串断言代替行为测试。

## G. 旧 artifact 与公开接口兼容

| ID | 输入/动作 | 预期结果 |
| --- | --- | --- |
| CMP-01 | 已发布 legacy spectroscopy-shaped candidate（无 `changes`，但含旧频率字段）在默认模式读取/验证/应用。 | 正常适配，默认仅推荐；不写入新的 decision 字段到 artifact。 |
| CMP-02 | 已发布 generic `calibration_candidate_v1` 的 canonical bytes、workflow SHA、receipt SHA。 | 变更前后逐字节与 SHA 相同；read model 仍能列出。 |
| CMP-03 | 老 Python 调用、老 Web payload 均不带 decision。 | 语义固定为 `recommended_only + automation`；老 Web 不可能写入 N。 |
| CMP-04 | legacy wrapper `apply_spectroscopy_candidates_to_current_configuration`。 | 原默认行为和类型保持；覆盖能力只在通用公开 API 的显式新参数上提供。 |
| CMP-05 | 旧 artifact 中 N 通过新 UI/API 显示并由新 decision 覆盖。 | 不迁移 artifact；新 state 的 provenance/audit 附加 decision，而 artifact 本身不变。 |
| CMP-06 | 重新加载新的 quality policy 后尝试把旧 N 当作 R 应用。 | 仍为 N；除非存在新的、独立发布并经验证的重评估 artifact。 |
| CMP-07 | 读取旧 source-candidate 与新 source-candidate（含未知附加键）。 | 旧严格键集合仍可读；新记录必须有 decision/recommendation snapshot 且只可使用新严格键集合，未知附加键 fail closed。 |

## H. 端到端与回归执行

实现完成后，独立执行者应按此顺序运行，并在本文件的执行记录或开发日志中写明命令、环境、fixture/run ID、
结果和失败原因。

1. 候选协议与 Python API：`pytest -q tests/test_calibration_api.py`，加上 DEC/VAL/BND 的新独立测试。
2. 配置与事务：`pytest -q tests/test_platform_configuration_v02.py tests/test_configuration_transactions.py`，加上 TX/AUD 测试。
3. Web API/UI：`pytest -q tests/test_calibration_web.py tests/test_calibration_web_ui.py`，加上 WEB 测试；UI 行为使用浏览器或等价 DOM/HTTP 集成测试。
4. 校准回归：`pytest -q tests/test_spectroscopy_calibration_workflow.py tests/test_spectroscopy_reader_verifier.py tests/test_rabi_calibration.py tests/test_rabi_storage_lifecycle.py`。
5. 真实已发布、非 synthetic 的候选端到端路径：在新存储根执行一次 `R` 默认更新和一次 `N` 显式覆盖，分别保存 workflow/receipt SHA、old/new current SHA、transaction ID、snapshot ID 和 audit event ID。`N` 必须由生产 artifact writer 与已注册 verifier 生成；允许确定性模型仿真和未通过的版本化质量策略，不允许手改 workflow 或 candidate JSON。

## 已冻结契约断言

详设第 17 节已冻结此前记录的 7 个问题。本矩阵以以下规则验收，而不再保留实现者自由裁量。

1. 覆盖选择缺失/空列表为 `candidate_override_selection_required`；重复/未知 ID 或 IDs 与 targets 并用为 `candidate_update_invalid`。
2. reason 先 trim，再按 Unicode code point 限制为 2048，并拒绝 General Category `Cc`；Web 预检查必须与服务端语义一致。
3. `recommended_only` 的非空 reason 合法、规范化并审计，但绝不使 `overrode_recommendation` 为 true。
4. current 把完整规范化 decision 纳入事务 request SHA；draft 是单次 Store 调用的原子候选组应用，不提供 operation ID 或跨请求幂等。
5. Python、Store、Web 分别通过 `.code`、`.code` 和 JSON `code` 暴露稳定错误；decision/selection/schema 为 HTTP 422，stale/current/idempotency conflict 为 HTTP 409。
6. 旧 source-candidate 按旧严格键集合读取；新记录必须带 decision/recommendation snapshot 并按新严格键集合验证，拒绝未知附加键。
7. `N` 使用生产 artifact writer 与已注册 verifier 产生，可由确定性模型仿真与未通过的版本化质量策略驱动，禁止通过编辑已发布 artifact 制造。

## 执行记录（2026-07-29）

本轮由独立验收上下文完成。所有写回均在 `tmp/` 下的全新 configuration root 内进行；每次仅复制
artifact 绑定的父 snapshot，并在结束时删除该临时根。未修改 `output/platform-configurations`、任何实验目录或
生产代码。

| 范围 | 命令/方法 | 结果 |
| --- | --- | --- |
| 候选 API、PlatformConfiguration、事务 | `pytest -q tests/test_calibration_api.py tests/test_platform_configuration_v02.py tests/test_configuration_transactions.py` | `60 passed` |
| Web API/UI | `pytest -q tests/test_calibration_web.py tests/test_calibration_web_ui.py` | `35 passed` |
| references | `pytest -q tests/test_experiment_storage_references.py` | `22 passed` |
| retention 回归 | `pytest -q tests/test_platform_configuration_v02.py -k candidate_provenance_retention_filters_recommendation_snapshot` | `1 passed, 33 deselected` |
| Rabi 校准与 runtime | `pytest -q tests/test_rabi_calibration.py`；`pytest -q tests/test_rabi_runtime_adapter.py` | `10 passed`；`8 passed` |
| Rabi storage/evidence | `pytest -q tests/test_rabi_storage_lifecycle.py -k "reader_fails_closed or legacy_no_fit or corrupted_rabi_staging"`；archive/restore 单例 | `11 passed, 1 deselected`；`1 passed, 11 deselected`（115.64 s） |
| 频谱 | `pytest -q tests/test_spectroscopy_calibration_workflow.py tests/test_spectroscopy_reader_verifier.py` | `22 passed` |
| Notebook | `pytest -q tests/test_user_notebooks.py` | `3 passed` |

审查中曾发现 `_retained_candidate_source` 在后续人工编辑只保留候选子集时，可能让
`recommendation_snapshot` 与 `candidate_ids` 脱节，且 `overrode_recommendation` 不再由保留候选计算。
PM 已在本轮中修复为同步裁剪 snapshot 并重算派生值；上述 retention 专项独立重跑通过。除该已修复问题外，
对候选协议、Python API、Store/transaction、Web server/UI、references 的 07_1_14 diff 审查未发现未解决的
行为、安全边界、审计、幂等或兼容性问题。

### 已发布 Artifact 端到端证据

两个用例均先验证 immutable workflow/receipt，再建立与 workflow `parent_configuration` 绑定的临时 current；
candidate `current_value` 未被手工改写。成功后重新打开 Store 和 transaction view，检查 current、snapshot
sidecar source-candidate、active pointer、audit、receipt request SHA 和 reference graph。两个 artifact 的
workflow/receipt SHA 在写回前后相同，reference graph 均为 `scan_incomplete=false`，并分别提供
`active_snapshot`、`applied_audit`、`current_configuration`、`snapshot_configuration` 四类引用边。

| 模式 | 已发布 run 与 artifact | 配置事务及写回证据 |
| --- | --- | --- |
| `recommended_only + automation` | run `3df4ccf6-6817-481c-82ee-80a02dca3e5c`，`output/experiments/qubit_spectroscopy_79f596318a674aac992f91c0b328f779`；workflow SHA `9B7E7D6C1DA09B9595DB8E3CDB2D5B63BE188EDD299AA8D41E5BCF7B0861E3B8`；receipt SHA `1F5F33F8637FDCA4BD6FDC1105C9ACE790ED31C37CE0FB3BC58A8C5CFE339986` | old/new current SHA `7D792CB38007B2CDC403B1CC4EA7D69245AB490E28A0A711354E11E4A2FF429E` / `5F6333B9E236BD2B3B98FFBE3B153AFF00938412151EE2A9EDCF607579738374`；transaction `ed1dc071-eb1e-4f0c-af5f-b92cc2791294` generation `1`；snapshot/active `617493f0-beeb-4070-8785-35ad9e31fca9`；audit `709a17ff-2722-4925-8f8d-fc9c52bc592e`；candidate IDs `Q1.reference_frequency_GHz`, `Q2.reference_frequency_GHz`；`overrode_recommendation=false`。 |
| `override_recommendation + notebook_user` | run `f447aa97-c484-4ccf-8e2f-212a8efc0a08`，`output/experiments/qubit_rabi_f447aa97c4844ccf8e2f212a8efc0a08`；workflow SHA `4DE48A90923703DA7EE3193C726AD58F2AB6C8F0E193D46E0B0CD0D5E32F9C63`；receipt SHA `DA2F475D379125566A21181A0774FF70B21E379A0C1F9050272EA124F4BE8F2A` | old/new current SHA `A4A38BCFFAC67F5EABA43C8AAB2E17A0A5564026DCBD80C0A47FC197919168D2` / `E9A79A9E2F4F7A64AD71A5193E069D43A618EA48906D26AD0C81CA00E53F023A`；transaction `3c0ff052-0beb-420e-9e52-acea463fa801` generation `1`；snapshot/active `fad893eb-7084-4b7a-98c6-04b3146ac7de`；audit `7d612f19-cef5-4574-85da-d4d374d9b39d`；candidate `Q1:xy2_amplitude:C370E944544362CE` 写入 `0.19180102850420075 GHz`；`overrode_recommendation=true`，reason 已存入 provenance/audit。 |

## 最终结论

**GO。** 本矩阵要求的默认推荐、显式 `N` 覆盖、不可绕过边界、错误码、事务 request SHA 幂等绑定、
source-candidate/audit、references 严格兼容、Web 与 Python 路径均已通过独立审查和分层测试。残余风险为
本地真实 Rabi archive/restore 单例耗时约两分钟；它已通过，属于验收时长/资源规划问题而非行为失败。

## 最终放行条件

GO 条件已满足：分层回归通过；R 默认与 N 覆盖的已发布 artifact 端到端证据齐全；artifact bytes/SHA 无变化；
transaction、current、snapshot sidecar、active pointer、audit 和 references 一致。任何后续变更若使绕过边界成功、
出现部分写入或遗漏审计，均应重新判定为 NO-GO。
