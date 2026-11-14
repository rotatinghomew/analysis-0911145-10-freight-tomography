# 计数器覆盖不全时的果品货流反演与分流证据分析

这是一个本地运行的分析工作台。当前版本提供业务数据检查、已有口径的预览、HTTP 查询及 Python 消费端；新的分析入口尚待接入。字段的业务含义与兼容边界见 `docs/contracts.md`，验证范围见 `docs/verification.md`。

数据均为本地合成，不含真实姓名、电话号码或生产系统凭据。`standard` 是常规资料，`edge` 用于检查信息不足或约束冲突。它们只描述观测，不预设分析结论。

## 本地使用

Python 3.11，依赖版本固定在 `requirements.txt`。NumPy/SciPy 是分析工作台的数值工具；预览逻辑主要使用标准库。准备阶段安装依赖，仓库不在启动或测试时下载任何资源。

支持 Python 3.11 系列的补丁版本；Docker 基础镜像通过 SHA256 固定为 Python 3.11.14，依赖清单保持精确版本。相同依赖还需在非 root 的 Python 3.11.2 Linux 环境中验证，应用不依赖特定补丁版本。

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m app.demo
python -m app.server
```

服务监听 `127.0.0.1:8080`，也可通过 `PORT` 调整。预览请求由 `app/client.py` 发起；`app.demo` 在临时端口启动真实服务并完整打印现有业务输出，不是录制日志的回放。服务接口和数据文件的可见性约定不能互相替代。

## 可复现环境

```sh
docker build -t local/freight-tomography:baseline .
docker run --rm --network none local/freight-tomography:baseline python -m unittest discover -s tests -v
docker run --rm --network none local/freight-tomography:baseline
```

准备阶段允许构建镜像和预装依赖；正式演示不进行构建或安装。默认演示只显示已有功能。新能力完成后，请使演示通过实际 HTTP 消费调用展示输入口径、分析结果和限制，保留完整输出；小样本预期在 90 秒内结束。功能测试启用方式：`RUN_ACCEPTANCE=1 python -m unittest tests.test_acceptance -v`，初始版本未提供新接口，故默认跳过这些测试。
