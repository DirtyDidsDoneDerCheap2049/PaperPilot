# 研究执行与流式显示性能

整轮研究时间包括来源访问、PDF、模型排队和生成、证据处理、调度及显示。局部优化需分别测量，不能用本地脚本结果推断供应商速度或整轮提速比例。

## 执行调度

独立论文下载、模型阅读及审计批次默认并发 3，可在设置中调整为 1–6。结果顺序、调用编号、缓存检查、取消和请求预算独立维护。

本地 PDF 解析仍顺序执行，规划决策、综合分析和报告按依赖等待。增加并发不减少请求总数，也不保证供应商允许同样并发；遇到限流时应降低并发并检查失败率。

每次模型请求记录首段正文等待和总耗时，帮助区分服务端生成、后处理和界面等待。

## 流式传输与界面

1. 每次调用有稳定 ID、论文标签和执行代次，避免并发片段混用。
2. 首段及时发送，正文约每 50 ms 合并，超过块大小和结束时立即刷新。
3. 事件持久化后唤醒 SSE，通过游标回放，旧执行代次不覆盖新任务。
4. 页面按 requestAnimationFrame 合并更新，只解析和渲染增长部分，保留已完成段落。
5. 思考保持单行预览，折叠内容按需呈现；原始 JSON 和长思考不进入正文或报告。

Heartbeat 不触发整块重绘。历史事件边回放边显示，首段出现不代表全部历史加载完毕。

## 设计参考

实现参考了 DSH 的独立工具并发、稳定流式身份和快照复用，未引入其整套插件系统或改成单厂商模型协议。参考源码固定在提交 `5badb15009ae1756c3afe0ae0cef1faafc290ccc`：

- [工具调用调度](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/core/agent-loop/src/tool-calls.ts)
- [模型流式身份](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/core/agent-loop/src/assistant-stream.ts)
- [会话流式快照](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/api/session-controller/src/assistant-stream.ts)

借鉴调度或渲染方式不意味着两者模型、账号、资料和响应速度可直接比较，也不能把差距归因于后端语言。

## 离线测量记录

以下为固定输入的局部测量，没有付费模型请求。

| 测试 | 对照 | 优化实现 | 测量范围 |
| --- | --- | --- | --- |
| 30 篇模拟阅读，每次固定等待 50 ms，重复 3 次 | 串行中位 1.8658 秒 | 并发 3，中位 0.6222 秒 | 30 次请求和原文绑定结果相同，只测本地调度 |
| 30 个完成阶段后追加 200 次内容 | 6,200 次 Markdown 渲染，46.273 ms | 200 次渲染，7.804 ms | 脚本回放，不含 DOM、排版或屏幕帧率 |
| 50 次原生事件写入、通知与读取 | SSE 有固定一秒等待 | 中位 2.026 ms，P95 2.660 ms | SQLite FULL 同步，不含模型、网络、浏览器 |

### 浏览器回放

使用本机 Chrome 和实际 Markdown 渲染函数。实时输入为 64 个已完成回答及一个草稿，追加 40 块内容，每块间隔 20 ms；历史输入为 9,002 条事件，每 200 条注入 20 ms 传输等待。

| 指标 | 优化前 | 优化后 |
| --- | --- | --- |
| 收到内容到 DOM 更新，中位 | 101.9 ms | 3.2 ms |
| 收到内容到 DOM 更新，P95 | 168.3 ms | 5.0 ms |
| 历史回放首个阶段进入 DOM | 1,437.1 ms | 18.6 ms |
| 已完成回答和段落节点 | 被替换 | 保留 |

回放同时检查最终文字、向上阅读位置、手动折叠和重开尾段。数值代表 DOM 变更，不代表屏幕绘制、桌面 WebView 帧率、DSH 速度或模型生成速度；历史首段延迟也不是全量加载耗时。

## 复现

```powershell
.venv/Scripts/python.exe scripts/benchmark_agent_runtime.py --output work/testing/current/runtime-benchmark.json
node scripts/benchmark_stream_ui.cjs baseline/chat-research.js src/ui/static/chat-research.js work/testing/current/ui-benchmark.json
node scripts/benchmark_stream_browser.cjs baseline/chat-research.js src/ui/static/chat-research.js work/testing/current/stream-browser.json
```

`baseline/chat-research.js` 是需自行准备的明确版本对照文件，不是仓库自带路径。浏览器测量需要开发环境的 Playwright 和 Chrome；Playwright 不在默认模块目录时，用 `READER_PLAYWRIGHT` 指定模块路径。这不是桌面用户安装要求。

线上对照应固定账号、模型、思考档位、资料和预算，分别测并发 1 与 3，记录完成率、证据覆盖、用量、限流及总耗时。未进行同条件测量前，不给整轮研究提速承诺。
