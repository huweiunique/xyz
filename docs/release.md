# 发版：APK / IPA / Docker

Nightly 的 GitHub 标签和 Docker 镜像标签均固定为 `nightly`，每次更新覆盖；
Docker 不再额外生成 `nightly-<sha>` 标签。已有远端历史标签不由此规则自动删除。
安装包文件名仍保留日期与提交号，便于识别下载版本。

## 按端触发

Nightly 的 `scope` 输入可选 `server`、`android`、`ios`、`apps`、`all` 或默认 `auto`。
例如只构建并发布服务端镜像：

```bash
gh workflow run nightly.yml --ref main -f scope=server
```

`server` 仅运行说明与 Docker job，APK、IPA 和安装包发布 job 均跳过，现有安装包及 Nightly
安装包标签保持原状。`android` 或 `ios` 仅发布所选安装包，校验完整上传后只清理该端旧附件，保留另一端及其它附件。

定时运行及手动 `auto` 通过 `.github/scripts/select_build_targets.js` 分别寻找各端最近成功构建并发布的提交，
再核对当前代码的实际打包依赖。无变更跳过，缺少成功记录或近期构建/发布失败时保留构建资格。
服务端加载的 JS 不自动触发 IPA；iOS 随包的 `app_settings.js`、`prefip_patch.js` 及版本标记脚本变化会触发 IPA。
Android 随包 HTML/HLS 的变化会触发 APK。纯文档和测试变化不触发安装包或镜像发布。
手动选择明确目标时按所选范围执行；只有 `all` 明确要求全量。各端构建独立控制并发，安装包发布串行更新同一 Release。

修复并完成必需验证后，按 `AGENTS.md` 的持续授权自动提交、推送并触发受影响范围；构建完成不表示运行服务已经部署。

2026-11-15 分流验证：默认检查 125 项，104 通过、21 跳过，另有 6 个浏览器测试类按默认开关整体跳过；
其中 6 项构建选择回归覆盖手动仅服务端、各端独立基线、真实随包依赖、无变更跳过及构建/发布失败重试，
4 项发布附件回归覆盖完整上传前保护和单端发布保留另一端及无关附件。actionlint 1.7.12 校验工作流通过。
本轮 iPhone 中央按钮修复只选择服务端构建；相关页面的 21 项实际服务验证记录见 [沉浸观看](immersive-viewing.md)。
GitHub 实际构建状态以对应提交和 Actions 运行回执为准。

## Nightly 附件保留规则

IPA 内的 Nightly 构建标识采用紧凑格式，例如 `N261113-9478076`：`N` 表示 Nightly、
`261113` 为 UTC 构建日期（年月日各两位）、末尾为七位提交号。完整可见版本为
`1.2.0.8-N261113-9478076`，显示在 iOS 原生设置及“我的 → 设置”入口中。
日期与提交来自同次发布的文件名后缀，跨 UTC 午夜的构建也保持一致。
构建标识写入 IPA 的 Info.plist，不改变系统数字版本字段；正式版仅显示基线版本。

Nightly Release 分别保留最近一次成功发布的 APK 与未签名 IPA。发布流程先核对本次所选
安装包已完整上传，再清理相同类型的旧附件；未重建的另一端和其它附件保留，构建或上传失败时保留此前可用安装包。
正式版不执行此清理。成功上传后同时将 Nightly 标签向前同步到本次提交，避免源码链接仍指旧版。
GitHub 自动生成的 Source code (zip / tar.gz) 属于标签源码下载链接，
不属于上传附件，不能通过 Release 附件删除接口单独移除。

## 打一个 tag 就全自动

```bash
git tag -a v1.2.0.8 -m "HGhub 1.2.0.8"
git push origin v1.2.0.8
```

触发 **Release 工作流**（`.github/workflows/release.yml`），一个 run 里两个 job，
产物都进同一个 **GitHub Release**：

| job | runner | 产物 | 说明 |
| --- | --- | --- | --- |
| `apk` | ubuntu-latest | `HGhub-1.2.0.8.apk` | debug 签名，可直接安装 |
| `ipa` | macos-latest | `HGhub-1.2.0.8-unsigned.ipa` | 未签名，需重签 |

另一条 `backend-tests.yml` 在 push 到 main / PR 时跑，不参与发版。

不想打 tag 时也可以手动跑：Actions → 选工作流 → Run workflow，可填 `app_url` / `access_key`。
手动跑的产物在 **Artifacts** 里，不进 Release。

## 客户端里的「后端地址 + 访问密钥」

**两个客户端都有这两项配置**，优先级：App 内设置 > 构建时内置 > 首次打开手填。

| 客户端 | 设置入口 | 构建时注入 |
| --- | --- | --- |
| APK | 首次打开的设置页（地址 + 密钥），可清除后重填 | `APP_URL` / `APP_ACCESS_KEY`（Gradle：`-PappUrl` / `-PappAccessKey`） |
| IPA | 我的 → 设置：修改服务地址、可选密钥、测试连接 | 仅 `APP_URL` 写入 `AppConfig.plist`；密钥不参与构建 |

密钥最终以 `?key=` 附加到入口 URL，后端据此下发 Cookie，之后请求自动带上。

```bash
# 手动构建并注入（产物在 Actions → Artifacts）
gh workflow run release.yml \
  -f app_url="http://<IP>:8081/" \
  -f access_key="<与服务端 HGHUB_ACCESS_KEY 相同的值>"

# 本地构建
APP_URL="http://<IP>:8081/" APP_ACCESS_KEY="<KEY>" bash android/build-apk-manual.sh
APP_URL="http://<IP>:8081/" ACCESS_KEY="<KEY>" bash ios/build-ipa.sh
```

> 密钥会写进 app 包（dex / AppConfig.plist）。不想内置就留空，让用户首次打开手填；
> 网页本身也会弹「需要访问密钥」输入框（存 30 天 Cookie）。

## iOS 未签名 IPA 怎么用

未签名 IPA **不能直接装到 iPhone**（iOS 只认签名 + 描述文件）。三种方式：

1. **自签**（免费，7 天有效）：Sideloadly / AltStore / TrollStore
2. **开发者证书**：`codesign -f -s "Apple Development: xxx" Payload/HGhub.app`，
   再 `ios-deploy` 或 Xcode Devices 安装
3. **App Store / TestFlight**：需要 Apple Developer 账号（$99/年）+ 签名证书
   + provisioning profile，给 workflow 加 `-exportMethod app-store-connect` 与 secrets

工作流目前只产出**未签名包**（无需任何 Apple 账号），签名环节留给你按需接入。

## macOS runner 与打包方式（实测）

- `macos-14` 已于 2026-11-02 退役，用它会瞬间被 cancel。
- `macos-latest` / `macos-15` 有时能秒级分配，有时排队 15 分钟后取消
  （`duration_ms: 0`、无日志）。这是 GitHub 侧容量问题，重跑 `Release` 即可。
- **打包方式**：iOS 26.5 SDK 不允许 ad-hoc 签名，且 `CODE_SIGNING_ALLOWED=NO`
  时 `xcodebuild archive` 不出 archive。所以脚本用 `xcodebuild build` 产出无签名
  `.app`，再自己组装 `Payload/` 打成 IPA（Sideloadly/AltStore 负责重签）。
- 脚本会断言 `Assets.car` 存在，图标缺失时直接失败，不会推出白图标的包。

可用标签见 [actions/runner-images](https://github.com/actions/runner-images#available-images)。

## 版本号在哪改

| 位置 | 字段 |
| --- | --- |
| `android/app/src/main/AndroidManifest.xml` | `versionCode` / `versionName` |
| `android/app/build.gradle` | `versionCode` / `versionName` |
| `android/build-apk-manual.sh` | `VERSION_CODE` / `VERSION_NAME` / 产物文件名 |
| `ios/HGhub/HGhub.xcodeproj/project.pbxproj` | `MARKETING_VERSION` / `CURRENT_PROJECT_VERSION` |
| `Dockerfile` / `docker-compose.yml` | 镜像 tag |
| `tests/test_static.py` | 断言了包名与版本，改版本要同步 |

## Docker 发布

见 [deploy.md](deploy.md)。目前只能本地构建（本机未登录 Docker Hub）。
