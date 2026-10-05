# 构建与发布

源码仓库与桌面软件包分别分发。公开版本应对应明确的源码提交，附带许可证、构建说明和校验值；工作区、论文、真实会话及本机配置不属于发布内容。

以下命令在开发目录执行，Python 环境示例为 `.venv`。首次导出与刷新已有导出目录二选一，不重复初始化已有 Git 仓库。

## 测试与构建

先完成[发布检查](RELEASE_CHECKS.md)，再构建：

```powershell
.venv/Scripts/python.exe scripts/build_desktop.py
.venv/Scripts/python.exe scripts/prepare_release.py --check-only
.venv/Scripts/python.exe scripts/prepare_release.py --desktop dist/public-desktop/AIReader
```

公开目录为 `dist/public-desktop/AIReader`。发布时压缩完整文件夹，单独 EXE 不能运行。默认不携带本机工作区指向或使用记录。

`--local-workspace` 可在公开构建完成后更新维护用 `dist/PaperPilot.exe`，沿用固定日常数据；本机文件和选择配置不能并入公开包。日常更新规则见[目录约定](WORKSPACES.md)。

Windows 构建采用 GUI 子系统，不应弹出控制台。诊断查看工作区 `.agent_history/app.log` 和 `worker.log`，不要为了显示日志把公开包改成控制台程序。

## 源码导出

### 首次导出

```powershell
.venv/Scripts/python.exe scripts/prepare_release.py
```

脚本按白名单建立 `dist/public-source`、源码 ZIP 和 SHA-256 清单。目标已存在时停止，不覆盖已有 Git 工作树。普通克隆仓库可以保留根目录 Git；包含私人工作区的维护目录应只提交白名单导出。

### 刷新已有产物

```powershell
.venv/Scripts/python.exe scripts/refresh_release.py
```

刷新先核对旧源码清单，遇到手工修改、未知文件或源码删除时停止，保留 `.git`。运行前处理停止原因，不能删除 Git 目录来绕过检查。

脚本同步公开源码、公开包文档和 ZIP，并核对包内 UI 是否与源码一致。文档改动不需要重编译 EXE；业务代码或资源改动仍需重新构建。刷新只准备本地文件，不提交或上传。

| 产物 | 用途 |
| --- | --- |
| `dist/public-source` | 公开源码目录 |
| `dist/public-source.zip` | 对应源码附件 |
| `dist/AIReader-windows-preview.zip` | 完整桌面包 |
| `dist/AIReader-windows-preview.zip.sha256` | 下载校验值 |
| `dist/DELIVERY_MANIFEST.json` | 私有构建与核对记录，不作为用户下载内容 |

不要上传整个 `dist`。程序运行后的私人数据也不能重新压入公开包。

## 审查提交

在实际 Git 工作树执行；维护导出仓库通常是 `dist/public-source`，普通克隆则是克隆根目录：

```powershell
git status --short
git diff --stat
git diff --check
git ls-files --others --exclude-standard
```

检查新增文件、截图和依赖许可，确认没有密钥、数据库、PDF、日志或真实对话。自动扫描仅覆盖常见格式及已知本机凭据，不能识别全部隐私。

提交前确认 README、版本说明和包对应同一源码。源码目录是可阅读的仓库内容，不能只上传一个 ZIP。

## GitHub 仓库与版本

首次建仓库时，选择 **New repository（新建仓库）**、**Public（公开）**；导出已有 README、忽略文件和许可证，不再生成重复文件。只对未初始化的公开源码目录执行：

```powershell
git init -b main
git add .
git diff --cached --stat
git commit -m "Initial public preview"
git remote add origin https://github.com/YOUR_ACCOUNT/PaperPilot.git
git push -u origin main
```

替换 `YOUR_ACCOUNT`。已有仓库不重复 `git init` 或添加 origin；先检查差异，再提交和推送。身份缺失时在仓库中设置 `user.name` 与 `user.email`，不要把令牌或密码拼进 URL。

确认版本号和 CI 后，创建与软件包对应的标签：

```powershell
git tag -a v0.2.0-preview.1 -m "PaperPilot v0.2.0-preview.1"
git push origin v0.2.0-preview.1
```

这里的版本是更新草稿示例；实际发布需采用已经确认且未占用的标签，不覆盖旧标签。

在 **Releases（发布）→ Draft a new release（起草新版本）** 选择标签和对应提交，填写版本说明，上传桌面 ZIP、校验文件及所需源码 ZIP。预览版勾选 **This is a pre-release（这是预发布版本）**，先保存草稿并检查附件，再发布。说明来源见[版本记录](RELEASE_NOTES.md)。

README 使用 Releases 列表链接，避免猜测附件地址；需要直链时，从实际发布页面复制。仓库社交封面可使用 `docs/assets/readme/social-preview.png`，在 **Settings → Social preview（社交预览）** 上传。

操作参考：[创建仓库](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository)、[推送源码](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)、[管理 Release](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)。

## 发布说明必须包含

- 支持的系统、WebView2 依赖、自备 API 和数据去向。
- 对应源码、构建步骤、LICENSE 和第三方许可。
- 本版功能变化、升级与备份方式、测试条件及未验证范围。
- 不把离线测试写成研究准确率，也不把局部性能写成整轮提速。
- 干净 Windows 安装和代码签名尚未完成时继续标记预览版。

导入论文及第三方资料不纳入项目代码许可。公开 embedding 模型应保留其版本、校验清单、LICENSE 和 NOTICE。
