// Secrets enter and leave only through private pipes, never argv or logs.
#import <Foundation/Foundation.h>
#import <Security/Security.h>
#import <LocalAuthentication/LocalAuthentication.h>

static void reply(NSDictionary *value) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
    if (data) [[NSFileHandle fileHandleWithStandardOutput] writeData:data];
}
int main(void) { @autoreleasepool {
    NSData *input = [[NSFileHandle fileHandleWithStandardInput] readDataOfLength:65537];
    id request = input.length <= 65536 ? [NSJSONSerialization JSONObjectWithData:input options:0 error:nil] : nil;
    if (![request isKindOfClass:[NSDictionary class]]) { reply(@{@"ok":@NO,@"error":@"invalid_request"}); return 0; }
    NSString *op=request[@"operation"], *profile=request[@"profile"], *reference=request[@"reference"];
    BOOL valid = [op isKindOfClass:[NSString class]] && [@[@"create",@"read",@"delete"] containsObject:op] &&
        [profile isKindOfClass:[NSString class]] && [reference isKindOfClass:[NSString class]] &&
        [[NSUUID alloc] initWithUUIDString:profile] && [[NSUUID alloc] initWithUUIDString:reference];
    NSSet *keys = [NSSet setWithArray:[request allKeys]];
    NSSet *expected = [NSSet setWithArray:[op isEqual:@"create"] ? @[@"operation",@"profile",@"reference",@"value"] : @[@"operation",@"profile",@"reference"]];
    if (!valid || ![keys isEqual:expected]) { reply(@{@"ok":@NO,@"error":@"invalid_request"}); return 0; }
    LAContext *context = [[LAContext alloc] init]; context.interactionNotAllowed=YES;
    NSMutableDictionary *query = [@{
        (__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,
        (__bridge id)kSecAttrService:[@"ai.swarmtracker.verity.credentials." stringByAppendingString:profile.lowercaseString],
        (__bridge id)kSecAttrAccount:reference.lowercaseString,
        (__bridge id)kSecUseDataProtectionKeychain:@YES,
        (__bridge id)kSecAttrSynchronizable:@NO,
        (__bridge id)kSecUseAuthenticationContext:context
    } mutableCopy];
    OSStatus status;
    if ([op isEqual:@"create"]) {
        id value=request[@"value"];
        if (![value isKindOfClass:[NSString class]]) { reply(@{@"ok":@NO,@"error":@"invalid_request"}); return 0; }
        NSData *data=[value dataUsingEncoding:NSUTF8StringEncoding];
        if (data.length==0 || data.length>32768) { reply(@{@"ok":@NO,@"error":@"invalid_request"}); return 0; }
        query[(__bridge id)kSecValueData]=data;
        query[(__bridge id)kSecAttrAccessible]=(__bridge id)kSecAttrAccessibleWhenUnlockedThisDeviceOnly;
        status=SecItemAdd((__bridge CFDictionaryRef)query,NULL);
    } else if ([op isEqual:@"read"]) {
        query[(__bridge id)kSecReturnData]=@YES;
        query[(__bridge id)kSecMatchLimit]=(__bridge id)kSecMatchLimitOne;
        CFTypeRef result=NULL;
        status=SecItemCopyMatching((__bridge CFDictionaryRef)query,&result);
        id data=CFBridgingRelease(result);
        if (status==errSecSuccess) {
            NSString *value=[data isKindOfClass:[NSData class]] && [data length]<=32768 ? [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] : nil;
            reply(value ? @{@"ok":@YES,@"value":value} : @{@"ok":@NO,@"error":@"invalid_stored_value"}); return 0;
        }
    } else { status=SecItemDelete((__bridge CFDictionaryRef)query); }
    if (status==errSecSuccess) { reply(@{@"ok":@YES}); return 0; }
    NSString *error=@"keychain_unavailable";
    if (status==errSecItemNotFound) error=@"not_found";
    else if (status==errSecDuplicateItem) error=@"already_exists";
    else if (status==errSecInteractionNotAllowed || status==errSecAuthFailed || status==errSecUserCanceled) error=@"access_unavailable";
    reply(@{@"ok":@NO,@"error":error,@"status":@(status)});
    return 0;
} }
