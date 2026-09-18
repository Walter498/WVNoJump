// WVNoJumpRootListController.m
// 设置界面：只显示设备上「真的装了」的输入法开关（思路同原版 WTVRBGPrefs）

#import <Preferences/PSListController.h>
#import <Preferences/PSSpecifier.h>
#import <Preferences/PSTableCell.h>
#import <UIKit/UIKit.h>
#import <Foundation/Foundation.h>

#define kPrefDomain @"com.walter.voicenojump"

// 私有：查询 App 是否安装
@interface LSApplicationProxy : NSObject
+ (instancetype)applicationProxyForIdentifier:(NSString *)identifier;
- (BOOL)isInstalled;
@end

@interface WVNoJumpRootListController : PSListController
@end

@implementation WVNoJumpRootListController

- (NSArray *)specifiers {
    if (!_specifiers) {
        NSMutableArray *list = [[self loadSpecifiersFromPlistName:@"Root" target:self] mutableCopy];

        // (bundleID, 对应的 specifier 的 key) —— 没装这个输入法就把它的分组和开关一起去掉
        NSArray *pairs = @[
            @[@"com.tencent.wetype",          @"IsWeTypeEnabled"],
            @[@"com.baidu.inputMethod",       @"IsBaiduEnabled"],
            @[@"com.sogou.sogouinput",        @"IsSogouEnabled"],
            @[@"com.iflytek.inputime",        @"IsXunFeiEnabled"],
            @[@"com.bytedance.ios.doubaoime", @"IsDoubaoEnabled"],
            @[@"com.typeless.mobile",         @"IsTypelessEnabled"],
        ];

        NSMutableArray *toRemove = [NSMutableArray array];
        for (NSArray *pair in pairs) {
            LSApplicationProxy *proxy = [LSApplicationProxy applicationProxyForIdentifier:pair[0]];
            if (proxy && [proxy isInstalled]) continue;
            for (PSSpecifier *s in [list copy]) {
                NSString *key = [s.properties objectForKey:@"key"];
                NSString *bundle = [s.properties objectForKey:@"bundleID"];
                if ((key && [key isEqualToString:pair[1]]) ||
                    (bundle && [bundle isEqualToString:pair[0]])) {
                    [toRemove addObject:s];
                }
            }
        }
        [list removeObjectsInArray:toRemove];

        // 一个输入法都没装 → 显示空提示
        if (![list count]) {
            list = [[self loadSpecifiersFromPlistName:@"Empty" target:self] mutableCopy];
        }
        _specifiers = list;
    }
    return _specifiers;
}

- (void)viewDidLoad {
    [super viewDidLoad];
    self.navigationItem.rightBarButtonItem = [[UIBarButtonItem alloc]
        initWithBarButtonSystemItem:UIBarButtonSystemItemDone
                             target:self
                             action:@selector(dismiss)];
}

- (void)dismiss {
    [self.navigationController dismissViewControllerAnimated:YES completion:nil];
}

// 「关于」按钮
- (void)about {
    [[UIApplication sharedApplication]
        openURL:[NSURL URLWithString:@"https://github.com/Walter498"]
        options:@{}
        completionHandler:nil];
}

@end
