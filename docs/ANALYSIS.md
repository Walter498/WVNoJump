# 语音转文字免跳转 1.2 —— 逆向分析报告

分析对象：`语音转文字免跳转-1.2-iphoneos-arm64e.deb`
分析日期：2026-09-18　分析工具：dpkg-deb / ipsw 3.1.722 / python3+capstone 5.0.3

---

## 1. 包结构

```
DEBIAN/control                                   Package com.wkk.wtvrbglauncher  v1.2
Library/MobileSubstrate/DynamicLibraries/
    WTVRBGLauncher.dylib    166 KB  (arm64 + arm64e fat)   ← 全部逻辑在这里
    WTVRBGLauncher.plist    Filter: { Bundles: [com.apple.springboard] }
Library/PreferenceLoader/Preferences/
    WTVRBGPrefs.plist       设置入口
Library/PreferenceBundles/WTVRBGPrefs.bundle/
    WTVRBGPrefs  (arm64+arm64e)   Root.plist / Info.plist / Empty.plist / icon
```

* 作者 Lessica（乌龙工作室 / 82flex），Depends: `firmware(>=14.0), mobilesubstrate, preferenceloader`
* **rootless（RootHide）布局**：deb 内路径不含 `/var/jb`，但 `LC_ID_DYLIB` 为
  `@loader_path/.jbroot/Library/MobileSubstrate/DynamicLibraries/WTVRBGLauncher.dylib`
  → 必须用 `THEOS_PACKAGE_SCHEME = rootless` 编译，装到 iOS 17.3 RootHide 上正好。
* 两个 slice 大小完全一致（0x10ba0），arm64e 只是 arm64 + PAC 变体，逻辑相同。
* `LC_ENCRYPTION_INFO` = not encrypted，无壳、无混淆、无花指令。

## 2. 有没有「破解」这回事？——没有

我把 `__text` 全部 828 条指令、`__cstring`、`__objc_methname`、objc selector stub、
chained fixups 导入表全部过了一遍：

| 项目 | 结论 |
|---|---|
| 授权 / 序列号 / 激活校验 | **不存在**（无网络请求、无 Keychain、无设备指纹、无时间炸弹） |
| 反调试 / 反注入 / 完整性校验 | **不存在** |
| 加密字符串 / 资源 | **不存在**（`__cstring` 只有 0x22f 字节，全是明文） |
| 隐藏逻辑 | **不存在**（代码段仅 3312 字节，已 100% 覆盖） |
| 混淆 | 无。变量/选择器名原样保留 |

这是个**免费**插件（设置页里那句「请支持我们的其他付费作品」只是宣传按钮）。
所以「完美破解」在这里等于「已 100% 读懂」，没有锁可以开。真正能做的、
也是你想要的，是**重建 + 加功能**（见第 6 节）。

## 3. 四个 hook（`%ctor` 里注册）

| # | 目标类 | 目标方法 | 替换实现 | 作用 |
|---|---|---|---|---|
| 1 | `SBActivationSettings` | `-setFlag:forActivationSetting:` | 0x40f0 | **空钩子**（只是 `%orig`，无行为改变） |
| 2 | `SBActivationSettings` | `-setObject:forActivationSetting:` | 0x40fc | **空钩子**（同上） |
| 3 | `SBWorkspaceTransitionContext` | `-animationDisabled` | 0x4108 | ★ 核心判定逻辑 |
| 4 | `SBApplicationSceneView` | `-layoutSubviews` | 0x46c8 | ★ 快照遮罩 / 淡出动画 |

另外 `%ctor` 里：

```c
reloadSettings();
CFNotificationCenterAddObserver(CFNotificationCenterGetDarwinNotifyCenter(), NULL,
    reloadSettings, CFSTR("com.82flex.wtvrbgprefs/saved"), NULL,
    CFNotificationSuspensionBehaviorDeliverImmediately);
```

> 1/2 号钩子的替换 IMP 是一段 `ldr x4,[&old]; br x4` 的裸跳板 —— 也就是
> 「调用原实现」的恒等钩子，语义上什么都没做（推测是作者留的占位/调试残留）。
> 重建时可以原样保留，也可以删掉，行为完全一致。

## 4. 全局状态与偏好读取

`__DATA.__bss` 中的全局量（地址 = 还原出来的符号）：

| 地址 | 符号 | 说明 |
|---|---|---|
| 0xc128 | `gLastBundle` | `NSString *`，记住「跳转前的前台 App」 |
| 0xc130 | `gSnapshot` | `UIView *`，盖上去的快照视图 |
| 0xc138/0xc140/0xc148/0xc150 | `old1..old4` | MobileSubstrate 回填的原 IMP |
| 0xc158 | `gPrefs` | `NSUserDefaults initWithSuiteName:@"com.82flex.wtvrbgprefs"` |
| 0xc160 ~ 0xc165 | `gEnabled` `gWeType` `gBaidu` `gSogou` `gXunFei` `gDoubao` | BOOL 缓存 |
| 0xc118 / 0xc120 | `gInterval` / `gDelay` | 秒，= AnimationInterval/1000、AnimationDelay/1000 |
| 0xc168 | — | 懒加载缓存 `objc_getClass("SBApplicationSceneEntity")` |

`reloadSettings()`（0x4908）用 `[gPrefs dictionaryRepresentation]` + `objectForKeyedSubscript:`
逐个读 key，**key 缺失时默认 YES**（与 Root.plist 的 `default=true` 一致），
`AnimationInterval` / `AnimationDelay` 缺失时默认 `300/1000.0 = 0.3`。

偏好键：`IsEnabled`、`IsWeTypeEnabled`、`IsBaiduEnabled`、`IsSogouEnabled`、
`IsXunFeiEnabled`、`IsDoubaoEnabled`、`AnimationDelay`(0~900)、`AnimationInterval`(0~900)。

## 5. 核心逻辑（还原伪代码）

### 5.1 `-[SBWorkspaceTransitionContext animationDisabled]`

```objc
- (BOOL)animationDisabled {
    BOOL orig = %orig;                       // 记录系统原值
    if (!gEnabled) return orig;

    id prev = [[self previousEntities] anyObject];   // 旧实体（你原来在的 App）
    id ent  = [[self entities] anyObject];           // 新实体（要被拉起的输入法 App）
    Class sceneEntity = objc_getClass("SBApplicationSceneEntity");
    if (![prev isKindOfClass:sceneEntity]) return orig;
    if (![ent  isKindOfClass:sceneEntity]) return orig;

    NSString *prevBundle = [[prev application] bundleIdentifier];
    NSString *curBundle  = [[ent  application] bundleIdentifier];
    SBActivationSettings *s = [ent activationSettings];

    /* ---- 百度：设置 key 14 是字符串 bundle id ---- */
    id v14 = [s objectForActivationSetting:14];
    if (gBaidu && [curBundle isEqualToString:@"com.baidu.inputMethod"]
        && [v14 isKindOfClass:NSString.class]
        && [v14 isEqualToString:@"com.baidu.inputMethod.keyboard"])
        { gLastBundle = prevBundle; return YES; }

    /* ---- 其余四家：设置 key 5 是一个 NSURL，看 scheme/host/path ---- */
    NSURL *u = [s objectForActivationSetting:5];

    if (gWeType && [curBundle isEqualToString:@"com.tencent.wetype"]
        && [u isKindOfClass:NSURL.class]
        && [[u scheme] isEqualToString:@"wetype"]
        && [[u host] isEqualToString:@"WXKBURL_STARTVOICERECORD"])
        { gLastBundle = prevBundle; return YES; }

    if (gSogou && [curBundle isEqualToString:@"com.sogou.sogouinput"]
        && [u isKindOfClass:NSURL.class]
        && [[u scheme] isEqualToString:@"com.sogou.sogouinput.ext"]
        && [[u absoluteString] containsString:@":path=SpeechInput&"])
        { gLastBundle = prevBundle; return YES; }

    if (gXunFei && [curBundle isEqualToString:@"com.iflytek.inputime"]
        && [u isKindOfClass:NSURL.class]
        && [[u scheme] isEqualToString:@"xfime"]
        && [[u host] isEqualToString:@"activate_for_record"])
        { gLastBundle = prevBundle; return YES; }

    if (gDoubao && [curBundle isEqualToString:@"com.bytedance.ios.doubaoime"]
        && [u isKindOfClass:NSURL.class]
        && [[u scheme] isEqualToString:@"oime"]
        && [[u host] isEqualToString:@"start_asr_from_keyboard"])
        { gLastBundle = prevBundle; return YES; }

    /* ---- 兜底：系统已自行置了 42 号 flag（回程方向），且上一个 App 就是某输入法 ---- */
    if ([s flagForActivationSetting:42]) {
        if (gWeType && [prevBundle isEqualToString:@"com.tencent.wetype"])   return YES;
        if (gBaidu  && [prevBundle isEqualToString:@"com.baidu.inputMethod"]) return YES;
        if (gSogou  && [prevBundle isEqualToString:@"com.sogou.sogouinput"])  return YES;
        if (gXunFei && [prevBundle isEqualToString:@"com.iflytek.inputime"])  return YES;
        if (gDoubao && [prevBundle isEqualToString:@"com.bytedance.ios.doubaoime"]) return YES;
    }
    return orig;
}
```

**各输入法语音按钮的真实特征值（从二进制提取，可直接用于扩展）**

| 输入法 | 宿主 App bundleID | 判定位置 | 特征 |
|---|---|---|---|
| 微信输入法 | `com.tencent.wetype` | setting 5 (NSURL) | scheme `wetype` + host `WXKBURL_STARTVOICERECORD` |
| 百度输入法 | `com.baidu.inputMethod` | setting **14** (NSString) | == `com.baidu.inputMethod.keyboard` |
| 搜狗输入法 | `com.sogou.sogouinput` | setting 5 (NSURL) | scheme `com.sogou.sogouinput.ext` + absoluteString 含 `:path=SpeechInput&` |
| 讯飞输入法 | `com.iflytek.inputime` | setting 5 (NSURL) | scheme `xfime` + host `activate_for_record` |
| 豆包输入法 | `com.bytedance.ios.doubaoime` | setting 5 (NSURL) | scheme `oime` + host `start_asr_from_keyboard` |
| Typeless | `com.typeless.mobile` | 全 setting 扫描 | scheme `typeless`（Simpl.y LLC / Simply LLC，v2.6.2，iOS 16.1+） |

### 5.2 `-[SBApplicationSceneView layoutSubviews]`

```objc
- (void)layoutSubviews {
    %orig;
    if (!gEnabled) return;
    NSString *bid = [[self application] bundleIdentifier];
    if (!bid) return;

    if ([gLastBundle isEqualToString:[[self application] bundleIdentifier]]) {
        /* 命中「上一个是它」→ 清标记 */
        gLastBundle = nil;
        if (gSnapshot == nil) {                       // 第一次：直接盖快照
            UIView *win = [self window];
            gSnapshot = [win snapshotViewAfterScreenUpdates:NO];
            [gSnapshot setFrame:[win bounds]];
            [gSnapshot setUserInteractionEnabled:NO];
            [win addSubview:gSnapshot];
            return;
        }
    } else if (gSnapshot == nil) {
        return;                                       // 无关场景，什么都不做
    }

    /* 第二次：把它淡掉并移除 —— 相当于「补一次淡出动画」 */
    UIView *snap = gSnapshot; gSnapshot = nil;
    [UIView animateWithDuration:gInterval delay:gDelay options:0
                     animations:^{ [snap setAlpha:0]; }
                     completion:^(BOOL f){ [snap removeFromSuperview]; }];
}
```

概括这套机制：
1. 用户点输入法麦克风 → 系统把输入法 App 拉起到前台；
2. 插件的 3 号钩子认出「这是语音录制跳转」，把该次转场标成 **animationDisabled = YES**（瞬间切换，没有推拉动画），并记下你原来所在的 App；
3. 4 号钩子在场景视图布局时，抓一张原窗口快照盖上去（视觉上「还停在原 App」），
   下次布局时再把快照按 `AnimationDelay/AnimationInterval` 淡出移除。

## 6. 设置界面（WTVRBGPrefs.bundle）

`WTVRBGRootListController`：`loadSpecifiersFromPlistName:@"Root"`，然后用
`LSApplicationProxy applicationProxyForIdentifier:` + `isInstalled` 过滤掉
**未安装的输入法**对应的开关；全被过滤掉时改加载 `Empty.plist`（「未找到支持的输入法」）。
「「乌龙工作室」倾情献制」按钮 = `support` → 打开 `https://havoc.app/search/82Flex`。

## 7. 重建 / 二次开发要点

* **必须** rootless：`THEOS_PACKAGE_SCHEME = rootless`，否则 RootHide(iOS 17.3) 上路径不对。
* 需要自己声明 SpringBoard 私有类（只需要 4 个类 + 几个方法，已在 `src/Tweak.xm` 里写好）。
* 编译完记得 `ldid -S` 自签（theos `make package` 自动处理）。
* 原包 arm64+arm64e 双架构：`ARCHS = arm64 arm64e`。
* 扩展点非常干净，加功能基本只动三处：① IME 规则表（第 5.1 节的判定）
  ② 快照视觉层（5.2）③ 偏好键（reloadSettings + Root.plist）。

详情与可编译源码见 `src/`，逐指令反汇编见 `text.asm`。
