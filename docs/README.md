# 语音转文字免跳转 —— 逆向 + 可二次开发源码

对 `语音转文字免跳转-1.2-iphoneos-arm64e.deb`（Lessica / com.wkk.wtvrbglauncher）
做的完整拆包逆向，以及一份「逐条指令对齐」的可编译重建版，方便往里加功能。

## 结论先说

1. **这个包里没有任何加密/授权/校验**（无序列号、无网络校验、无反调试、无混淆，
   代码段只有 3312 字节、全 828 条指令已 100% 读完）。
   所以「破解」这件事不存在 —— 它本来就是免费插件，没有锁。
2. 真正的价值是：**逻辑已经完全还原成源码**，可以直接改、直接编译出包。

## 目录

```
ANALYSIS.md                  逆向分析报告（hook 清单 / 全局变量 / 伪代码 / 特征值表）
src/Tweak.xm                 还原版 Tweaker 源码（Logos）
src/Makefile, control        根模式(rootless/var-jb)打包配置
src/prefs/                   设置界面（PreferenceBundle）
.github/workflows/build.yml  GitHub Actions 云端编译出 deb（macos-14 + Theos）
tools/annotated_disasm.txt   全部 828 条指令的带注释反汇编
tools/ana.py, mdis.py        反汇编/交叉引用分析脚本（python3 + capstone）
original/orig.deb            原始包备份
```

## 核心机制（一句话）

输入法点麦克风时，系统会「拉起输入法宿主 App」，产生一次场景切换；
插件 hook `-[SBWorkspaceTransitionContext animationDisabled]` 认出这次切换是语音跳转，
返回 `YES` 让转场不做动画（瞬间完成），同时记下你原来所在的 App；
再 hook `-[SBApplicationSceneView layoutSubviews]`，把原 App 窗口的快照盖上去，
下次布局时按设置里的延时/时长淡出移除 —— 视觉上「没有跳转」。

## 四个 hook

| 目标 | 方法 | 作用 |
|---|---|---|
| SBWorkspaceTransitionContext | `-animationDisabled` | ★ 判定是否语音跳转（5 家输入法规则） |
| SBApplicationSceneView | `-layoutSubviews` | ★ 快照遮罩 + 淡出 |
| SBActivationSettings | `-setFlag:forActivationSetting:` | 恒等钩子（原版即如此，可删） |
| SBActivationSettings | `-setObject:forActivationSetting:` | 恒等钩子（原版即如此，可删） |

## 内置输入法特征值（加新输入法照着填）

| 输入法 | bundleID | 判定位置 | 特征 |
|---|---|---|---|
| 微信输入法 | com.tencent.wetype | setting 5 (NSURL) | scheme `wetype` + host `WXKBURL_STARTVOICERECORD` |
| 百度输入法 | com.baidu.inputMethod | setting **14** (NSString) | == `com.baidu.inputMethod.keyboard` |
| 搜狗输入法 | com.sogou.sogouinput | setting 5 (NSURL) | scheme `com.sogou.sogouinput.ext` + 含 `:path=SpeechInput&` |
| 讯飞输入法 | com.iflytek.inputime | setting 5 (NSURL) | scheme `xfime` + host `activate_for_record` |
| 豆包输入法 | com.bytedance.ios.doubaoime | setting 5 (NSURL) | scheme `oime` + host `start_asr_from_keyboard` |
| Typeless | com.typeless.mobile | 扫描全部 setting | scheme `typeless`（来自其 Info.plist `CFBundleURLTypes`） |

> Typeless 的 setting key 尚未实测，所以用了「扫描全部 activation setting 找 scheme 匹配的 URL」
> 的匹配方式（规则表里 `isString = 2`），不依赖固定 key。

## 通用模式（支持任意输入法，含 Typeless）

不需要知道对方的 URL scheme，也能支持：

> 被拉起的 App 自己的 `PlugIns/*.appex` 里带 `NSExtensionPointIdentifier = com.apple.keyboard-service`
> → 它就是输入法宿主 App → 这次跳转按语音跳转处理。

* 实现：`WVAppHostsKeyboardExtension()`，读 App bundle 的 PlugIns 目录，
  读不到时退回用 `UIKeyboardInputModeController.enabledInputModeIdentifiers` 前缀匹配；结果按 bundleID 缓存。
* 为降低误判，通用模式还要求「本次转场带自定义 scheme 深链」或「系统已置 42 号 flag」。
* 已在精确规则表里的输入法，如果把它的开关关掉了，通用模式也不会再兜它。
* 设置里可开关：**自动识别其他输入法（通用模式）**。
* 覆盖对象举例：Typeless（com.typeless.mobile）、Wispr Flow、Flow 输入法、声印、ninan、懒懒键盘…

## 跳转日志（补精确规则用）

打开设置里的 **记录跳转日志（排查用）**，然后去 App 里点一次输入法麦克风，
日志写到 `/var/mobile/Library/Logs/WVNoJump.log`，每行形如：

```
[日期] SKIP(cur=<被拉起的App> prev=<原App> deep=<深链> f42=0) s5=NSURL(typeless://...) s14=... f42=...
```

`MATCH` / `GENERIC` / `FLAG42` / `SKIP` 是四种判定结果，后面的 `sN=值` 是 SpringBoard
这次收到的**全部 activation setting**。把一行发出来，就能把该输入法写成精确规则。


## 编译

```bash
export THEOS=~/theos
make clean && make package FINALPACKAGE=1
```

或推到 GitHub 后跑 `.github/workflows/build.yml`（macos-14，自动装 Theos + 从
`theos/sdks` 拉 iPhoneOS16.5.sdk，产出 **rootless** arm64+arm64e deb）。

> 注意：原包是 **rootless**（`THEOS_PACKAGE_SCHEME = rootless`），
> 装到 iOS 17.3 RootHide 设备上路径才对。

## 还能加什么（扩展点已留好）

* `kIMERules[]`：加输入法（QQ 输入法 / 手心 / Gboard / 百度国际版…）
* 自定义规则表：把规则放 plist/JSON，用户不用重编译就能加输入法
* 按 App 白名单：只在指定 App 里生效（或反过来）
* 日志：把遇到的「未知输入法 URL」写到文件，方便用户自己补规则
* 视觉层：改成淡入/缩放、保留原动画选项、只在特定方向生效
