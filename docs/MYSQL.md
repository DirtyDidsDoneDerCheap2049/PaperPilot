# MySQL 使用与迁移

2026-09-22 起新桌面工作区默认使用内置 SQLite，无需安装数据库。本文适用于主动选择 MySQL 的用户和已有 MySQL 工作区，不是普通用户的启动前提。

## 连接

Windows 桌面使用 MySQL 8.0，本机验证版本为 8.0.16。每个工作区使用独立数据库和账号，不使用 root 作为日常账号。先启动 MySQL，由管理员创建数据库与账号：

```sql
CREATE DATABASE ai_reader CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE USER 'reader'@'localhost' IDENTIFIED BY '<your-password>';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX ON ai_reader.* TO 'reader'@'localhost';
```

使用 `AIReader.exe --storage mysql --workspace D:/ReaderData/mysql-research` 打开 MySQL 连接向导。请使用独立工作区，切换选项不会搬迁数据。已有工作区的 `.database.json` 会自动保留 MySQL 选择；连接失败不会回退。Windows 密码使用当前用户的 DPAPI 加密保存。开发时可使用私有文件 work/mysql.local.env，不要提交：

```dotenv
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=reader
MYSQL_PASSWORD=<your-password>
MYSQL_DATABASE=ai_reader
```

```powershell
.venv/Scripts/python.exe -m src.app.desktop --workspace D:/ReaderData/research --db-env work/mysql.local.env
```

远程连接可指定 MYSQL_SSL_CA，启用证书与主机名校验；本轮只验证本机连接。数据库不可用时不会自动回退到 SQLite。

## 从 SQLite 导入

关闭 Reader，备份旧工作区。目标必须是空的专用数据库，不自动合并已有数据。

```powershell
.venv/Scripts/python.exe scripts/migrate_mysql.py --source D:/ReaderData/research/db/ai_reader.db --db-env work/mysql.local.env --report work/mysql-migration.json
```

工具只读打开源库，在 MySQL 事务中导入研究表，逐表核对行数和内容摘要，失败回滚。PDF、报告路径仍指向原工作区，应保持路径不变。源 SQLite 不删除、不修改。

导入包含论文、解析、证据、画像、会话、消息等研究表。旧 tasks.db 的任务历史、旧文本索引和外部向量索引暂不自动导入；保留旧库供查阅。新任务在 MySQL 记录，论文重新解析后建立新文本索引。这不是全部历史无损迁移。

## 备份与恢复

关闭 Reader，同时备份文件和数据库：

```powershell
.venv/Scripts/python.exe scripts/backup_workspace.py D:/ReaderData/research D:/ReaderBackup/research-copy
.venv/Scripts/python.exe scripts/backup_mysql.py --db-env work/mysql.local.env --output D:/ReaderBackup/research.sql
```

SQL 导出使用一致性快照，保留时间戳。末尾必须有 READER_BACKUP_COMPLETE 标记；失败的半成品不可恢复。备份含论文内容，应按私人资料保管。

恢复：管理员建立新的空库，在 MySQL 命令行客户端执行 `source D:/ReaderBackup/research.sql`，再配置 Reader 连接新库和对应工作区副本。路径发生变化时需检查历史文件引用。确认恢复成功前保留原库。

回退使用改造前的完整程序与工作区备份。新 MySQL 数据不会自动同步回 SQLite。

## 边界

- 源码构建可选 MySQL：`python scripts/build_native.py --mysql-client "C:/Program Files/MySQL/MySQL Server 8.0"`。不传此参数可构建纯本地任务内核。支持 MySQL 的发布包附带客户端 DLL，不附带或自动安装 MySQL 服务器。

- C++ 管任务表，Python 管研究表和文本索引，PDF 和报告仍在本地。
- MySQL FULLTEXT 不等价于向量检索，默认分词对中文有限制。
- 数据库与报告文件没有共同事务，异常中断后仍需核对结果。
- 不包含 MySQL 安装器、自动服务启动、多用户共享、自动主从切换。
- 集成测试只连接专用测试端口并创建临时库，不读取 work/mysql.local.env；启用方式见 tests/test_mysql.py。
