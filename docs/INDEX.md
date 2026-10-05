# 文档索引

文档描述当前实现。历史设计、旧提示词和个人维护流水不作为接口或功能依据；历史研究结果保存在各自工作区，不随文档更新改写。

## 使用

| 文档 | 内容 |
| --- | --- |
| [项目首页](../README.md) / [English](../README.en.md) | 功能、安装、模型配置和限制 |
| [产品目标](PRODUCT_PURPOSE.md) | 使用场景、交付结果及产品边界 |
| [工作区指南](WORKSPACE_GUIDE.md) | 数据位置、备份、导入、升级和恢复 |
| [论文库整理](LIBRARY_ORGANIZATION.md) | 分类、增量复用、重复折叠、名称与缺全文 |
| [模型输出](MODEL_OUTPUT.md) | 报告格式、Naturawrite、讨论及自动修订 |
| [模型用量](MODEL_COST.md) | 本地复用、供应商缓存和费用统计 |
| [MySQL](MYSQL.md) | 可选存储、迁移及数据库备份 |

## 实现

| 文档 | 内容 |
| --- | --- |
| [架构](ARCHITECTURE.md) | 进程职责、任务协议、存储和故障边界 |
| [研究调度](ADAPTIVE_RESEARCH.md) | 工具、资料范围、时间、预算及比较 |
| [主张与证据](DOMAIN_PROFILES.md) | 原文绑定、方法事实、覆盖与候选 |
| [全文检索](FULLTEXT_RETRIEVAL.md) | 分块、embedding、混合排名和增量索引 |
| [性能与显示](RUNTIME_PERFORMANCE.md) | 并发、SSE、增量渲染和局部基准 |
| [本地检索测量](LOCAL_RETRIEVAL_BENCHMARK.md) | 真实语料的资源条件、结果和限制 |

## 维护与发布

| 文档 | 内容 |
| --- | --- |
| [项目目录](WORKSPACES.md) | 源码、程序、数据、测试和清理规则 |
| [发布检查](RELEASE_CHECKS.md) | 离线、桌面、在线及恢复验收 |
| [发布指南](PUBLISHING.md) | 构建、公开导出、Git 与 Release |
| [版本记录](RELEASE_NOTES.md) | 版本变化摘要与未发布草稿 |
| [第三方说明](../THIRD_PARTY.md) | 依赖、模型及许可 |
| [图片说明](assets/readme/README.md) | 截图与视觉素材来源 |

## 模型规则

以下文件是程序加载的行为规则，不是用户使用教程：

- [论文库整理](../src/knowledge/policies/library-organizer/SKILL.md)：分类、输入边界及输出约束。
- [Naturawrite](../src/knowledge/policies/naturawrite/SKILL.md)：报告自然写作，附原许可。

规则中的“你”指调用时的模型角色；修改规则会改变模型行为，应与对应代码和回归一起审查。
