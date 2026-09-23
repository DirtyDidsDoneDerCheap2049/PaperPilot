# 开源与发布

这份指南用于发布 PaperPilot 的首个 Windows 预览版。2026-09-24 已整理本地公开文件；本轮不再做在线模型测试，也未创建远程仓库或上传文件。

源码仓库与桌面下载包分开管理。只提交 `dist/public-source`，不要在包含私人研究资料的项目根目录执行 `git add .`。

## 本次发布从哪里开始

如果已经拿到本地准备好的文件，直接从下方「首次推送 GitHub」开始，不必重复导出。

从包含 `src`、`scripts` 和 `dist` 的开发项目目录开始，待提交源码位于 `dist/public-source`。

| 本地文件 | 用途 |
|---|---|
| `dist/public-source/` | 公开源码；在此目录建立 Git 仓库并推送 |
| `dist/public-source.zip` | 同一份源码的备份包，可作为 Release 附件 |
| `dist/AIReader-windows-preview.zip` | 用户下载的完整 Windows 桌面包 |
| `dist/AIReader-windows-preview.zip.sha256` | 桌面包的校验值 |
| `dist/DELIVERY_MANIFEST.json` | 本地交付记录，检查包名、散列值和验证结果 |
| `docs/RELEASE_NOTES.md` | 可直接复制到 GitHub Release 的版本说明 |

通常只需上传桌面 ZIP 和它的 `.sha256`；源码通过 Git 推送，`public-source.zip` 可作为额外附件。`DELIVERY_MANIFEST.json` 是本机检查记录，无需上传。已有离线回归与桌面启动记录；没有干净系统安装验证或代码签名，所以本次标记为预发布。

`dist/current/` 与 `dist/preview/` 是开发者本机版本，可能带有私人工作区入口，不用于发布。后续源码有改动时，应重新测试和构建，再更新公开目录与校验值。

## 准备公开源码

在开发目录运行：

```powershell
./work/.venv/Scripts/python.exe scripts/prepare_release.py --check-only
./work/.venv/Scripts/python.exe scripts/prepare_release.py
```

如果开发环境位于 `.venv`，相应调整 Python 路径。脚本按白名单首次导出 `dist/public-source/` 与 `dist/public-source.zip`，并生成文件 SHA-256 清单。输出已存在时不会覆盖。后续构建用下方 `refresh_release.py` 更新固定目录：它会先核对旧清单，遇到手工修改、未知文件或源码删除便停止，不覆盖这些改动，也不改 `.git`。

导出不含工作区、数据库、论文、历史研究报告、本机凭据、内部审计记录与构建缓存。`docs/assets/` 中的公开展示图片会随源码导出，其中的应用截图使用隔离示例数据。检查会匹配常见密钥格式和已知本机凭据，只报告命中文件，不打印值。自动扫描不能识别所有敏感信息，上传前仍需查看文件列表和图片。

## 首次推送 GitHub

1. 登录 GitHub，点击右上角 **＋ → New repository（新建仓库）**。
2. **Repository name（仓库名）** 填 `PaperPilot`，选择 **Public（公开）**。关闭 **Add a README file（添加 README）**，不要添加 `.gitignore` 或许可证，导出目录中已有这些文件。
3. 点击 **Create repository（创建仓库）**，复制页面上的 HTTPS 仓库地址。
4. 在开发项目目录打开 PowerShell，进入准备好的公开源码目录。不要切回上级开发目录后执行提交命令。

```powershell
Set-Location -LiteralPath '.\dist\public-source'
git init -b main
git add .
git diff --cached --stat
git diff --cached --name-only
git commit -m "Initial public preview"
```

首次提交成功后，添加仓库地址并推送。`YOUR_ACCOUNT` 改成自己的 GitHub 用户名。

```powershell
git remote add origin https://github.com/YOUR_ACCOUNT/PaperPilot.git
git push -u origin main
```

替换 `YOUR_ACCOUNT`。首次提交若提示身份未配置，在这个仓库内设置 `git config user.name` 和 `git config user.email`，邮箱可用 GitHub 提供的 noreply 地址。使用登录授权，不把密码或访问令牌拼进 URL。

出现 `Author identity unknown` 时，先填写自己的提交身份，再重新执行 commit 和 push；已经成功的 init/add 不必重做。不要在聊天中发送密码或令牌。

```powershell
git config user.name "你的提交显示名"
git config user.email "你的 GitHub 提交邮箱"
git commit -m "Initial public preview"
```

提交成功后再执行上面的添加仓库地址与推送步骤。

如果此前执行 `git remote add origin` 已成功，后面不要重复添加；用 `git remote -v` 查看即可。推送后刷新仓库主页，应该能看到 README、源码目录和图片，而不是只有一个 ZIP。

提交源码目录的文件，不把 ZIP 当作唯一仓库内容。首次推送后检查 Actions；本地通过不代表远程 CI 已通过。今后保留这个 Git 目录，不要直接用重新导出的副本覆盖 `.git`。

如果公开目录已经有 Git 仓库，先用 `git status` 和 `git remote -v` 确认状态；不要重复初始化或重复添加 `origin`。GitHub 页面中查看 **Actions（操作）** 的运行结果，失败时先打开对应日志处理。

## 桌面下载包

以下命令供后续重新构建使用，在开发项目根目录执行。沿用固定目录和包名，刷新前先完成测试；只有确有回退需求的版本才单独保留，不因每次验证创建一套新目录。

```powershell
./work/.venv/Scripts/python.exe scripts/build_desktop.py
./work/.venv/Scripts/python.exe scripts/prepare_release.py --desktop dist/public-desktop/AIReader
./work/.venv/Scripts/python.exe scripts/refresh_release.py
```

公开构建默认没有 `reader-workspace.json`，不会自动打开开发者日常工作区。`--local-workspace` 只用于本机测试包，不能把该包直接上传。必须打包整个 AIReader 文件夹，单独 EXE 无法运行。

软件仍需 WebView2 Runtime，尚无代码签名与自动更新。干净 Windows 系统安装未验证，本次按已有检查结果发布预览版，不要求再做一轮研究任务测试，也不写成正式稳定版。

确认 CI 后，在 GitHub Releases 创建版本，例如 `v0.1.0-preview.1`，选择对应源码提交，附桌面 ZIP，说明 Windows、WebView2、用户自备 API 和已知限制。当前仓库采用 AGPL-3.0-only，保留 LICENSE、THIRD_PARTY.md、随包第三方许可和对应源码构建说明。不要将论文资料纳入代码许可。

## 在 GitHub 提供软件下载

1. 打开仓库首页右侧 **Releases（发布）**，选择 **Draft a new release（起草新版本）**。
2. 在 **Choose a tag（选择标签）** 创建 `v0.1.0-preview.1`，确认 **Target（目标）** 是这次桌面包对应的源码提交。
3. **Release title（版本标题）** 填 `PaperPilot v0.1.0-preview.1`。把 [RELEASE_NOTES.md](RELEASE_NOTES.md) 的正文复制到 **Describe this release（版本说明）**。
4. 上传 `AIReader-windows-preview.zip`、对应 `.sha256`，以及需要附带的 `public-source.zip`。不要上传本机日常运行目录、论文或数据库。
5. 勾选 **This is a pre-release（这是预发布版本）**。先点 **Save draft（保存草稿）**，检查附件和说明；确认后点 **Publish release（发布版本）**。
6. 发布后复制 Release 页面的真实地址，更新中英文 README 的下载段落，去掉“尚未发布”的说明并提交。不要提前填写猜测的下载地址。若启用了不可变发布，先保存草稿并上传齐附件，再点击发布。

完整版本说明已单独保存，后续只改 `RELEASE_NOTES.md`，避免指南与 Release 内容各留一份后逐渐不一致。

上述操作于 2026-09-24 对照 GitHub 官方说明核对：[创建仓库](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository)、[推送本地源码](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)、[管理 Release](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)。

## 发布前核对

仓库首页使用中文 README，可通过顶部 English 链接打开英文版。封面、真实应用截图和来源说明位于 `docs/assets/readme/`；在 GitHub 仓库 Settings（设置）中找到 Social preview（社交预览），上传其中的 `social-preview.png`。这是分享链接时的封面，不是应用界面截图。

- 文件列表里没有 `.env`、`.secrets.json`、数据库、PDF、SQL 备份和真实对话。
- 模型服务地址与默认模型不是私人代理或不可公开的内部配置。
- README 只写实际验证结果；MySQL 专项、真实论文效果与性能没有验证就明确说明。
- 下载包与本次源码一致，运行后的私人工作区不要再压回发布包。
- 尚未提供远程仓库时，只完成本地准备，不声称已上传或 CI 已通过。
