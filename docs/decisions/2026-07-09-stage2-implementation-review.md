# 阶段 2 实现审查记录

状态：

```text
通过（approved）
```

日期：

```text
2026-07-09
```

审查者：

```text
Claude（审查角色）
```

审查对象（Codex 实现）：

```text
configs/hamiltonians/2q1c_charge_basis.yaml
scripts/run_stage_02_hamiltonian.py
src/sqvm/hamiltonian/（__init__/config/artifacts/capacitance/junction/basis/builder/solver/checks/notebook/verify）
src/sqvm/__main__.py（新增 verify-hamiltonian 子命令）
tests/test_hamiltonian_*.py
output/stage_02_hamiltonian/hamiltonian_artifacts.json + verification.ipynb
```

依据：

```text
docs/designs/02_hamiltonian_design.md
docs/stages/02_hamiltonian_plan.md
docs/decisions/2026-07-09-stage2-hamiltonian-review.md
docs/decisions/2026-07-09-stage2-implementation-acceptance.md
```

## 结论

```text
准予通过阶段 2。
实现正确、物理对、测试全绿、verify_hamiltonian 端到端 ok。
发现的都是小问题（nit），无一阻塞验收；红线项（acceptance 第 J 节）无一命中。
```

## 运行的验证

```text
解释器：C:\Users\fandaojin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
依赖：numpy 2.3.5 / scipy 1.18.0（已在 pyproject 声明）

python -m pytest -q
  结果：38 passed in 22.47s

python -m sqvm verify-hamiltonian configs/hamiltonians/2q1c_charge_basis.yaml \
    --output output/stage_02_hamiltonian
  结果：ok=true，退出码 0；生成 hamiltonian_artifacts.json（11KB）+ verification.ipynb（174KB，含 PNG）
```

## 物理正确性核验（实际产物 vs 手算预期）

```text
量                 预期(手算)              Codex 产物
C_mode 对角        77.025 / 75.8 / 80.075  77.025 / 75.8 / 80.075      吻合
C_mode 非对角      -0.1 / -0.075           -0.1 / -0.075               吻合
E_C 对角 (GHz)     0.251/0.255/0.242       0.2515/0.2555/0.2419        吻合
EJ_eff q1/c/q2     14.81/30.0/16.11        14.809/30.016/16.121        吻合
单模 f01 数值      5.20/7.57/5.34          5.194/7.569/5.332           吻合
单模 f01 vs 解析   差 ~O(Ec)               差 ~0.01 GHz                 吻合
第一组 gaps        ≈5.2/5.3/7.6            5.19/5.33/7.60              吻合
Hilbert 维度       1331                    1331                        吻合
hermiticity error  0                       0.0                         吻合
```

低能谱结构正确：gaps 清晰对应单量子激发与双量子 / |2> 能级。小耦合 ~0.1 fF 如预期保留，未被"修"。

## 验收清单（acceptance A–J）

```text
A 文件与接口        通过（write_hamiltonian_artifacts 未从包导出；resolve_effective_ej 改名 resolve_effective_junctions，见 nit 5）
B 物理实现          通过（C_mode / E_C / SQUID / charge basis / cos(phi) 矩阵元 / Kronecker 顺序全对；
                        常数复用 sqvm.device.junction；未用 asymmetry）
C 求解器            通过（默认 eigh + subset_by_index 取最低 k；eigsh 直接拒绝 → 红线"默认 which='LM'"彻底规避）
D Checks            通过（结构性 error / 物理 warning 分流正确；ok = all(passed for severity==error)）
E Artifact/notebook 通过（字段齐；notebook 用 Agg、只读 ham_artifact、展示耦合量级+解析对照+PASS/WARN/FAIL）
F 依赖与运行         通过（numpy/scipy 已加；CLI 退出码正确；VSCode runner 可跑）
G 数字自检          通过（见上表）
H 自测              通过（38 passed；verify ok=true）
I 交付附带数值       通过（artifact 含本征值 / C_mode / E_C / EJ / 解析 / 收敛）
J 红线              无一命中
```

亮点：逐模收敛 check 实测印证 N6——coupler 漂移 0.032 GHz，显著大于 q1/q2 的 0.001–0.002，
说明"coupler 更难收敛"是真问题，该 check 抓得住。

## 发现的小问题（nit，均不阻塞）

```text
1. junction.py 的 np_sin()：只是包一层 math.sin（函数内 import），命名误导（像 numpy）。
   纯 cosmetic，公式正确。建议直接用 math.sin。

2. 契约失败走"抛异常"而非"写入 check 报告"：node_order(N1)、device_type、junction 配对(N2)
   的真正校验在上游 raise，导致 checks 里 device_artifacts_type / node_order_contract /
   junction_flux_pairing 三项"结构性检查"实际不可能失败（走到 checks 时已必然通过）。
   CLI 已 catch ValueError -> 退出 1，行为正确；但 verify 失败时拿不到"枚举哪条 check 失败"
   的结构化报告。属 fail-fast vs report 的取舍，建议后续把契约失败也收进 report。

3. junction_flux_pairing check 只判 len==3，message 却写"paired with flux bias"，名实不符
   （真正配对校验在 resolve_effective_junctions 的 raise 里）。

4. single_transmon_check 容差 0.75 GHz 偏松（实际误差才 0.013）。warn-only 影响小，
   但作为"抓实现 bug"的检查偏松；可收紧到 ~0.1 GHz。

5. 接口小出入：write_hamiltonian_artifacts 未从 sqvm.hamiltonian 导出；
   设计里的 resolve_effective_ej 实现为 resolve_effective_junctions（改名）。

6. offset_charge_ng 只记录未接入算子（number_operator 用裸电荷）。n_g=0 下正确，
   但字段是文档性、非功能性。第一版可接受。

7. 缺一个"warn-only 端到端"测试（验证某物理 sanity 失败时 ok 仍为 true）。
   逻辑简单可见，影响小。
```

## 建议与门禁状态

```text
准予通过阶段 2。上述 7 条为可选打磨，不挡进入阶段 3。
若要处理，优先级：nit 2（契约失败收进 report）> nit 1、3（代码清晰度）> 其余。

剩余阶段 2 门禁步骤：
  人工打开 output/stage_02_hamiltonian/verification.ipynb 确认无误，
  即可正式收尾阶段 2，进入阶段 3（静态能谱与 dressed-state 分析）。
```
