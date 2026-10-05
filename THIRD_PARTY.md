# 第三方依赖与模型

PaperPilot 自有代码采用 [AGPL-3.0-only](LICENSE)。论文及导入资料保留原权利归属，不随源码分发，也不因导入改变许可。

## 主要组件

| 组件 | 用途 | 许可与来源 |
| --- | --- | --- |
| PyMuPDF / MuPDF | 本地 PDF 提取 | AGPL-3.0 或商业许可；本项目采用开源许可，见[许可说明](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright) |
| nlohmann/json 3.11.3 | C++ JSON 协议 | MIT，[固定版本](https://github.com/nlohmann/json/tree/v3.11.3) |
| SQLite 3.46.1 | 本地数据与任务存储 | Public Domain，[版权说明](https://www.sqlite.org/copyright.html) |
| MySQL C API 8.0.16 | 可选任务存储 | 随附 GPLv2、Universal FOSS Exception 1.0 和第三方许可，构建复制供应商 LICENSE |
| PyMySQL | Python MySQL 驱动 | MIT，版本见依赖锁 |
| pywebview | 桌面窗口 | BSD-3-Clause，[源码](https://github.com/r0x0r/pywebview) |
| FastAPI / Uvicorn | 本地 HTTP 接口 | MIT / BSD-3-Clause |
| OpenAI Python SDK | 兼容模型接口 | Apache-2.0 |
| Naturawrite | 报告写作规则 | MIT，基于 blader/humanizer 修订；[许可与版权](src/knowledge/policies/naturawrite/LICENSE.txt) |
| Chroma、ONNX Runtime、Tokenizers、NumPy、Filelock | 本地 embedding 和索引 | 各组件许可证随包放在 THIRD_PARTY_LICENSES |

C++ 依赖下载地址和 SHA-256 见 `native/dependencies.json`，许可文本位于 `native/licenses`。Python 固定版本见 `requirements-lock-windows-py312.txt`。

桌面构建复制原生和 Python 依赖许可到 `THIRD_PARTY_LICENSES`；清单可能包含构建环境中的可选依赖。此表是主要组件摘要，不替代完整随包许可。

## 本地 embedding 模型

使用 Apache-2.0 模型 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，由 Xenova 转换为 ONNX。转换固定于版本 `2c4055b12046f11709e9df2c122e59ffbdc2f900`。

模型 LICENSE、NOTICE、版本与 SHA-256 清单随文件保存在 `_internal/resources/models/paper-embedding`。源码准备方式见[全文检索](docs/FULLTEXT_RETRIEVAL.md)。

Windows WebView2 为系统运行组件，不作为项目自有代码分发。公开二进制需提供对应源码、构建脚本及完整许可，不能将第三方论文纳入代码授权。
