// WTVRBGLauncher 1.2 —— 反汇编还原版 + 通用输入法识别
//
// 原包：com.wkk.wtvrbglauncher   「语音转文字免跳转」 v1.2 (Lessica)
// 核心逻辑依据 arm64 slice 的 828 条指令重写；在此基础上增加了：
//   [1] 通用识别：只要被拉起的 App 带键盘扩展（com.apple.keyboard-service），
//       就当作输入法语音跳转处理 —— Typeless / Wispr Flow / 声印 / ninan 等
//       任何新输入法都不用加规则即可生效
//   [2] 精确规则表 kIMERules（原版 5 家）
//   [3] 未知输入法日志：把跳转时 SpringBoard 收到的全部 activation setting 落到
//       /var/mobile/Library/Logs/WVNoJump.log，方便补精确规则
//
// 机制概要：
//   1) 输入法点麦克风 → 系统拉起输入法宿主 App
//   2) hook -[SBWorkspaceTransitionContext animationDisabled]：认出是语音跳转 → 返回 YES
//      （该次转场不做动画 = 瞬间切换），并记下「跳转前的前台 App」
//   3) hook -[SBApplicationSceneView layoutSubviews]：对原窗口截图盖住，
//      下一次布局时按设置的延时/时长淡出移除 —— 视觉上「没有跳转」

#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <substrate.h>
#import <objc/runtime.h>

#define kPrefDomain   @"com.walter.voicenojump"
#define kPrefNotify   "com.walter.voicenojump/saved"
#define kLogPath      @"/var/mobile/Library/Logs/WVNoJump.log"

#pragma mark - SpringBoard 私有类最小声明

@interface SBApplication : NSObject
- (NSString *)bundleIdentifier;
@end

@interface SBActivationSettings : NSObject
- (id)objectForActivationSetting:(NSUInteger)setting;
- (BOOL)flagForActivationSetting:(NSUInteger)setting;
- (void)setFlag:(BOOL)flag forActivationSetting:(NSUInteger)setting;
- (void)setObject:(id)object forActivationSetting:(NSUInteger)setting;
@end

@interface SBApplicationSceneEntity : NSObject
- (SBApplication *)application;
- (SBActivationSettings *)activationSettings;
@end

@interface SBWorkspaceTransitionContext : NSObject
- (NSSet *)entities;
- (NSSet *)previousEntities;
- (BOOL)animationDisabled;
@end

@interface SBApplicationSceneView : UIView
- (SBApplication *)application;
@end

@interface LSApplicationProxy : NSObject
+ (instancetype)applicationProxyForIdentifier:(NSString *)identifier;
- (NSURL *)bundleURL;
- (BOOL)isInstalled;
@end

@interface UIKeyboardInputModeController : NSObject
+ (instancetype)sharedInputModeController;
- (NSArray *)enabledInputModeIdentifiers;
@end

#pragma mark - 全局状态（与原二进制 0xc118~0xc168 一一对应）

static NSUserDefaults *gPrefs;
static BOOL gEnabled = YES;
static BOOL gWeType = YES, gBaidu = YES, gSogou = YES, gXunFei = YES, gDoubao = YES;
static BOOL gTypeless = YES;
static BOOL gGeneric  = YES;    // 通用模式：带键盘扩展的 App 一律认
static BOOL gDebugLog = NO;     // 写日志
static double gInterval = 0.3;  // AnimationInterval / 1000
static double gDelay    = 0.3;  // AnimationDelay    / 1000
static NSString *gLastBundle;   // 原来的 0xc128
static UIView   *gSnapshot;     // 原来的 0xc130
static NSMutableDictionary<NSString *, NSNumber *> *gKBHostCache;

#pragma mark - [精确规则] 原版 5 家输入法的语音跳转特征值

// matchMode = 1 → 指定 setting 值是 NSString，与 contains 比较（百度专用）
// matchMode = 0 → 指定 setting 值是 NSURL，比较 scheme / host / absoluteString 子串
// matchMode = 2 → **扫描全部 setting**，找 scheme 匹配的 NSURL（key 未知时用，如 Typeless）
typedef struct {
    __unsafe_unretained NSString *bundleID;
    NSUInteger settingKey;
    NSUInteger matchMode;
    __unsafe_unretained NSString *scheme;
    __unsafe_unretained NSString *host;
    __unsafe_unretained NSString *contains;
} IMERule;

static const IMERule kIMERules[] = {
    // 微信输入法
    { @"com.tencent.wetype", 5, 0,
      @"wetype", @"WXKBURL_STARTVOICERECORD", nil },
    // 百度输入法（唯一一个用 NSString(setting 14) 的）
    { @"com.baidu.inputMethod", 14, 1,
      nil, nil, @"com.baidu.inputMethod.keyboard" },
    // 搜狗输入法
    { @"com.sogou.sogouinput", 5, 0,
      @"com.sogou.sogouinput.ext", nil, @":path=SpeechInput&" },
    // 讯飞输入法
    { @"com.iflytek.inputime", 5, 0,
      @"xfime", @"activate_for_record", nil },
    // 豆包输入法
    { @"com.bytedance.ios.doubaoime", 5, 0,
      @"oime", @"start_asr_from_keyboard", nil },
    // Typeless（Simply LLC，com.typeless.mobile）
    //   scheme 来自 Typeless.app/Info.plist 的 CFBundleURLTypes: ["typeless"]
    //   具体 host 与 setting key 暂未知 → 扫描全部 setting 找 scheme 匹配的 URL
    { @"com.typeless.mobile", 0, 2,
      @"typeless", nil, nil },
};
static const size_t kIMERuleCount = sizeof(kIMERules) / sizeof(kIMERules[0]);

// 每个精确规则对应的开关（与 kIMERules 顺序一一对应）
static BOOL *RuleFlag(size_t i) {
    static BOOL *flags[] = { &gWeType, &gBaidu, &gSogou, &gXunFei, &gDoubao, &gTypeless };
    return (i < sizeof(flags) / sizeof(flags[0])) ? flags[i] : &gEnabled;
}

// 该 bundleID 在精确规则表里，但用户把它整个关掉了 → 通用模式也不再兜它
static BOOL WVExactRuleDisabled(NSString *bundleID) {
    if (!bundleID.length) return NO;
    BOOL known = NO;
    for (size_t i = 0; i < kIMERuleCount; i++) {
        if ([bundleID isEqualToString:kIMERules[i].bundleID]) {
            known = YES;
            if (*RuleFlag(i)) return NO;   // 至少有一个开关是开的 → 不算被关掉
        }
    }
    return known;
}

#pragma mark - [通用识别] 这个 App 是不是「带键盘扩展」的输入法宿主

// 先看 App 自己的 PlugIns/*.appex 里有没有 keyboard-service 扩展（最准）；
// 读不到（沙盒/路径拿不到）就退回用「已启用键盘的输入模式标识符」做前缀匹配。
static BOOL WVAppHostsKeyboardExtension(NSString *bundleID) {
    if (!bundleID.length) return NO;
    if (!gKBHostCache) gKBHostCache = [NSMutableDictionary dictionary];
    NSNumber *hit = gKBHostCache[bundleID];
    if (hit) return hit.boolValue;

    BOOL result = NO;
    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *appPath = nil;

    Class proxyCls = NSClassFromString(@"LSApplicationProxy");
    if (proxyCls) {
        id proxy = [proxyCls applicationProxyForIdentifier:bundleID];
        if ([proxy respondsToSelector:@selector(bundleURL)]) {
            NSURL *u = [proxy bundleURL];
            if ([u isKindOfClass:NSURL.class]) appPath = [u path];
        }
    }
    if (!appPath.length) {
        NSBundle *b = [NSBundle bundleWithIdentifier:bundleID];
        if (b) appPath = [b bundlePath];
    }

    if (appPath.length) {
        NSString *plugins = [appPath stringByAppendingPathComponent:@"PlugIns"];
        NSArray *items = [fm contentsOfDirectoryAtPath:plugins error:NULL];
        for (NSString *item in items) {
            NSString *info = [[plugins stringByAppendingPathComponent:item]
                              stringByAppendingPathComponent:@"Info.plist"];
            NSDictionary *d = [NSDictionary dictionaryWithContentsOfFile:info];
            id ext = d[@"NSExtension"];
            if ([ext isKindOfClass:NSDictionary.class] &&
                [ext[@"NSExtensionPointIdentifier"] isEqualToString:@"com.apple.keyboard-service"]) {
                result = YES;
                break;
            }
        }
    }

    if (!result) {
        // 退路：已启用的键盘输入模式标识符，通常形如 <宿主bundleID>.Keyboard
        Class imc = NSClassFromString(@"UIKeyboardInputModeController");
        if (imc) {
            id ctrl = [imc sharedInputModeController];
            if ([ctrl respondsToSelector:@selector(enabledInputModeIdentifiers)]) {
                NSString *prefix = [bundleID stringByAppendingString:@"."];
                for (id ident in [ctrl enabledInputModeIdentifiers]) {
                    if (![ident isKindOfClass:NSString.class]) continue;
                    if ([ident isEqualToString:bundleID] || [ident hasPrefix:prefix]) {
                        result = YES;
                        break;
                    }
                }
            }
        }
    }

    gKBHostCache[bundleID] = @(result);
    return result;
}

#pragma mark - [日志] 未知输入法跳转特征采集

static void WVLog(NSString *line) {
    if (!gDebugLog) return;
    NSString *s = [NSString stringWithFormat:@"[%@] %@\n",
                   [NSDate date], line ?: @""];
    NSData *data = [s dataUsingEncoding:NSUTF8StringEncoding];
    NSFileManager *fm = [NSFileManager defaultManager];
    if (![fm fileExistsAtPath:kLogPath]) {
        [fm createFileAtPath:kLogPath contents:nil attributes:nil];
    }
    NSFileHandle *fh = [NSFileHandle fileHandleForWritingAtPath:kLogPath];
    if (!fh) return;
    @try {
        [fh seekToEndOfFile];
        [fh writeData:data];
    } @catch (NSException *e) {
    } @finally {
        [fh closeFile];
    }
}

// 把这次转场里 SpringBoard 收到的全部 activation setting 打出来
static NSString *WVDumpSettings(SBActivationSettings *s) {
    NSMutableString *m = [NSMutableString string];
    for (NSUInteger i = 0; i < 64; i++) {
        id v = nil;
        @try { v = [s objectForActivationSetting:i]; } @catch (NSException *e) { v = nil; }
        if (!v) continue;
        NSString *d = [v isKindOfClass:NSURL.class] ? [(NSURL *)v absoluteString] : [v description];
        if (d.length > 160) d = [d substringToIndex:160];
        [m appendFormat:@" s%lu=%@(%@)", (unsigned long)i, NSStringFromClass([v class]), d];
    }
    for (NSUInteger i = 0; i < 64; i++) {
        BOOL f = NO;
        @try { f = [s flagForActivationSetting:i]; } @catch (NSException *e) { f = NO; }
        if (f) [m appendFormat:@" f%lu=1", (unsigned long)i];
    }
    return m;
}

// 扫描全部 activation setting，找 scheme 匹配的 NSURL（用于 key 未知的输入法）
static NSURL *WVFindURLWithScheme(SBActivationSettings *s, NSString *scheme) {
    if (!scheme.length) return nil;
    for (NSUInteger i = 0; i < 64; i++) {
        id v = nil;
        @try { v = [s objectForActivationSetting:i]; } @catch (NSException *e) { v = nil; }
        if (![v isKindOfClass:NSURL.class]) continue;
        if ([[(NSURL *)v scheme] isEqualToString:scheme]) return (NSURL *)v;
    }
    return nil;
}

// 这次转场里有没有「自定义 scheme 的 URL」（说明是某个 App 的深链跳转）
static NSString *WVFirstDeepLink(SBActivationSettings *s) {
    for (NSUInteger i = 0; i < 64; i++) {
        id v = nil;
        @try { v = [s objectForActivationSetting:i]; } @catch (NSException *e) { v = nil; }
        if (![v isKindOfClass:NSURL.class]) continue;
        NSString *sch = [(NSURL *)v scheme];
        if (!sch.length) continue;
        if ([sch isEqualToString:@"http"] || [sch isEqualToString:@"https"] ||
            [sch isEqualToString:@"file"] || [sch isEqualToString:@"data"]) continue;
        return [(NSURL *)v absoluteString];
    }
    return nil;
}

#pragma mark - 偏好读取（对应 0x4908）

static void reloadSettings(void) {
    if (!gPrefs) {
        gPrefs = [[NSUserDefaults alloc] initWithSuiteName:kPrefDomain];
    }
    NSDictionary *d = [gPrefs dictionaryRepresentation];

    // key 不存在 → 默认 YES（与原实现一致）
    void (^loadBool)(NSString *, BOOL *) = ^(NSString *key, BOOL *out) {
        id v = d[key];
        *out = v ? [v boolValue] : YES;
    };
    loadBool(@"IsEnabled",       &gEnabled);
    loadBool(@"IsWeTypeEnabled", &gWeType);
    loadBool(@"IsBaiduEnabled",  &gBaidu);
    loadBool(@"IsSogouEnabled",  &gSogou);
    loadBool(@"IsXunFeiEnabled", &gXunFei);
    loadBool(@"IsDoubaoEnabled", &gDoubao);
    loadBool(@"IsTypelessEnabled", &gTypeless);
    loadBool(@"IsGenericEnabled", &gGeneric);
    // 日志默认关
    id lg = d[@"IsDebugLogEnabled"];
    gDebugLog = lg ? [lg boolValue] : NO;

    // 毫秒 → 秒；缺省 300ms
    id iv = d[@"AnimationInterval"], dv = d[@"AnimationDelay"];
    gInterval = iv ? [iv doubleValue] / 1000.0 : 0.3;
    gDelay    = dv ? [dv doubleValue] / 1000.0 : 0.3;

    [gKBHostCache removeAllObjects];
}

static void reloadSettingsNotification(CFNotificationCenterRef center, void *observer,
                                       CFStringRef name, const void *object,
                                       CFDictionaryRef userInfo) {
    reloadSettings();
}

#pragma mark - Hooks

%hook SBWorkspaceTransitionContext

- (BOOL)animationDisabled {
    BOOL orig = %orig;
    if (!gEnabled) return orig;

    id prev = [[self previousEntities] anyObject];
    id ent  = [[self entities] anyObject];
    Class sceneEntity = objc_getClass("SBApplicationSceneEntity");
    if (![prev isKindOfClass:sceneEntity]) return orig;
    if (![ent  isKindOfClass:sceneEntity]) return orig;

    NSString *prevBundle = [[prev application] bundleIdentifier];
    NSString *curBundle  = [[ent  application] bundleIdentifier];
    SBActivationSettings *settings = [ent activationSettings];

    BOOL host   = (gGeneric || gDebugLog) ? WVAppHostsKeyboardExtension(curBundle) : NO;
    BOOL flag42 = [settings flagForActivationSetting:42];
    NSString *deep = WVFirstDeepLink(settings);

    // ---- 1) 精确规则（原版行为）----
    for (size_t i = 0; i < kIMERuleCount; i++) {
        const IMERule *r = &kIMERules[i];
        if (!*RuleFlag(i)) continue;
        if (![curBundle isEqualToString:r->bundleID]) continue;

        id v = nil;
        NSURL *u = nil;
        if (r->matchMode == 1) {
            v = [settings objectForActivationSetting:r->settingKey];
            if (![v isKindOfClass:NSString.class]) continue;
            if (![v isEqualToString:r->contains]) continue;
        } else {
            if (r->matchMode == 2) {
                u = WVFindURLWithScheme(settings, r->scheme);
            } else {
                v = [settings objectForActivationSetting:r->settingKey];
                u = [v isKindOfClass:NSURL.class] ? (NSURL *)v : nil;
            }
            if (!u) continue;
            if (r->scheme && ![[u scheme] isEqualToString:r->scheme]) continue;
            if (r->host   && ![[u host]   isEqualToString:r->host])   continue;
            if (r->contains && ![[u absoluteString] containsString:r->contains]) continue;
        }
        WVLog([NSString stringWithFormat:@"MATCH(cur=%@ prev=%@ rule=%lu)%@",
               curBundle, prevBundle, (unsigned long)i, WVDumpSettings(settings)]);
        gLastBundle = prevBundle;
        return YES;
    }

    // ---- 2) 通用规则：被拉起的 App 自带键盘扩展 = 输入法宿主 ----
    // 需要同时满足「有深链」或「系统已置 42 号 flag」，避免把普通启动也算进来。
    // 例外：如果这个输入法在精确规则表里、但用户把它关掉了，就不再走通用匹配。
    if (gGeneric && host && (deep || flag42) && !WVExactRuleDisabled(curBundle)) {
        WVLog([NSString stringWithFormat:@"GENERIC(cur=%@ prev=%@ deep=%@ f42=%d)%@",
               curBundle, prevBundle, deep, flag42, WVDumpSettings(settings)]);
        gLastBundle = prevBundle;
        return YES;
    }

    // ---- 3) 兜底：42 号 flag（回程方向），且上一个 App 是输入法宿主 ----
    if (flag42) {
        if (WVAppHostsKeyboardExtension(prevBundle)) {
            WVLog([NSString stringWithFormat:@"FLAG42(cur=%@ prev=%@)", curBundle, prevBundle]);
            return YES;
        }
        for (size_t i = 0; i < kIMERuleCount; i++) {
            if (*RuleFlag(i) && [prevBundle isEqualToString:kIMERules[i].bundleID]) return YES;
        }
    }

    // 排查用：只记录「被拉起的 App 是输入法宿主」的转场，不会把每次切 App 都写进去
    if (gDebugLog && host) {
        WVLog([NSString stringWithFormat:@"SKIP(cur=%@ prev=%@ deep=%@ f42=%d)%@",
               curBundle, prevBundle, deep, flag42, WVDumpSettings(settings)]);
    }
    return orig;
}

%end

%hook SBApplicationSceneView

- (void)layoutSubviews {
    %orig;
    if (!gEnabled) return;

    NSString *bid = [[self application] bundleIdentifier];
    if (!bid) return;

    if ([gLastBundle isEqualToString:bid]) {
        gLastBundle = nil;
        if (gSnapshot == nil) {
            // 第一次命中：抓当前窗口快照盖上去（视觉上「还停在原 App」）
            UIView *win = [self window];
            UIView *snap = [win snapshotViewAfterScreenUpdates:NO];
            gSnapshot = snap;
            [snap setFrame:[win bounds]];
            [snap setUserInteractionEnabled:NO];
            [win addSubview:snap];
            return;
        }
    } else if (gSnapshot == nil) {
        return;
    }

    // 第二次命中：把旧快照淡出并移除
    UIView *snap = gSnapshot;
    gSnapshot = nil;
    [UIView animateWithDuration:gInterval
                          delay:gDelay
                        options:0
                     animations:^{ [snap setAlpha:0]; }
                     completion:^(BOOL finished) { [snap removeFromSuperview]; }];
}

%end

// 原实现里这两个是「只调用 %orig」的恒等钩子（无行为改变）。
// 保留是为了与 1.2 行为完全一致；不想留可以直接删掉这 8 行。
%hook SBActivationSettings

- (void)setFlag:(BOOL)flag forActivationSetting:(NSUInteger)setting   { %orig; }
- (void)setObject:(id)obj forActivationSetting:(NSUInteger)setting    { %orig; }

%end

#pragma mark - 入口

%ctor {
    reloadSettings();
    CFNotificationCenterAddObserver(CFNotificationCenterGetDarwinNotifyCenter(),
                                    NULL,
                                    reloadSettingsNotification,
                                    CFSTR(kPrefNotify),
                                    NULL,
                                    CFNotificationSuspensionBehaviorDeliverImmediately);
}
