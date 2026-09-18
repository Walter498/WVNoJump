# WVNoJump — 语音转文字免跳转 (Plus)

中文输入法「语音转文字」时不再整屏跳转的 SpringBoard 插件。

机制逆向自 Lessica 的 `语音转文字免跳转 1.2`（`com.kwk.wtvrbglauncher`），
逻辑按其 arm64 slice 的 828 条指令逐条还原，并在此基础上增加：

* **通用模式**：被拉起的 App 只要自带键盘扩展（`com.apple.keyboard-service`）就认作输入法宿主
  → Typeless、Wispr Flow、声印、ninan、懒懒键盘……无需单独适配
* **Typeless 精确规则**（`com.typeless.mobile`，scheme `typeless`）
* **未知输入法日志**：一键把那次跳转收到的全部 activation setting 落盘，方便补精确规则
* 支持的输入法：微信 / 百度 / 搜狗 / 讯飞 / 豆包 / Typeless / 其他（通用模式）

## 工作原理

1. 输入法点麦克风 → 系统把输入法宿主 App 拉起到前台
2. hook `-[SBWorkspaceTransitionContext animationDisabled]`，认出这次是语音跳转 → 返回 `YES`
   （该次转场不做动画 = 瞬间切换），同时记下跳转前的前台 App
3. hook `-[SBApplicationSceneView layoutSubviews]`，把原 App 窗口的快照盖上去，
   下次布局时按设置里的延时/时长淡出移除 —— 视觉上「没有跳转」

## 支持的输入法特征值

| 输入法 | bundleID | 判定 |
|---|---|---|
| 微信输入法 | `com.tencent.wetype` | setting 5 URL：scheme `wetype` + host `WXKBURL_STARTVOICERECORD` |
| 百度输入法 | `com.baidu.inputMethod` | setting 14 字符串 `com.baidu.inputMethod.keyboard` |
| 搜狗输入法 | `com.sogou.sogouinput` | setting 5 URL：scheme `com.sogou.sogouinput.ext` + 含 `:path=SpeechInput&` |
| 讯飞输入法 | `com.iflytek.inputime` | setting 5 URL：scheme `xfime` + host `activate_for_record` |
| 豆包输入法 | `com.bytedance.ios.doubaoime` | setting 5 URL：scheme `oime` + host `start_asr_from_keyboard` |
| Typeless | `com.typeless.mobile` | 扫描全部 setting 找 scheme `typeless` 的 URL |

## 编译

```bash
export THEOS=~/theos
make clean && make package FINALPACKAGE=1
```

或直接跑 `.github/workflows/build.yml`（macos-14 + Theos，产出 rootless 的
`iphoneos-arm64` 与 `iphoneos-arm64e` 两个 deb）。

> 插件走 **rootless** 方案（`THEOS_PACKAGE_SCHEME = rootless`），
> 装到 rootless / RootHide 设备上路径才对。

## 设置

设置 → 语音转文字免跳转：总开关、各输入法开关（未安装的自动隐藏）、
通用模式、跳转日志、快照淡出延时/时长。

日志路径：`/var/mobile/Library/Logs/WVNoJump.log`

## 逆向资料

`docs/` 下有完整分析报告、带交叉引用注释的反汇编、分析脚本。
