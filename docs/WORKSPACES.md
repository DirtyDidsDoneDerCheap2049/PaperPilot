# 项目目录

源码、程序、用户数据和测试产物分开保存。公开源码导出使用白名单，私人工作区及维护归档不进入安装包或仓库。

## 源码与文档

| 路径 | 用途 |
| --- | --- |
| `src/app`、`src/server` | 桌面入口、应用装配和本地 API |
| `src/runtime` | 任务服务、worker、并发及事件 |
| `src/analysis`、`src/agents` | 研究调度、工具协调、证据和报告 |
| `src/knowledge`、`src/llm` | 存储、检索、模型适配及规则文件 |
| `src/ui` | 桌面共用的本地页面与静态资源 |
| `native` | C++ 任务内核、依赖清单和许可 |
| `scripts` | 可复用构建、迁移、备份及测量工具 |
| `tests`、`.github` | 本地回归和 CI 配置 |
| `docs` | 当前产品、实现、维护和版本说明 |
| `requirements*.txt`、`pytest.ini` | 依赖与测试配置 |

文档入口见[索引](INDEX.md)。历史设计、旧提示词和个人操作流水不作为当前接口说明。

## 程序与发布产物

| 路径 | 用途 |
| --- | --- |
| `dist/PaperPilot.exe` | 维护用本机桌面入口 |
| `dist/_internal` | 本机运行依赖 |
| `dist/package-docs` | 本机随附说明 |
| `dist/public-desktop/AIReader` | 公开桌面包目录，使用 AIReader.exe 启动 |
| `dist/public-source` | 白名单源码导出，可保留独立 Git 工作树 |
| `dist/AIReader-windows-preview.zip` | 完整公开桌面 ZIP |
| `dist/public-source.zip` | 对应源码 ZIP |
| `build` | 构建中间产物及准备的 embedding 模型 |

本机只保留一个入口，不建立 `dist/current`、`dist/preview` 或 `work` 下的 EXE 副本。公开包不携带本机工作区选择文件，也不携带使用后的私人记录。

## 数据与开发文件

| 路径 | 用途与处理 |
| --- | --- |
| `work/preview/workspace` | 维护用固定日常工作区；preview 为历史名称，升级继续沿用 |
| `my_research` | 旧研究资料，保留原文，不是默认启动入口 |
| `work/reader-mysql`、`work/mysql-test` | 既有 MySQL 工作区及专用测试数据，清理前确认用途 |
| `work/testing/current` | 可重建的普通测试产物，不能放真实日常资料 |
| `work/archive` | 私有历史记录和必要最终验证产物，不公开 |
| `work/.venv`、`work/native-vendor`、`work/zig-cache` | 开发环境与构建依赖 |
| `work/mysql.local.env` | 私有数据库配置，不提交 |

公开包的默认数据位置为 `%LOCALAPPDATA%/AIReader/workspace`，不受维护目录名称影响。典型工作区包含数据库、论文与解析文件、报告、`notes`、设置和 `.agent_history`；以实际配置及记录路径为准。

## 清理规则

先核对绝对路径、目录链接及资料用途。真实会话、删除状态、原始论文、API 配置和历史报告需保留；运行中的数据库及索引不可当作缓存删除。

测试复用固定目录，不连续创建 final、final2 或日期副本。一次性脚本完成后删除或小型归档，可复用工具放入 `scripts`。只保留有回退用途的版本、最终验证结果和复现所需证据，避免重复保存整套论文副本。

发布源码只复制已审查文件，保留 `dist/public-source/.git`。目录移动、清理或导入不会自动修复报告中手写的旧绝对路径。操作前后的校验记录应保存在私有目录。
