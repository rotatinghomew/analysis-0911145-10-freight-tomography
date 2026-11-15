# 行为验证

- 部分网络观测的逆问题
- 路径样本选择偏差与异口径计数
- 可识别范围及留出校验
- 通道变化与信息补采价值

tests/test_domain.py 覆盖现有业务口径；tests/test_transport.py 通过真实本地 HTTP 验证数据到消费端。tests/test_acceptance.py 是新接口的公开契约测试，以 RUN_ACCEPTANCE=1 启用。tests/test_inversion.py 针对算法设计：

- PolytopeEngineTests：LP 极值与等式行的精确执行、不可行检测（不依赖固定 fixture 名称）。
- InversionEvidenceTests：点估计落在识别区间内；卡口与业务边界约束成立；货车口径只产生区间；停机卡口不被当零；样本带只收窄假设层且标注选择偏差；未点识别的 OD 不得宣称唯一识别；解析中心不塌缩到零顶点。
- HoldoutAndEdgeCaseTests：holdout 只做 validation；edge 的矛盾证据触发松弛并完整列出松弛约束；两个 case 可区分。
- ScenarioTests：封路不重新拟合、无幸存路径 OD 标 unserved；需求不变与改道比例两种假设分离；closure 覆盖与非法输入拒绝；稳健结论同时成立于两种情景。
- InformationValueTests：补采探针排除 holdout 边和已有精确箱数边、按不确定性下降量排序并标注反事实性质。

新接口不得通过固定 fixture 名称返回预制结论；所有数值结论由 app/inversion.py 的 LP/障碍优化现场计算。默认测试运行跳过验收用例，RUN_ACCEPTANCE=1 时全部 32 个用例应通过。
