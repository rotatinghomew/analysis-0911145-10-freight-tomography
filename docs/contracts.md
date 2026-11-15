# 产品数据与调用约定

paths 每个候选路径有 od、按方向排列的 edges 以及允许日期。counters 的 measure 是 boxes 或 trucks，availability 为有效观察比例，停机为 null 不等于零；holdout=true 不参与拟合。samples 的 inclusion_band 是可接受抽样概率范围，绝不表示随机无偏抽样。业务 known_bounds 可跨 OD 约束。POST /infer {case, closure?} 返回 {snapshot, day, relaxed_fit, relaxed_constraints, od, fitted, validation, scenarios, information_value, diagnostics}；od 每条记录含 point、identified（硬证据识别区间及其 sources）和 assumption（加入样本带后的假设区间、中心与 sources）。封路仅改变可选路径，未观察的改道行为不能冒充实测。不能从正则化得到唯一解便称数据唯一识别；point 是可行多面体的解析中心（退化时回退到 LP 中心），identified 区间才是数据可区分范围的正式表述。

当前 GET /cases、POST /preview、POST /infer 和 GET /health 已可用；client.analysis 封装 /infer 调用，closure 可按请求覆盖 case 内置封路块（edges 必填、reroute_band 可为 null）。原始数据通过 app.store 读取，snapshot 为输入字节的 SHA-256。新增接口保留现有预览能力和 JSON 有限数值约定（allow_nan=false，所有输出四舍五入到 6 位小数）。

fitted 和 validation 均为记录列表，用 counter 标识输入计数器；holdout 只出现在 validation，不用于参数拟合、松弛选择或补采探针。availability 缩放仅是现有查看器的初步归一，不自动带来无偏或精确约束，推断中作为点约束并在 diagnostics 标注其假设。样本倒概率区间是期望关系下的假设范围，只进入 assumption 层，正式推断说明抽样波动未建模。

反演把非负路径流量作为决策变量，在同一线性多面体上叠加：箱数卡口点约束、货车卡口经 boxes_per_truck 转换的区间约束、known_bounds 跨 OD 区间约束（识别层）；样本带单独构成假设层。每个量通过最小化/最大化 LP 取识别区间。硬证据矛盾时不静默改写：minimum weighted violation 定位必须松弛的约束，relaxed_fit=true、relaxed_constraints 列出涉及 ID，所有输出标注来自松弛拟合。scenarios 是历史拟合的前向投影而非重新拟合：demand_unchanged 让 OD 总量保持在历史识别区间内自由改道；reroute_band 情景以历史点估计乘以改道比例区间，显式标注司机行为不是观测；无幸存路径的 OD 标 unserved。information_value.candidates 是反事实完美箱数探针的排序（排除 holdout 边和已有精确箱数卡口的边）：od_width_reduction 是各 OD 边际识别区间的宽度下降，joint_allocation_spread_reduction 是路径层条件宽度的联合下降（刻画同一 OD 在并行路径间的分流不确定性），仅用于补采决策不代表实测。
