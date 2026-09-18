include $(THEOS)/makefiles/common.mk

BUNDLE_NAME = WVNoJumpPrefs
WVNoJumpPrefs_FILES = WVNoJumpRootListController.m
WVNoJumpPrefs_INSTALL_PATH = /Library/PreferenceBundles
WVNoJumpPrefs_FRAMEWORKS = UIKit Foundation
WVNoJumpPrefs_PRIVATE_FRAMEWORKS = Preferences
WVNoJumpPrefs_CFLAGS = -fobjc-arc
WVNoJumpPrefs_LDFLAGS = -Wl,-undefined,dynamic_lookup

include $(THEOS_MAKE_PATH)/bundle.mk
