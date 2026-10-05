# 发布检查

检查对象是待发布源码和对应软件包。离线回归、桌面启动和在线研究效果分别记录；跳过项不计为通过，局部基准不证明研究结论正确。

## 源码与依赖

在 Windows Python 3.12 开发环境执行：

```powershell
.venv/Scripts/python.exe -m pip check
.venv/Scripts/python.exe -m pytest tests -q
node --test tests/test_conversation_ui.cjs tests/test_live_ui.cjs
.venv/Scripts/python.exe scripts/prepare_release.py --check-only
```

检查依赖锁、未定义名称、文档链接和新增文件。MySQL、符号链接权限及显式软件包测试可能按环境跳过，记录原因，不能用总通过数掩盖这些范围。

## 桌面包

```powershell
.venv/Scripts/python.exe scripts/build_desktop.py
./dist/public-desktop/AIReader/AIReader.exe --smoke-test
.venv/Scripts/python.exe scripts/prepare_release.py --desktop dist/public-desktop/AIReader
```

检查静态资源、健康接口、窗口打开与关闭、GUI 子系统、标准管道及 worker。打包 worker 的模拟模型链路：

```powershell
$env:READER_TEST_DESKTOP_EXE = (Resolve-Path dist/public-desktop/AIReader/AIReader.exe).Path
.venv/Scripts/python.exe -m pytest tests/test_desktop_delivery.py -k worker_through -q
Remove-Item Env:READER_TEST_DESKTOP_EXE
```

测试使用隔离工作区，不指向日常资料。窗口启动成功不等于干净系统安装通过；没有 WebView2 的环境、代码签名和安装兼容性需单独验证。

## 行为验收

| 范围 | 验证内容 |
| --- | --- |
| 首次使用 | 无 MySQL 和无模型密钥仍可启动；模型入口、缺全文提示清楚 |
| 任务可靠性 | 幂等冲突、取消、重启中断、排队恢复、显式重试及单实例锁 |
| 研究调度 | 指定资料与开放研究范围、日期边界、重复查询和预算停止 |
| 阅读覆盖 | 指定 PDF 承接、读取成功 / 失败 / 未完成统计，局部摘录明确标记 |
| 数值与证据 | 模型行归属、教师及基线隔离、引用原文绑定、部分交付 |
| 论文库 | 固定目录、增量复用、独立筛选维度、重复折叠和撤销 |
| 全文检索 | 真实 embedding、原文位置、源变化失效、失败索引不激活 |
| 报告 | 直接结论、可读格式、修订保留原稿且不重读，讨论回复展开 |
| 流式交互 | 并发调用隔离、游标回放、最终文字、折叠和向上阅读位置 |
| 隐私与权限 | 私有文件访问、设置保护、上传类型与大小、公开包隔离 |

固定样例和模型替身验证程序路径。真实论文验收另选有人工答案的样例，检查日期、模型、原表值、引用、结论及不确定性表达，不能只确认“生成了报告”。

## 在线评估与恢复

在线测试使用独立工作区，明确外发资料、模型配置、请求预算和费用。记录来源失败、实际请求、统计覆盖、读取完整性和最终结论，不覆盖旧报告。

恢复演练先关闭程序，备份文件及所用数据库，恢复到新位置后核对会话、全文、报告和引用。SQLite 与 MySQL 分别测试；跨账号需重新配置 DPAPI 密钥。

取消与崩溃演练检查活动任务终态、已有产物和后续是否继续请求。供应商已收到的请求可能收费，不把本地终止记录当作零费用证明。

## 发布产物核对

源码导出与 ZIP、包内 UI、文档和许可应一致，记录 SHA-256。确认无私人选择文件、凭据、数据库、论文和会话，保留对应源代码。构建及上传步骤见[发布指南](PUBLISHING.md)。

当前仍有限制：跨数据库与文件的共同事务、任意步骤自动恢复、干净 Windows 安装、代码签名、所有供应商兼容和各领域研究质量均不能因离线检查通过就宣称完成。
