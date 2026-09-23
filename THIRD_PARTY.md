# 第三方依赖

PaperPilot 自有代码按 AGPL-3.0-only 发布，见 LICENSE。论文与用户导入资料不随源码分发，也不因导入而改变许可。

| 组件 | 用途 | 许可 / 来源 |
| --- | --- | --- |
| PyMuPDF / MuPDF | 本地 PDF 文本提取 | AGPL-3.0 或商业许可；本项目选择开源许可。见 https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright |
| nlohmann/json 3.11.3 | C++ JSON 协议 | MIT；https://github.com/nlohmann/json/tree/v3.11.3 |
| SQLite 3.46.1 | 默认本地数据与任务存储 | Public Domain；https://www.sqlite.org/copyright.html |
| MySQL C API 8.0.16 | C++ 持久任务存储 | 客户端随附 GPLv2、Universal FOSS Exception 1.0 与第三方许可；构建时复制供应商 LICENSE |
| PyMySQL | Python MySQL 驱动 | MIT；版本见锁文件 |
| pywebview | Windows 桌面窗口 | BSD-3-Clause；https://github.com/r0x0r/pywebview |
| FastAPI / Uvicorn | 桌面内部 HTTP 接口 | MIT / BSD-3-Clause |
| OpenAI Python SDK | 用户配置的兼容模型接口 | Apache-2.0 |

C++ 下载地址和 SHA-256 见 native/dependencies.json，原生依赖许可文本见 `native/licenses/`。Python 的准确版本见 requirements-lock-windows-py312.txt。桌面构建会复制原生及 Python 依赖许可证到 THIRD_PARTY_LICENSES；清单包含构建环境中存在但未必打进程序的可选依赖。

Windows WebView2 是系统运行组件，未作为 Reader 自有代码分发。当前桌面包不附带 Chroma；需要外部向量检索时使用源码安装 requirements-vector.txt。

公开二进制时须同时提供对应源码、构建脚本和许可文件。本轮只准备本地文件；尚未发布任何远程仓库或二进制。
