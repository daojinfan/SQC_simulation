# 阶段 2 设计审查与待定问题

状态：

```text
草稿 / 待用户拍板
```

日期：

```text
2026-07-09
```

审查对象：

```text
docs/stages/02_hamiltonian_plan.md
docs/designs/02_hamiltonian_design.md
configs/devices/2q1c2r.yaml（阶段 1 示例器件，作为阶段 2 输入）
```

## 本文档目的

记录阶段 2 实现前需要回答的设计问题。每条问题都给出：

```text
现象或问题
影响或原因
候选方案
当前推荐
状态
```

本文档不是最终决策。问题被解决后，再在本文档底部登记结论，并把需要长期保留的内容沉淀为正式决策记录。

## 审查结论速览

阶段 2 设计整体内部自洽。坐标变换 `C_mode = A^T C_node A`、`E_C = e^2 / (2h) * C_mode^-1`、非对称 SQUID 公式、charge-basis 稀疏 Hamiltonian 的推导均正确，Kronecker 顺序、单位约定、artifact 字段也清晰。

主要风险不在推导，而在两点：

```text
1. 示例器件的耦合几何近对称，导致模式间耦合天然很小（见 H1 二次复核更正）。
   这不是投影 bug，但会让 demo 器件缺少可观察的耦合。
2. 缺少能证明“物理对”的验证项（解析极限、收敛性、耦合量级 sanity）。
```

## 审查证据：用示例器件实算的结果

为避免空谈，用 `configs/devices/2q1c2r.yaml` 的真实电容手算了 `C_node -> C_mode -> E_C -> f01`。

节点电容矩阵 `C_node`（fF，节点顺序 q1_p, q1_m, c, q2_p, q2_m）：

```text
[[ 79.3, -75.0,  -4.0, -0.3,   0   ],
 [-75.0,  78.8,  -3.8,  0,     0   ],
 [ -4.0, -3.8,   75.8, -4.1,  -3.9 ],
 [ -0.3,  0,     -4.1,  82.4, -78.0],
 [  0,    0,     -3.9, -78.0,  81.9]]
```

模式电容矩阵 `C_mode = A^T C_node A`（fF，模式顺序 q1, c, q2）：

```text
         q1      c       q2
q1  [ 77.025, -0.100, -0.075 ]
c   [ -0.100, 75.800, -0.100 ]
q2  [ -0.075, -0.100, 80.075 ]
```

对角元给出 `E_C`（GHz）：

```text
q1: 0.251
c : 0.255
q2: 0.242
```

qubit 频率对照（`EJ` 由 `Rn -> Ic -> EJ` 的 AB 公式估算，`EJ_eff` 用非对称 SQUID 公式）：

```text
模式   E_C(GHz)  EJ_eff(GHz)  f01 = sqrt(8 Ec EJ) - Ec   prior f01
q1     0.251     14.81        5.20                       5.10  (吻合)
q2     0.242     16.11        5.34                       5.30  (吻合)
```

结论：

```text
对角物理（qubit 频率）与阶段 1 priors 高度吻合，说明 EJ 解析链路和 E_C 对角部分可信。
非对角模式耦合只有约 0.1 fF，是 H1 的核心证据。
```

## 问题清单

| 编号 | 优先级 | 标题 | 状态 |
| --- | --- | --- | --- |
| H1 | 中 | demo 耦合几何近对称→模式间耦合过小（投影本身合理，见二次复核更正） | 待定 |
| H2 | 高 | eigsh 默认 which='LM' 会取最大本征值 | 待定 |
| H3 | 高 | 缺少解析极限验证 | 待定 |
| H4 | 高 | charge_cutoff 收敛性未验证 | 待定 |
| M1 | 中 | 非对称 SQUID 忽略相位偏置 delta | 待定 |
| M2 | 中 | 1331 维下稠密 eigh 比 eigsh 更稳更简 | 待定 |
| M3 | 中 | asymmetry 死字段，示例不触发非对称分支 | 待定 |
| D1 | 低 | offset charge n_g=0 未显式记录 | 待定 |
| D2 | 低 | Hermiticity check 近似平凡 | 待定 |
| D3 | 低 | 绝对本征值含 -EJ 大常数，应说明看 gaps | 待定 |
| D4 | 低 | pyproject 缺 numpy / scipy | 待定 |
| N1 | 高 | 第二轮：未硬校验 device_artifacts 的 node_order | 待定 |
| N2 | 中 | 第二轮：EJ/flux_bias 跨字段拼接易错 | 待定 |
| N3 | 中 | 第二轮：H3 需 priors，与 notebook 只读 ham_artifact 冲突 | 待定 |
| N4 | 中 | 第二轮：common-mode warning 缺可计算定义 | 待定 |
| N5 | 中 | 第二轮：H3 应在解耦单模 H 上验证 | 待定 |
| N6 | 中 | 第二轮：收敛性应逐模，coupler 更易欠收敛 | 待定 |
| N7 | 低 | 第二轮：tolerance 字段在 eigh 下语义失效 | 待定 |
| N8 | 低 | 第二轮：物理常数应跨阶段共享 | 待定 |
| N9 | 低 | 第二轮：缺基态/gap 量级 sanity | 待定 |
| N10 | 低 | 第二轮：notebook 需显式 matplotlib Agg backend | 待定 |

## H1：差分模投影导致模式间耦合异常小

> 二次复核更正（2026-07-09，物理判断）：原“投影丢掉共模、black-box 能找回耦合”的说法过头。对称 floating transmon 的差分模（plasma 模）与对称耦合的 coupler 之间，电容耦合本就 ∝ island 不对称；共模无 Josephson 恢复力（高 E_C、无 EJ），不承载 qubit-coupler 耦合。因此 black-box / 正则模量化对该近对称器件会给出**相同量级**的小耦合，投影并未“丢”耦合。真正成因是示例器件耦合几何近对称 → 小 g 是几何的真实结果，不是量化方法问题。结论：**保持固定 A 投影**；H1 实质问题降级为“demo 耦合几何是否需要调到真实量级”。

现象：

```text
C_mode 非对角项约 0.1 fF，是对角元的 0.13%。
C_mode[q1,c] = -0.1 fF，完全来自 C_q1p_c - C_q1m_c = 4.0 - 3.8 = 0.2 再减半。
若两 island 对 coupler 电容取相等（4.0 / 4.0），该项严格为 0。
对应 g_q1c 量级约 0.4 MHz，真实器件典型几十到几百 MHz，差 2 到 3 个数量级。
```

影响：

```text
小耦合本身不是 bug：投影正确反映了近对称几何（black-box 会给出相同量级）。
但 demo 器件 g ~ 0.4 MHz，意味着几乎没有 qubit-coupler 相互作用：
阶段 3 的 g / ZZ / 可调耦合器关断（ZZ cancel）/ CZ 门物理都会接近零。
若希望后续阶段有意义，需要把耦合几何调到真实量级（见候选方案 B）。
```

候选方案：

```text
方案 A（方法层面）：保持固定 A 差分模投影。
  投影对该器件合理；black-box 不会给出更大耦合，方法无需更换。
  即便改成单 island / 强不对称耦合，plasma 模仍是 ~差分模，投影依旧近似成立。

方案 B（几何层面）：调整示例器件耦合几何，让 g 进入真实量级（几十 MHz）。
  例如 qubit-coupler 改单 island 耦合，或把 4.0 / 3.8 改成强不对称。
  这是器件配置改动，不是量化方法改动。

方案 C（范围层面）：阶段 2 只验证建 H 机制，demo 小耦合先接受，
  真实几何留到阶段 3 前再调。
```

当前推荐：

```text
方法层面：保持固定 A（投影合理，black-box 不解决耦合）。
几何层面：倾向方案 B，让 demo 有真实耦合，阶段 3 的 g / ZZ / CZ 才有意义；
  但属器件配置决定，需用户拍板（见与用户的逐条确认）。
N4（common-mode warning）相应降级为可选 sanity，不再是关键项。
```

状态：

```text
已决定（2026-07-09）：方案 C —— 保持固定 A 投影，阶段 2 接受小耦合，
不调 demo 几何；真实耦合几何留到阶段 3 前再决策。详见决策登记。
```

## H2：eigsh 默认 which='LM' 会取最大本征值

现象：

```text
设计中 solver.method = eigsh，tolerance = 1.0e-10，但未指定 which / sigma。
scipy.sparse.linalg.eigsh 默认 which='LM'（最大模），
会返回能量最高的本征值，而不是最低的。
```

附加陷阱：

```text
即使改用 sigma=0 做 shift-invert，本例基态能量为负（约 -EJ_eff，可达十几 GHz）。
sigma=0 落在谱中部，会返回中间本征值而非最低本征值。
```

候选方案：

```text
方案 A：保留 eigsh，显式约束求解策略
  shift-invert，sigma 取在估计基态之下，which='LM'；
  或直接 which='SA'（最小代数值，但对不定矩阵可能慢且不稳）。
  把 which / sigma / tolerance 语义写入配置和文档。

方案 B：默认改用稠密求解（见 M2）
  阶段 2 当前维度 1331，稠密 eigh 完全可行，且没有这套坑。
```

当前推荐：

```text
采用 M2 的稠密 eigh 作为默认求解器，eigsh 留给将来更大维度，
从而从根上消除 H2。
```

状态：

```text
待定，与 M2 联动。
```

## H3：缺少解析极限验证

现象：

```text
阶段 2 现有 13 个 check 全是结构性（对称 / 正定 / Hermitian / 形状 / 有限 / 排序）。
没有一条能证明“物理是对的”。
项目开发流程 10_development_process.md 明确要求“可用时与解析近似比较”，阶段 2 漏了。
```

建议 check：

```text
把另外两模式解耦（交叉电容置零）后，单 transmon 应满足：
  f01 ≈ sqrt(8 E_C EJ_eff) - E_C
  非谐性 alpha ≈ -E_C
本文档“审查证据”一节已经用示例数值验证过 f01 吻合 priors，
因此该 check 可以写成确定性的、可自动判定的。
```

当前推荐：

```text
加入 single_transmon_analytic_limit check。
作为阶段 2 能谱正确性的核心证据之一。
```

状态：

```text
待定。
```

## H4：charge_cutoff 收敛性未验证

现象：

```text
默认 charge_cutoff = 5，单模式 dim = 11，总 dim = 1331。
本例 EJ / E_C 约 60，transmon 波函数在相位上窄、电荷基收敛慢，
N = 5 不一定收敛。
```

影响：

```text
若未收敛，最低本征值可能只是截断误差。
后续阶段若用这些本征值拟合频率或耦合，会带入系统偏差。
```

建议 check：

```text
对 N 和 N + 2 分别求最低若干本征值，要求漂移小于阈值才算通过。
verify 时打印 N -> N + 2 的漂移量。
可选：根据漂移自动给出 charge_cutoff 推荐。
```

当前推荐：

```text
加入 charge_basis_convergence check，
默认配置的 N = 5 也需要被该 check 验证为收敛。
```

状态：

```text
待定。
```

## M1：非对称 SQUID 忽略相位偏置 delta

现象：

```text
严格势能是 -EJ_eff * cos(phi_i - delta_i)，
delta = arctan(EJ_delta * sin(alpha) / (EJ_sigma * cos(alpha)))。
设计用 cos(phi_i)，即 delta = 0。
```

影响：

```text
在 transmon 区（EJ / E_C 远大于 1，电荷色散指数小），delta 对能级影响指数小，可接受。
但若 coupler 工作在电荷敏感点（flux 接近 0.5，EJ_eff 很小），该近似变差。
本例 c 的 flux = 0.27，EJ_eff 仍较大，目前安全。
```

当前推荐：

```text
不改模型，但在 02_hamiltonian_design.md 第 8 节显式写明：
“忽略 delta，理由是 transmon 区电荷色散可略，电荷敏感点附近需复核。”
让隐含假设变成显式记录。
```

状态：

```text
待定。
```

## M2：1331 维下稠密 eigh 比 eigsh 更稳更简

现象：

```text
dim = 1331，稠密直接对角 scipy.linalg.eigh(H, subset_by_index=[0, 12]) 约 1 到 2 秒，
无收敛、which、sigma 任何坑，精确拿到最低 12 个本征值。
稀疏是为将来准备的：N = 10 时 dim = 9261，稠存约 700 MB 会爆。
```

当前推荐：

```text
默认求解用稠密 eigh + subset。
稀疏构建保留，用于“小 cutoff sparse / dense 等价”验证和未来更大维度。
solver.method 支持 eigh 与 eigsh 两种。
```

状态：

```text
待定。与 H2 联动，二者建议一起按“默认稠密”定。
```

## M3：asymmetry 死字段，示例不触发非对称分支

现象：

```text
阶段 1 SquidSpec.asymmetry 在阶段 2 EJ 公式里未使用，
非对称性已由 rn1 != rn2 体现。
示例器件所有结 rn1 == rn2，
“非对称 SQUID 公式”端到端从未被示例触发，
test_effective_ej_asymmetric_formula 只能靠手工构造数据覆盖。
```

当前推荐：

```text
二选一：
  删除 asymmetry 字段；或
  给示例某个结 rn1 != rn2，以真正覆盖非对称分支。
倾向删除字段，避免“有字段但没用”的歧义。
```

状态：

```text
待定。该改动会回溯影响阶段 1（spec / validation / artifacts）。
```

## D1：offset charge n_g = 0 未显式记录

现象：

```text
charge basis 默认假设无门电压，即 offset charge n_g = 0。
该假设在阶段 2 设计中未显式说明。
```

建议：

```text
在 hamiltonian_artifacts.json 和设计文档第 9 节显式记录 n_g = 0（charge degeneracy 假设）。
避免阶段 3 猜测本征值对应的偏置点。
```

## D2：Hermiticity check 近似平凡

现象：

```text
E_C 实对称，cos(phi) 给出实三对角，
Hamiltonian 是实对称矩阵，自动 Hermitian。
Hermiticity check 几乎不可能失败。
```

建议：

```text
把 hermiticity check 保留为便宜的自检，
另加更强的“对角 E_C 量级合理（约 0.05 到 2 GHz）warning”作为物理 sanity。
```

## D3：绝对本征值含 -EJ 大常数

现象：

```text
Hamiltonian 中 -EJ 项使绝对本征值为较大负数。
物理可观测量是 gaps，不是绝对本征值。
```

建议：

```text
artifact 已存 eigenvalue_gaps_GHz。
在文档和 notebook 点明：可观测量看 gaps，绝对本征值无直接物理意义。
```

## D4：pyproject 缺 numpy / scipy

现象：

```text
阶段 2 明确依赖 numpy 和 scipy，当前 pyproject.toml 尚未加入。
```

建议：

```text
在 pyproject.toml dependencies 补充 numpy 和 scipy。
```

## 决策登记

每条问题被解决后，在此登记结论。格式：

```text
日期
问题编号
结论
影响范围（文档 / 代码 / 测试）
后续动作
```

（待填写）

### 2026-07-09 / H1

结论：

```text
保持固定 A 差分模投影（物理上合理，black-box 不解决耦合）。
示例器件耦合几何近对称 → 小耦合（g ~ 0.4 MHz）属几何真实结果，非 bug。
阶段 2 接受小耦合（方案 C），不调 demo 几何；真实耦合几何留到阶段 3 前再决策。
N4（common-mode warning）降级为可选 sanity，非关键。
```

影响范围：

```text
docs：阶段 2 设计保持固定 A；H1 二次复核更正已写入本文档与记忆。
代码：阶段 2 实现保持固定 A；可加“模式间耦合量级”展示（非必须）。
测试：不受影响。
```

后续动作：

```text
阶段 3 前必须重新评估：是否调 demo 几何以获得真实 g / ZZ。
```

### 2026-07-09 / M3

结论：

```text
延后清理。asymmetry 字段先保留；阶段 2 实现禁止读取 asymmetry（避免两个非对称来源）。
```

影响范围：

```text
阶段 1 不变。
阶段 2：effective EJ 只用 rn1 / rn2 / flux_bias_phi0，不用 asymmetry。
```

后续动作：

```text
后续统一清理 stage 1 时删除 asymmetry 字段。
```

### 2026-07-09 / H3 + H4 严格度

结论：

```text
全部只 warn。解析极限（单模数值 vs 解析公式、vs prior）和收敛性检查
不达标时只给 warning，不让 verify_hamiltonian 失败。
只有结构性检查（对称 / 正定 / Hermitian / 形状 / 有限 / 排序）和 error 才 fail。
```

影响范围：

```text
阶段 2 verify_hamiltonian：物理 sanity 全部走 warning 通道。
artifact：checks 仍记录 passed 标志供 notebook 展示，但不阻塞 verify。
```

后续动作：

```text
实现时把 H3 / H4 结果写入 checks（带 passed 标志），
verify 的 ok 只由结构性 check 和 validation 决定。
```

## Codex 初步建议：先处理 H1-H4，再进入实现

日期：

```text
2026-07-09
```

状态：

```text
建议稿 / 待用户拍板
```

### 总体判断

阶段 2 当前设计可以作为起点，但不建议直接按原设计实现。审查文档提出的问题中，H1-H4 会影响阶段 2 的正确性证据和默认实现路径，应先拍板。

最需要先定的是：

```text
H1：固定差分模投影导致耦合异常小。
H2 + M2：默认 eigsh 改为 dense eigh。
H3：加入解析极限验证。
H4：加入 charge_cutoff 收敛性检查。
```

### H1 建议：保留固定 A，但显式暴露耦合投影风险

不建议阶段 2 立即升级到 black-box quantization，因为这会显著扩大阶段范围。但也不应把固定差分模投影当作最终耦合模型。

建议阶段 2 第一版：

```text
1. 继续使用固定 A 构建 C_mode。
2. 在 artifacts 和 notebook 中显式展示 C_mode 非对角项。
3. 增加 projected coupling capacitance 指标。
4. 增加 common-mode coupling 被投影掉的 warning 指标。
5. 文档中明确阶段 2 Hamiltonian 是“差分模投影模型”，不能直接当作最终耦合模型。
6. 在开放问题中标注：阶段 3 前必须决定是否升级到 black-box / normal-mode quantization。
```

建议原因：

```text
这样可以保持阶段 2 小步推进，同时不隐藏最关键的物理风险。
若阶段 3 要提取 g / ZZ，则必须重新评估固定 A 是否足够。
```

### H2 + M2 建议：默认 solver 改为 dense eigh

当前默认 `eigsh` 有取错本征值的风险，因为 `scipy.sparse.linalg.eigsh` 默认 `which='LM'`，会取最大模本征值，不是最低能级。

阶段 2 默认维度：

```text
dim = 1331
```

在这个规模下，dense `scipy.linalg.eigh` 更稳、更简单，也更容易保证取到最低本征值。

建议默认配置改为：

```yaml
solver:
  method: eigh
  num_eigenvalues: 12
```

实现策略：

```text
1. Hamiltonian 构建仍可保留 sparse 表示。
2. 默认求解时转 dense，用 scipy.linalg.eigh subset_by_index 求最低 num_eigenvalues。
3. eigsh 作为未来大维度可选 solver，不作为阶段 2 默认。
4. 若后续支持 eigsh，必须显式配置 which / sigma，不能依赖 scipy 默认值。
```

### H3 建议：加入 single_transmon_analytic_limit check

阶段 2 不能只有结构性检查。应加入至少一个解析近似 check，证明低能物理量级正确。

建议新增：

```text
single_transmon_analytic_limit
```

检查对象：

```text
q1
q2
可选 c
```

解析近似：

```text
f01 ≈ sqrt(8 * E_C * EJ_eff) - E_C
alpha ≈ -E_C
```

建议输出：

```text
mode
E_C_diag_GHz
EJ_eff_GHz
analytic_f01_GHz
prior_f01_GHz（若存在）
difference_GHz（若存在 prior）
```

判断方式：

```text
第一版先作为 sanity check 和 notebook 展示。
若 prior 存在且偏差过大，给 warning。
不建议第一版直接 fail，因为 priors 只是参考信息，不是强约束。
```

### H4 建议：加入 charge_cutoff convergence check

默认 `charge_cutoff = 5` 不一定对 EJ/E_C 约 60 的 transmon 足够收敛。

建议新增：

```text
charge_basis_convergence
```

检查方式：

```text
1. 用默认 N 构建并求最低本征值。
2. 用 N + 2 构建并求最低本征值。
3. 比较前 num_eigenvalues 中若干个低能 gaps 的漂移。
4. 在 artifact 和 notebook 中展示最大漂移。
```

第一版建议：

```text
收敛性超阈值先给 warning，不直接 fail。
```

原因：

```text
若一开始要求必须通过，可能会迫使默认 cutoff 变大，导致阶段 2 实现和运行时间膨胀。
先让用户看到实际漂移，再决定默认 cutoff 是否改为 6、7 或更高。
```

### M1 建议：显式记录忽略 SQUID phase offset delta

非对称 SQUID 严格势能为：

```text
-EJ_eff * cos(phi - delta)
```

阶段 2 设计只使用：

```text
-EJ_eff * cos(phi)
```

建议不改模型，但在设计文档中写明：

```text
第一版只保留有效 Josephson 能量幅度，忽略 phase offset delta。
该近似在 transmon 区通常可接受；flux 接近 0.5 或 coupler 进入电荷敏感区时需要复核。
```

### M3 建议：后续清理 asymmetry 字段

`asymmetry` 当前是死字段。非对称性已经由：

```text
rn1_ohm != rn2_ohm
```

体现。

建议后续单独清理阶段 1：

```text
删除 squid.asymmetry。
更新 device config、spec、artifact、测试和文档。
```

这可以在阶段 2 实现前作为一个小清理完成，也可以单独排期。若暂不清理，阶段 2 不应使用 `asymmetry` 字段，避免出现两个非对称来源。

### D1-D4 建议

低优先级但应写入文档或 artifact：

```text
D1:
  显式记录 offset_charge_ng = 0。
  第一版建议写入 hamiltonian_artifacts.json，暂不暴露到配置。

D2:
  Hermiticity check 保留，但不要把它当作核心物理正确性证据。
  新增 E_C 量级 sanity warning，例如 E_C 对角元是否处于约 0.05 到 2 GHz。

D3:
  notebook 中说明绝对本征值含 -EJ 常数项，主要物理可观测量看 gaps。

D4:
  pyproject.toml dependencies 必须显式加入 numpy 和 scipy。
```

### 建议拍板顺序

建议按下面顺序逐条确认：

```text
1. H1：阶段 2 先保留固定 A，但加入耦合投影 warning，black-box 留到阶段 3 前决策。
2. H2/M2：默认 solver 改为 dense eigh。
3. H3：加入 single_transmon_analytic_limit check。
4. H4：加入 charge_cutoff convergence check，第一版先 warning 不 fail。
5. M1：文档显式记录忽略 SQUID phase offset delta。
6. M3：决定是否现在清理 asymmetry 字段。
7. D1-D4：作为文档和 artifact 的小修正一并处理。
```

## 第二轮审查补充（2026-07-09）

在第一轮 H1–D4 和 Codex 建议稿之上再过一遍，补充以下证据与问题。编号 N1–N10 已加入上方问题清单表。

### 补充证据 E1：coupler 对角也吻合 prior

```text
c: Rn=6188 -> EJ/结 ≈ 22.7 GHz, EJ_sum ≈ 45.4 GHz
   EJ_eff(flux 0.27) = 45.4 * |cos(0.27π)| ≈ 30.0 GHz
   f_c = sqrt(8 * 0.255 * 30.0) - 0.255 ≈ 7.57 GHz
   prior estimated_idle_frequency_GHz = 7.50  (吻合)
```

三个模的频率全部与 priors 吻合（q1 5.20/5.10、q2 5.34/5.30、c 7.57/7.50）。结论更明确：

```text
对角物理（频率、flux 点）全部可信。
问题集中在 off-diagonal 耦合（约 0.1 fF），与对角正确性无关。
```

### 补充证据 E2：投影后模式变量仍正则（正面确认）

```text
theta_node = A * theta_mode 时，共轭动量 n_mode = A^T n_node，
可验证 [phi_mode_i, n_mode_j] = i * delta_ij。
因此 4 Σ E_C,ij n_i n_j - Σ EJ_eff cos(phi_i) 是合法的正则量子 Hamiltonian。
模型内部一致，唯一的物理风险仍是差分模投影（H1）。
```

> 二次复核更正（2026-07-09）：下面“H1 强化”小节基于“耦合是投影错误”的前提，该前提已被推翻（投影正确，耦合只是小）。其中唯一仍成立的点是：demo 几乎无耦合时，低能 check 看不出耦合量级是否合适，因此建议把模式间耦合量级写入 artifact 展示（不必非做 N4 共模量化）。

### H1 强化：阶段 2 自己的本征值已受影响（前提已被更正）

```text
错误耦合不只影响阶段 3。
阶段 2 唯一的输出就是最低若干本征值；多激发态和避免交叉能级直接依赖耦合大小。
低激发单量子态以对角为主（所以 E1 频率吻合），
而 H3/H4 检查看的正是这些低 gap（对角主导），因此发现不了耦合错误。
结论：阶段 2 当前规划的检查无法发现“中高能级因耦合错误而失真”。
所以 N4（量化 common-mode 耦合）应在阶段 2 就做，不能只靠低能 check。
```

### N1：未硬校验 device_artifacts 的 node_order（高）

现象：

```text
设计 check #4 是“coordinate_transform A 与 node_order 匹配”，
即 A 被构造成适配读到的 node_order。
但阶段 2 用的是固定 A，其行顺序硬编码为 [q1_p, q1_m, c, q2_p, q2_m]。
若阶段 1 将来改变 node_order，而阶段 2 仍套用固定 A，会静默错位。
```

建议：

```text
阶段 2 显式 assert device_artifacts.capacitance_matrix.nodes
严格等于 [q1_p, q1_m, c, q2_p, q2_m]，不等则 error（不是 warning）。
把“node_order 必须是预期 2q1c 顺序”写成阶段 1 -> 阶段 2 的硬契约。
```

### N2：EJ / flux_bias 跨字段拼接易错（中）

现象：

```text
阶段 1 junction_parameters 输出每个 component 两行（j1, j2 的 ej_GHz），不含 flux_bias_phi0。
flux_bias_phi0 只在 components.X.squid.flux_bias_phi0 里。
阶段 2 resolve_effective_ej 必须把这两处拼起来：
  按 component 配对 j1/j2 得到 EJ1/EJ2，再从 components.X.squid 取 flux_bias。
```

影响：

```text
这是 bug-prone 的隐式 join，依赖两个未明示的前提：
  junction_parameters 恰好每个 component 两行；
  components.X.squid.flux_bias_phi0 存在。
```

建议：

```text
1. 文档化该 join，写成 EffectiveJunctionTable 的明确输入契约。
2. 加 check：每个 tunable component 恰好 2 行 junction + 非空 flux_bias_phi0。
3. 可选：阶段 1 artifact 增加 squid summary（component -> ej1, ej2, flux_bias_phi0），
   让阶段 2 不必跨字段拼接。
```

### N3：H3 需 priors，与“notebook 只读 hamiltonian_artifacts”冲突（中）

现象：

```text
设计 §15.1 规定 verification.ipynb 只读 hamiltonian_artifacts.json。
但 Codex H3 建议的解析 check 要对照 priors（estimated_f01_GHz），
而 priors 只在 device_artifacts.json 里。
```

建议：

```text
二选一：
  把参与对比的 prior 值（如 prior_f01_GHz per mode）嵌入 hamiltonian_artifacts.json；或
  放宽 notebook 读取规则，允许读 device_artifacts 的 priors 子集。
倾向前者，保持“notebook 只读 hamiltonian_artifacts”的边界。
```

### N4：Codex 的 common-mode warning 缺可计算定义（中）

现象：

```text
Codex H1 建议加“common-mode coupling 被投影掉的 warning”，但没给具体公式。
```

建议：

```text
好消息：阶段 2 手里有完整 C_node（从 device_artifacts 读），
可以不建全 H 就量化被丢掉的共模耦合。
具体做法：
  取被 A 投影掉的 2 个共模方向（q1_p+q1_m、q2_p+q2_m 的归一化基）；
  计算它们与保留模（q1, c, q2）之间的电容耦合块范数；
  以及共模自身的 E_C（共模电容很小 -> E_C 很大 -> 高能但非冻结）。
把该量级写入 artifact 并在 notebook 显示，超阈值 warning。
这同时正面回答了“共模到底安不安全”的物理问题。
```

### N5：H3 应在解耦单模 H 上验证（中）

现象：

```text
Codex H3 把 analytic f01 和“实际本征值”对比，但没说在哪个 H 上取本征值。
若直接用耦合 3 模 H 的本征值比解析值，会把耦合效应和解析极限混在一起。
```

建议：

```text
解析 check 在“解耦单模 H”上做：
  对每个模单独构建 1 模 H（只用 E_C 对角元 E_C,ii 和 EJ_eff,i），
  对角化取 f01，与 sqrt(8 E_C,ii EJ_eff) - E_C 比较。
这样验证的是“单模 charge-basis 实现是否正确”，与耦合无关。
```

### N6：收敛性应逐模，coupler 更易欠收敛（中）

现象：

```text
coupler 的 EJ_eff / E_C ≈ 30 / 0.255 ≈ 118，比 qubit 的 ≈ 60 高。
同样 charge_cutoff 下，coupler 波函数更窄、更可能欠收敛。
```

建议：

```text
H4 收敛 check 逐模报告（固定其他模，单独抬高一模的 cutoff，看该模相关 gap 漂移）。
并允许逐模 charge_cutoff（config 已支持 per-mode），避免一刀切。
```

### N7：tolerance 字段在 eigh 下语义失效（低）

现象：

```text
Codex M2 把默认 solver 改为 dense eigh。
但 config 的 tolerance 字段是 eigsh 专属；eigh 没有“tolerance”概念（走 LAPACK）。
```

建议：

```text
config schema 注明 tolerance 仅适用于 eigsh；
或把 solver 配置按 method 分组：
  eigh: { num_eigenvalues }
  eigsh: { which, sigma, tol }
```

### N8：物理常数应跨阶段共享（低）

现象：

```text
Phi0、e、h、Delta_Al 已在阶段 1 junction.py 定义。
阶段 2 若重新定义 E_C 常数（e^2 / (2h) 等），可能出现两处常数值漂移。
```

建议：

```text
把物理常数提到共享模块（如 sqvm.physical_constants 或 sqvm.device.constants），
阶段 1 / 2 共用同一来源。
```

### N9：缺基态/gap 量级 sanity（低）

现象：

```text
现有 check 只要求本征值“有限且升序”，
没有要求第一 gap 处于合理 GHz 范围。
```

建议：

```text
加 check：第一 gap > 0 且处于约 0.1–20 GHz 的宽松范围，
防止数值上得到退化或病态谱却仍“通过”。
```

### N10：notebook 需显式 matplotlib Agg backend（低）

现象：

```text
阶段 2 notebook 含 C_node / C_mode / E_C heatmap 和 gaps 折线图。
headless / CI 执行需 Agg backend；阶段 1 踩过中文编码和渲染坑。
```

建议：

```text
沿用阶段 1 notebook.py 的渲染模式（matplotlib Agg + UTF-8），
在设计文档注明 notebook 执行环境要求。
```

### 第二轮小结

```text
新增 1 条高优先级（N1，接口硬契约）和 5 条中优先级（N2–N6，多为建议稿的缺陷或方法论收紧）。
两条正面证据（E1、E2）确认：模型结构自洽、对角物理可信，风险仍集中在 H1 的耦合投影。
关键收敛点：N4（量化 common-mode 耦合）应与 H1 一起在阶段 2 落地，
因为低能 check（H3/H4）发现不了耦合错误（见 H1 强化）。
```
