# 产品数据与调用约定

paths 每个候选路径有 od、按方向排列的 edges 以及允许日期。counters 的 measure 是 boxes 或 trucks，availability 为有效观察比例，停机为 null 不等于零；holdout=true 不参与拟合。samples 的 inclusion_band 是可接受抽样概率范围，绝不表示随机无偏抽样。业务 known_bounds 可跨 OD 约束。POST /infer {case, closure?} 返回 {snapshot, od, fitted, validation, scenarios, diagnostics}；od 至少含估计及其识别/假设范围。封路仅改变可选路径，未观察的改道行为不能冒充实测。不能从正则化得到唯一解便称数据唯一识别。

当前 GET /cases、POST /preview 和 GET /health 已可用；client.analysis 是待接入的业务消费调用，新分析路由尚不存在。原始数据通过 app.store 读取，snapshot 为输入字节的 SHA-256。新增接口须保留现有预览能力和 JSON 有限数值约定。

fitted 和 validation 均为记录列表，用 counter 标识输入计数器；holdout 只出现在 validation，不用于参数拟合或超参选择。availability 缩放仅是现有查看器的初步归一，不自动带来无偏或精确约束。样本倒概率区间也是期望关系下的假设范围，正式推断应说明抽样波动。
