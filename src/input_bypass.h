#ifndef SMITHY_SKIP_INPUT_BYPASS_H
#define SMITHY_SKIP_INPUT_BYPASS_H

#include <xinput.h>

typedef DWORD (WINAPI *ControllerStateFn)(DWORD, XINPUT_STATE *);

static PVOID volatile controller_get_state;
static volatile LONG64 controller_retry_after[XUSER_MAX_COUNT];
#define CONTROLLER_RETRY_MS 1000u

#ifdef SMITHY_SKIP_TEST
static ULONGLONG controller_test_time;

__declspec(dllexport) void SmithySkip_TestControllerSetup(ControllerStateFn get_state) {
    controller_test_time = 0;
    for (DWORD slot = 0; slot < XUSER_MAX_COUNT; ++slot) {
        InterlockedExchange64(&controller_retry_after[slot], 0);
    }
    InterlockedExchangePointer(&controller_get_state, (PVOID)get_state);
}

__declspec(dllexport) void SmithySkip_TestControllerSetTime(ULONGLONG now_ms) {
    controller_test_time = now_ms;
}
#else
/* Called by the runtime worker outside DllMain. Keep the resolved module
 * loaded for the lifetime of the pinned plugin. */
static void initialize_controller_input(void) {
    static const wchar_t *const libraries[] = { L"xinput1_4.dll", L"xinput9_1_0.dll" };
    for (size_t i = 0; i < sizeof(libraries) / sizeof(libraries[0]); ++i) {
        HMODULE module = LoadLibraryExW(libraries[i], NULL, LOAD_LIBRARY_SEARCH_SYSTEM32);
        if (!module) continue;
        FARPROC get_state = GetProcAddress(module, "XInputGetState");
        if (get_state) {
            InterlockedExchangePointer(&controller_get_state, (PVOID)get_state);
            return;
        }
        FreeLibrary(module);
    }
}
#endif

static int bypass_requested(void) {
#ifndef SMITHY_SKIP_TEST
    if ((GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0) return 1;
#endif
    ControllerStateFn get_state = (ControllerStateFn)InterlockedCompareExchangePointer(
        &controller_get_state, NULL, NULL);
    if (!get_state) return 0;

#ifdef SMITHY_SKIP_TEST
    ULONGLONG now = controller_test_time;
#else
    ULONGLONG now = GetTickCount64();
#endif
    for (DWORD slot = 0; slot < XUSER_MAX_COUNT; ++slot) {
        ULONGLONG retry_after = (ULONGLONG)InterlockedCompareExchange64(&controller_retry_after[slot], 0, 0);
        if (now < retry_after) continue;

        XINPUT_STATE state;
        if (get_state(slot, &state) != ERROR_SUCCESS) {
            /* Avoid repeatedly polling empty slots on the subtitle update path. */
            InterlockedExchange64(&controller_retry_after[slot], (LONG64)(now + CONTROLLER_RETRY_MS));
        } else if (state.Gamepad.wButtons & XINPUT_GAMEPAD_RIGHT_SHOULDER) {
            return 1;
        }
    }
    return 0;
}

#endif
