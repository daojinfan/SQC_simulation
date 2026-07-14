# QCIS（Quantum Control Instruction Set）说明

> 本文根据视频画面逐帧提取并整理，仅使用画面信息，未使用视频语音。
> 画面中的正文、指令格式、参数名和可辨识公式已尽量校正；少量公式因拍屏模糊或原页面 LaTeX 未正确渲染，仅保留可确认的内容与计算流程。

## 简介

用户对量子计算机的量子调控通过量子线路实现。量子线路由量子调控指令（QCIS 指令）构成的指令序列。

QCIS（Quantum Control Instruction Set）是一套对超导量子计算机硬件系统进行控制的指令集。

## 指令格式

```text
<Operation> <Target> <Parameters>
```

- `Operation`：指令类型。
- `Target`：目标 QAgent，1 个或以上。
- `Parameters`：需要的参数列表，可能有 0 到 N 个，为数值列表。

QOS 3.0 支持的指令包括：

```text
X, Y, X2P, X2M, Y2P, Y2M, X4P, X4M, Y4P, Y4M,
XY, XY2P, XY2M, Z, S, SD, T, TD, RZ, CZ, M, B,
FSIM, SET, I, SWD, SWA, CONDX, RST, LRU
```

## 指令说明

### X / X2P / X2M / Y / Y2P / Y2M

#### 功能

标准旋转角度 XY 门。

#### 格式

```qcis
X   [qubit]
X2P [qubit]
X2M [qubit]
Y   [qubit]
Y2P [qubit]
Y2M [qubit]
```

#### 说明

该组指令会在比特的 XY 线路中添加 XY 门波形。波形类型由注册表中量子比特单比特门配置里的 `wv_class` 决定。

`X2P Q01` 的解析流程如下，`X2M`、`Y2P`、`Y2M` 同理：

1. 在注册表中找到 Q01 量子比特的配置项 `active_xy2_setting`。
2. 根据其对应值（例如 `xy2_setting1`）找到同名的波形参数文件夹。
3. 根据 `xy2_setting1.wv_class` 确定波形类型。
4. 根据其余波形参数和波形公式计算波形数据。

`X Q01` 的解析流程如下，`Y` 同理：

1. 在注册表中找到 Q01 量子比特的配置项 `xy_pi_impl`。
2. 当其为 `True` 时，采用一个 X 脉冲实现 X 门：读取 `active_xy_setting` 指向的配置目录，根据 `wv_class` 和其他参数计算波形。
3. 当其为 `False` 时，采用两个 X/2 脉冲实现 X 门：将原指令转换为两个连续的 `X2P Q01`，再按 X2P 的方式解析。

单个 XY 门波形的计算形式为：

$$
f(t)=\left[f_{envelope}(t)+\alpha f'_{envelope}(t)e^{-i\pi/2}\right]
e^{i\left[
2\pi\frac{f_{01}-f_{LO}+f_{detune}}{f_{AWG}}t+
2\pi\frac{f_{01}-f_{LO}}{f_{AWG}}t_0-
\varphi_{gate}-\varphi_{RZ}
\right]}
$$

根据波形公式得到波形数据后：

- 对 EZQ1 系统，波形实部通过量子比特 `xy_awg` 的 `xy_awg_ch_i` 通道输出，虚部通过 `xy_awg_ch_q` 通道输出。
- 对 EZQ2 系统，波形通过量子比特的 `control_unit_xy` 输出，通道为 `control_unit_xy_ch`。

参数说明：

| 参数 | 说明 |
| --- | --- |
| $f_{envelope}(t)$ | 包络函数类型，由单比特门配置的 `wv_class` 确定 |
| $\alpha$ | 量子比特配置参数 `drag_alpha` |
| $f_{01}$ | 量子比特配置参数 `f01` |
| $f_{LO}$ | 量子比特配置参数 `xy_local_freq`（EZQ1）或 `nco_freq`（EZQ2） |
| $f_{detune}$ | 量子比特 XY 门配置参数 `drive_detune` |
| $f_{AWG}$ | AWG 采样率 |
| $t_0$ | 当前波形在量子线路中的开始位置 |
| $\varphi_{gate}$ | XY 门固定相位，单位 rad |
| $\varphi_{RZ}$ | RZ 累计相位，单位 rad |

XY 门固定相位：

| 门 | 相位 |
| --- | ---: |
| `X` | $0$ |
| `X2P` | $0$ |
| `X2M` | $\pi$ |
| `Y` | $\pi/2$ |
| `Y2P` | $\pi/2$ |
| `Y2M` | $-\pi/2$ |

包络函数 $f_{envelope}(t)$ 由量子比特的单比特门配置确定，所有参数均在单比特门配置目录中设置。

### XY / XY2P / XY2M

#### 功能

- `XY`：在 X 门基础上增加大小为 `phase` 的相移。
- `XY2P`：在 X2P 门基础上增加大小为 `phase` 的相移。
- `XY2M`：在 X2M 门基础上增加大小为 `phase` 的相移。

#### 格式

```qcis
XY   [qubit] [phase]
XY2P [qubit] [phase]
XY2M [qubit] [phase]
```

`phase` 的单位为 rad。

#### 说明

解析方法同 `X/X2P/X2M/Y/Y2P/Y2M`，区别是波形相位在原有基础上额外增加 $\varphi_{input}$（指令参数 `phase`）：

$$
f(t)=\left[f_{envelope}(t)+\alpha f'_{envelope}(t)e^{-i\pi/2}\right]
e^{i\left[
2\pi\frac{f_{01}-f_{LO}+f_{detune}}{f_{AWG}}t+
2\pi\frac{f_{01}-f_{LO}}{f_{AWG}}t_0-
\varphi_{gate}-\varphi_{input}-\varphi_{RZ}
\right]}
$$

### X12

#### 功能

将比特由 $|1\rangle$ 激发至 $|2\rangle$。

#### 格式

```qcis
X12 [qubit]
```

#### 说明

以 `X12 Q01` 为例：

1. 在注册表中找到 Q01 的配置项 `active_xy12_setting`。
2. 根据其对应值（例如 `xy12_setting1`）找到同名波形参数文件夹。
3. 根据 `xy12_setting1.wv_class` 确定波形类型，再结合其余参数和公式计算波形数据。

其中 $f_{01}$ 为比特配置参数 `f01`，$f_{ah}$ 为比特配置参数 `fah`（非简谐性，$f_{12}=f_{01}+f_{ah}$）。除 `f01` 和 `fah` 位于量子比特根配置项外，其余波形参数位于单比特门配置目录。

### RXY / RX / RY

#### 功能

绕 XY 平面上的旋转轴执行任意角度的旋转。

#### 格式

```qcis
RXY [qagent] [azimuth] [altitude]
RX  [qubit]  [altitude]
RY  [qubit]  [altitude]
```

- `azimuth`：XY 平面上旋转轴的方位角，单位 rad。
- `altitude`：绕旋转轴旋转的角度，单位 rad，取值范围 $[-\pi,\pi]$；超出范围时自动转换到该范围。

#### 说明

- `RXY`：绕 XY 平面上方位角为 `azimuth` 的轴旋转 `altitude`。
- `RX`：绕 X 轴旋转 `altitude`，等价于 `RXY [qubit] 0 [altitude]`。
- `RY`：绕 Y 轴旋转 `altitude`，等价于 `RXY [qubit] pi/2 [altitude]`。

解析时：

1. 当 $|altitude| \ge \pi/2$ 且 XY 门使用 $\pi/2$ 脉冲实现时，RXY 波形由两个 $\pi/2$ 脉冲组成，脉冲长度、幅度和相位由 `azimuth`、`altitude` 计算。
2. 当 XY 门使用 $\pi$ 脉冲实现时，RXY 波形由一个 $\pi$ 脉冲组成，幅度随 $|altitude|/\pi$ 缩放，相位由 `azimuth` 和 `altitude` 的符号确定。

### PLS / PLSXY

#### 功能

施加任意波形脉冲。

#### 格式

```qcis
PLS   [qagent] [waveIndex] [tStart] [length] [amplitude] [frequency] [phase] [dragAlpha] [...]
PLSXY [qagent] [waveIndex] [tStart] [length] [amplitude] [frequency] [phase] [dragAlpha] [...]
PLS   [qagent] -1 [tStart] [s0] [s1] [s2] ... [sn]
```

#### 波形编号

| 编号 | 波形名称 | 其他参数 |
| ---: | --- | --- |
| `0` | `rectangle` | `width` |
| `1` | `gaussian` | `r_sigma` |
| `2` | `flattop` | `edge` |
| `3` | `rrring` | `edge`, `ring_amplitude`, `ring_width` |
| `4` | `pump` | `sideband_amp`, `sideband_freq` |
| `5` | `acz` | `thf`, `thi`, `lam2`, `lam3` |
| `6` | `nacz` | `chc`, `chs`, `c1c`, `c1s`, `c2c`, `c2s`, `c3c`, `c3s`, `c4c`, `c4s` |
| `7` | `slepian` | `thf`, `thi`, `lam2`, `lam2`（按画面原文） |
| `8` | `accz` | `accz_freq`, `accz_phase_offset`, `envelope_para`, `rise_time`, `pulse_time`, `env_drag_alpha`, `double_alpha`, `double_phase`, `rise_type` |
| `-1` | `numeric` | `s0`, `s1`, `s2`, ..., `si`, ..., `sn` |

记自变量为 $t$、波形幅度为 $A$、波形长度为 $n$。画面给出的部分波形定义如下：

$$
rectangle(t,A,n,width)=A[u(t)-u(t-width)]
$$

$$
gaussian(t,A,n,r_{sigma})=A\exp\left[-\frac{1}{2}\left(\frac{t-(n-1)/2}{r_{sigma}}\right)^2\right]
$$

`flattop` 由两个误差函数的差构造；`rrring` 为 `flattop` 与环形高斯项叠加；`pump` 使用 `sideband_amp` 和 `sideband_freq` 生成复数边带波形。

`acz` 无直接解析表达式，画面中的算法流程为：根据 `thf`、`thi`、`lam2`、`lam3` 计算中间序列，归一化后积分，再通过线性插值生成 `acz` 采样点。

`slepian` 先计算长度为 1024 的 `acz` 波形，再按目标长度进行索引采样。

`nacz` 的可辨识表达式为：

$$
\begin{aligned}
nacz(t)=A[&chc\cos(t)+chs\sin(t)\\
&+c1c\cos(2t)+c1s\sin(2t)\\
&+c2c\cos(4t)+c2s\sin(4t)\\
&+c3c\cos(6t)+c3s\sin(6t)\\
&+c4c\cos(8t)+c4s\sin(8t)]
\end{aligned}
$$

`accz` 为交流 CZ 门波形。

`numeric` 约定编号为 `-1`，每个采样点由用户在指令参数中直接传入。例如：

```qcis
PLS Q1 -1 256 0 1 2 ... 100
```

表示在 Q1 的 Z 通道上，从 `t=256` 开始施加一段数值波形。对 `PLSXY`，数据长度应为 2 的倍数：前一半是 I（或 X）通道，后一半是 Q（或 Y）通道。

#### 参数说明

1. `tStart`：波形起始时间，单位为采样点数。
   - `tStart < 0`：起始时间为前一个门的结束时间加 1，即与前面的门序列拼接。
   - `tStart >= 0`：波形从 `tStart` 开始，与前面门序列的长度无关。
2. `length`、`amplitude`、`frequency`、`phase`、`dragAlpha`：分别为波形长度、幅度、载波频率、载波相位和 DRAG 系数。
3. 其余参数由具体波形类型决定。
4. `PLS` 默认用于量子比特 Z 波形，或只有一个控制通道的 QAgent，例如 `JPA`、`TRANSMON_TUNABLE_G`。
5. `PLSXY` 用于量子比特 XY 通道，按 IQ 通道读取数据。

### I

#### 功能

等待或延时。

#### 格式

```qcis
I [qagent] [length]
```

`length` 为延时时间长度，单位为 AWG 采样点。

该指令在 QAgent 的 XY 线路和 Z 线路中添加一段幅度为 0、长度为 `length` 的波形。

### Z / S / SD / T / TD

#### 功能

Z 门操作。

#### 格式

```qcis
Z  [qagent]
S  [qagent]
SD [qagent]
T  [qagent]
TD [qagent]
```

#### 说明

Z 门实现方式由量子比特配置参数 `z_gate_impl` 确定。

当配置为 `PULSE` 时，通过 XY 门实现 Z 门，适用于不能使用虚 Z 门且需要避免实 Z 拖尾的情况，例如存在 SWAP 门时。

| 门 | PULSE 实现方式 |
| --- | --- |
| `Z` | `Y X` |
| `S` | `Y2P X2P X2M` |
| `SD` | `Y2P X2M Y2M` |
| `T` | `Y2P X4P Y2M` |
| `TD` | `Y2P X4M Y2M` |

当配置为 `VIRTUAL` 时，等价于 `RZ [qagent] [phase]`，为当前指令之后的比特 XY 波形增加相移。

| 门 | VIRTUAL 相位 |
| --- | ---: |
| `Z` | $-\pi$ |
| `T` | $-\pi/2$ |
| `TD` | $\pi/2$ |
| `S` | $-\pi/4$ |
| `SD` | $\pi/4$ |

### DTN

#### 功能

比特频率 DETUNE 操作。

#### 格式

```qcis
DTN [qagent] [length] [amplitude]
```

- `length`：DETUNE 持续时间，单位为 DAC 采样点。
- `amplitude`：DETUNE 幅度，单位为 DAC 码值或 Hz。

#### 说明

向 QAgent 的 Z 线路添加一个 Z 偏置波形，波形类型由配置参数 `active_detune_setting` 指向的波形文件夹确定。

对量子比特，若 `is_zbias_amp2f01` 为 `True` 且设置了 `zbias2f01_mapper`，则指令参数 `amplitude` 按频率处理，生成波形时转换为 AWG 码值。画面给出的频率映射为：

$$
freq(z)=(max_{f01}-f_{ah})\sqrt{|\cos(k_f\pi(z-bias))|}
$$

DTN 码值计算流程：

1. 求方程 $freq(z)=f_{01}$ 距离 0 最近的实根：

   ```text
   z1 = get_root(zbias2f01_mapper, f01, fah, 0)
   ```

2. 求方程 $freq(z)=f_{01}+amplitude$ 距离 `z1` 最近的实根：

   ```text
   z2 = get_root(zbias2f01_mapper, f01 + amplitude, fah, z1)
   ```

3. DTN 波形幅度为 `z2 - z1`。

画面中的 `get_root(coef, freq, fah, target)` 通过反余弦的周期解构造候选根，并返回距离 `target` 最近的实根；原页面该部分有若干未正确渲染的 LaTeX 字符串，因此不在此重写具体展开式。

对耦合器，若 `is_g2bias_mapping` 为 `True` 且设置了 `zbias2g_mapper`，`amplitude` 按耦合强度处理。计算时根据 `zbias2g_mapper` 中的 `zpulse_amp` 和 `coupler_strength` 计算 AWG 码值：以 `coupler_strength` 为自变量、`zbias2g_mapper` 为因变量，对 `amplitude` 插值，结果作为 DTN 波形幅度。

### RZ

#### 功能

虚 Z 门。

#### 格式

```qcis
RZ [qagent] [phase]
```

`phase` 为相位，单位 rad。

#### 说明

RZ 指令为 QAgent 量子线路中当前指令之后的所有 XY 波形增加大小为 `phase` 的相移。例如：

```qcis
RZ Q1 0.1
X Q1       # phi_RZ = 0.1
RZ Q1 0.2
X2P Q1     # phi_RZ = 0.1 + 0.2 = 0.3
```

### CZ

#### 功能

虚 Z 门（按视频画面原文）。

#### 格式

```qcis
CZ [coupler]
```

#### 说明

CZ 门实现方式由耦合器配置中的 `active_cz_setting` 决定。该配置为字符串，指向一个文件夹；文件夹中的 `wv_class` 决定 CZ 门的波形类型。

### FSIM

#### 功能

双比特 FSIM 门。

#### 格式

```qcis
FSIM [qagent] [index]
```

`index` 为计算 FSIM 门波形所用参数的索引。

FSIM 门涉及的量子组件包括 `coupler`、耦合器硬件配置 `qubit0` 对应的比特，以及 `qubit1` 对应的比特。原页面的具体处理方法标记为 `todo`。

### B

#### 功能

对齐多个量子组件的线路。

#### 格式

```qcis
B [qagent_1] [qagent_2] ... [qagent_n]
```

#### 说明

分别计算 `qagent_1`、`qagent_2`、...、`qagent_n` 的量子线路当前长度，记最大长度为 `max_len`，将线路长度小于 `max_len` 的量子组件补齐到 `max_len`。

### M

#### 功能

对量子比特进行测量。

#### 格式

```qcis
M [qagent]
```

#### 说明

解析读取指令后会生成多个通道的波形，可能包括读取通道、量子比特 XY 通道、Z 通道，以及可调耦合器 Z 通道的波形。原页面后续细节标记为 `TODO`。

### SWD / SWA

#### 功能

设置 `workbias` 的持续时间和幅度。

#### 格式

```qcis
SWD [qagent] [length]
SWA [qagent] [amplitude]
```

- `length`：`workbias` 持续时间。
- `amplitude`：`workbias` 幅度。

#### 说明

`SWD`：

- 当 QAgent 的实现方式 `workBiasMode` 为 `dc` 时，该指令不起作用。
- 当 `workBiasMode` 为 `pulse` 时，按 `length` 设置 `workbias` 波形持续时间；默认情况下，`workbias` 波形覆盖整个量子线路。
- QAgent 类型应为可偏置（`BIASABLE`），例如 `TRANSMON_FT`、`TRANSMON_TUNABLE_G`、`JPA`。

`SWA`：

- QAgent 类型应为可偏置（`BIASABLE`），例如 `TRANSMON_FT`、`TRANSMON_TUNABLE_G`、`JPA`。

### RST

#### 功能

主动重置。

#### 格式

```qcis
RST [qagent]
```

#### 说明

RST 门的实现方式由耦合器配置中的 `active_reset_setting` 决定。该配置为字符串，指向一个文件夹；文件夹中的 `wv_class` 决定 RST 的实现方式。
