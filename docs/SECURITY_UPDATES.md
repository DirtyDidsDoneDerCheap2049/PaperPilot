# 安全依赖更新

## 2026-10-22

本次核对 eMudhra / emSign 证书事件、PHP 紧急更新和 Node.js 十月安全更新。

| 事件 | PaperPilot 中的影响 | 处理 |
| --- | --- | --- |
| emSign 中间证书私钥泄露 | 旧版 `certifi 2026.7.22`、pip 内置证书库及打包的 gRPC 独立证书库包含 `emSign ECC Root CA - G3` | 固定为 `certifi 2026.10.19`、`pip 26.2.2`；构建时从 gRPC 副本中仅移除该根证书，构建与发布检查其指纹 |
| PHP CVE-2026-109618、CVE-2026-109619 | 项目不运行 PHP，也不包含 PHP-FPM、mod_php 或 PHP CGI 后端 | 无需修改 PaperPilot；单独部署的 PHP 服务需按其厂商公告升级 |
| Node.js 十月安全更新 | 桌面运行使用 Python、C++ 和系统 WebView2；Node.js 用于开发测试及截图工具 | CI 主动获取 22.x 最新补丁，不依赖 runner 的旧缓存；本地开发环境需自行升级 |

Node.js 对应的修复版本为 **22.23.4、24.22.1、26.12.1**，使用同一受支持分支的后续补丁版也可。PHP 对应修复版本为 **8.2.35、8.3.36、8.4.27、8.5.12**。

### 验证证书库

在项目的 Windows Python 3.12 开发环境中执行：

```powershell
work/.venv/Scripts/python.exe scripts/check_tls_trust.py
work/.venv/Scripts/python.exe scripts/check_tls_trust.py --desktop dist/public-desktop/AIReader
work/.venv/Scripts/python.exe scripts/check_tls_trust.py --desktop dist
```

检查会直接解析实际 PEM 证书并按 SHA-256 指纹比对，不将执行环境额外注入的信任根误算为包内文件。构建前另检查 `certifi` 最低版本为 `2026.10.19`；该版本移除受影响的 G3，并恢复此前一版一并移除的其他未受影响的 emSign 根证书。

开发环境检查同时验证 pip 的版本及其独立证书库；桌面检查同时验证包内 certifi 与 gRPC 的证书库。pip 使用 Python 安全团队公布的 wheel SHA-256 引导升级，CI 不会重新安装锁文件中此前的旧版本。

已分发的桌面包包含自己的 Python 证书库，不会随系统或开发环境的 pip 更新而自动修复，需要分发重建后的完整软件包。若网站仍使用已不受信任的证书链，应由网站更新证书，不应通过 `verify=False` 绕过校验。

如果构建用 Python 的 `ssl.py` 引用了附带的 OpenSSL 吊销清单，打包会同时保留该资源，避免冻结后因缺少文件导致网络客户端初始化失败。该清单中的拒绝项与 `certifi` 中的信任根分开检查。

这些检查只覆盖 Python 的 `certifi` 证书库。Windows、WebView2、浏览器和其他应用的信任库由各自的更新机制维护；本项目检查不代表整台电脑或所有第三方服务完成了安全审计。

### 官方依据

- [Mozilla：eMudhra 中间证书事件](https://blog.mozilla.org/security/2026/10/20/protecting-firefox-users-following-the-compromise-of-emudhra-intermediate-certificates/)
- [certifi：GHSA-6q48-6c7w-9gw2](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2)
- [certifi 2026.10.19 发布文件](https://pypi.org/project/certifi/2026.10.19/)
- [Python 安全团队：受影响的 Python 工具与安全升级方式](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576)
- [PHP：2026-10-21 紧急安全公告](https://www.php.net/)
- [Node.js：2026-10-21 安全更新](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases)
