# MySQL 使用与迁移

默认 SQLite 足以启动桌面应用，无需数据库服务。本文适用于主动选择 MySQL 的用户或维护者；MySQL 不提供自动云同步、多人协作或云端任务执行。

## 连接

每个工作区使用独立数据库和专用账号，不用 root 作为应用账号。由管理员创建库和账号：

```sql
CREATE DATABASE ai_reader CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE USER 'reader'@'localhost' IDENTIFIED BY '<your-password>';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX ON ai_reader.* TO 'reader'@'localhost';
```

打开新工作区的连接向导：

```powershell
./AIReader.exe --storage mysql --workspace D:/ReaderData/mysql-research
```

已有工作区通过 `.database.json` 保留后端选择，连接失败不自动回退到 SQLite。Windows 密码由当前用户的 DPAPI 加密保存，跨账号需重新配置。

开发环境可使用不提交的 `work/mysql.local.env`：

```dotenv
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=reader
MYSQL_PASSWORD=<your-password>
MYSQL_DATABASE=ai_reader
```

```powershell
.venv/Scripts/python.exe -m src.app.desktop --workspace D:/ReaderData/mysql-research --db-env work/mysql.local.env
```

远程连接可指定 `MYSQL_SSL_CA` 以校验证书和主机名，实际远程服务及 TLS 配置需专项验证。不要把公网连接可配置等同于已经完成远程部署验收。

## 从 SQLite 导入

先关闭程序并备份旧工作区。目标必须是空的专用数据库，不自动合并已有数据。

```powershell
.venv/Scripts/python.exe scripts/migrate_mysql.py --source D:/ReaderData/research/db/ai_reader.db --db-env work/mysql.local.env --report work/mysql-migration.json
```

工具只读源库，在 MySQL 事务中导入研究表，逐表检查行数和内容摘要，失败回滚。源 SQLite 不删除。PDF 和报告路径仍指向原工作区，迁移数据库不会搬迁文件。

范围包括论文、解析、证据、画像、会话和消息。旧 `tasks.db` 任务历史、文本索引及外部向量索引不自动导入，需保留旧库查阅并重建派生索引；这不是全部历史的无损迁移。

## 备份与恢复

关闭程序，同时备份完整文件工作区和数据库：

```powershell
.venv/Scripts/python.exe scripts/backup_workspace.py D:/ReaderData/research D:/ReaderBackup/research-copy
.venv/Scripts/python.exe scripts/backup_mysql.py --db-env work/mysql.local.env --output D:/ReaderBackup/research.sql
```

SQL 导出使用一致性快照，末尾必须包含 `READER_BACKUP_COMPLETE` 标记；失败半成品不可用作恢复。备份含论文、会话和配置，应私有保管。

管理员创建新空库，通过 MySQL 客户端执行 `source D:/ReaderBackup/research.sql`，再连接该库和对应文件副本。核对表内容、会话、报告与全文路径，确认前保留原库。

回退使用匹配版本的完整程序及数据备份。MySQL 新数据不会自动同步回 SQLite。

## 构建与边界

```powershell
.venv/Scripts/python.exe scripts/build_native.py --mysql-client "C:/Program Files/MySQL/MySQL Server 8.0"
```

不传客户端路径可构建纯本地任务内核。支持 MySQL 的包附带客户端 DLL，不包含或自动安装 MySQL 服务器；客户端许可见[第三方说明](../THIRD_PARTY.md)。

- C++ 管任务表，Python 管研究记录和文本索引，PDF 和报告仍为本地文件。
- 数据库与报告文件没有共同事务，异常中断后需核对已有产物。
- MySQL FULLTEXT 不等于向量检索，默认中文分词有限。
- 不支持多用户共享、自动服务启动或主从切换。
- 集成测试使用专用实例与临时库，不读取私有连接文件；条件见 [test_mysql.py](../tests/test_mysql.py)。
- 旧库升级及所选服务器版本需单独验证，历史连接测试不代表所有版本已兼容。
