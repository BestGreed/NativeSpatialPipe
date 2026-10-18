#ifndef FSA_LANGUAGE_H
#define FSA_LANGUAGE_H
#include <windows.h>
#include <wchar.h>

typedef enum { FSA_EN, FSA_ZH } FSA_Language;
typedef enum {
    TXT_STEREO, TXT_51, TXT_71, TXT_ATMOS, TXT_DTS, TXT_AUTO, TXT_ADVANCED,
    TXT_EXIT, TXT_CAPTURE, TXT_EDIT, TXT_RELOAD, TXT_LOG, TXT_STARTUP,
    TXT_READING, TXT_CAPTURED, TXT_INIT_FAILED, TXT_COM_FAILED, TXT_UNAVAILABLE,
    TXT_READY, TXT_SWITCHING, TXT_VERIFIED, TXT_SWITCH_FAILED, TXT_UNKNOWN,
    TXT_MANUAL, TXT_STARTUP_FAILED, TXT_NO_SPATIAL, TXT_CAPTURE_OK, TXT_CAPTURE_FAILED,
    TXT_NSP, TXT_NSP_STOP, TXT_NSP_EMERGENCY,
    TXT_COUNT
} FSA_Text;

/* Select once before starting the worker. All Chinese UI sublanguages qualify;
   keyboard layout, regional formats and the system ANSI code page do not. */
static inline FSA_Language fsa_select_language(const wchar_t *setting, LANGID ui) {
    if(setting && !_wcsicmp(setting,L"en")) return FSA_EN;
    if(setting && !_wcsicmp(setting,L"zh")) return FSA_ZH;
    return PRIMARYLANGID(ui)==LANG_CHINESE?FSA_ZH:FSA_EN;
}
static inline const wchar_t *fsa_text(FSA_Language language,FSA_Text id) {
    static const wchar_t *const strings[TXT_COUNT][2]={
        {L"Stereo",L"Stereo"}, {L"5.1 Surround",L"5.1 Surround"},
        {L"7.1 Surround",L"7.1 Surround"}, {L"Dolby Atmos",L"Dolby Atmos"}, {L"DTS:X",L"DTS:X"},
        {L"Auto switch",L"自动切换"}, {L"Advanced",L"高级"}, {L"Exit",L"退出"},
        {L"Capture intent",L"意图捕获"}, {L"Edit rules",L"编辑规则"},
        {L"Reload rules",L"重新加载规则"}, {L"View log",L"查看日志"},
        {L"Start with Windows",L"开机自启"},
        {L"Reading audio status",L"正在读取音频状态"},
        {L"Intent captured and saved",L"意图已捕获并保存"},
        {L"Profile initialization failed; see log",L"配置初始化失败；请查看日志"},
        {L"COM initialization failed",L"COM 初始化失败"},
        {L"Endpoint unavailable",L"音频设备不可用"}, {L"Ready",L"就绪"},
        {L"Switching...",L"正在切换…"}, {L"Verified",L"已验证"},
        {L"Switch failed; see log (30s backoff)",L"切换失败；请查看日志（30 秒后重试）"},
        {L"Custom / unknown",L"自定义 / 未知"}, {L"Manual lock",L"手动锁定"},
        {L"Unable to update startup settings.",L"无法更新开机自启设置。"},
        {L"The current output device reports no available home theater spatial audio mode (Dolby Atmos / DTS:X). This may depend on the device, connection or spatial audio components. Unavailable modes are disabled; supported standard modes remain available.",
         L"当前输出设备未报告可用的家庭影院空间音频模式（Dolby Atmos / DTS:X）。可能与设备、连接方式或空间音频组件有关。不可用的模式已置灰；可继续使用支持的标准模式。"},
        {L"The current Windows audio settings have been captured and saved to the corresponding mode profile.",L"已捕获当前系统音频设置，并更新对应模式的配置。"},
        {L"Capture failed: the current state is not a supported mode, or the profile could not be saved. See the log for details.",L"捕获失败：当前状态无法识别为受支持模式，或配置无法保存。请查看日志。"}
        ,{L"Sonic NSp",L"Sonic NSp"}
        ,{L"Stop NSp",L"\u505c\u6b62 NSp"}
        ,{L"Emergency stop NSp",L"\u6025\u505c NSp"}
    };
    if((unsigned)id>=TXT_COUNT) return L"";
    return strings[id][language==FSA_ZH?FSA_ZH:FSA_EN];
}
#endif
