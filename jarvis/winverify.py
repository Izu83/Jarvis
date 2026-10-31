"""Making sure it's you, on the PC: your Windows PIN, face or fingerprint (Windows Hello), or your Windows password.

With Face ID on, a permission allowed from the desktop app (desktop.py) is checked here instead of with the phone's
Face ID: Windows shows its own "Making sure it's you" box and tells Jarvis only whether it was you. The PIN or
password never passes through Jarvis. Without Windows Hello, Windows' own password box is used and checked against
your Windows account the same way the sign-in screen does.
"""
import asyncio
import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger("jarvis.winverify")


def verify(reason: str) -> bool:
    """True only if Windows confirmed it's you. Cancelled or wrong = False."""
    try:
        return _hello(reason)
    except _NoHello:
        log.info("Windows Hello isn't set up; asking for the Windows password instead.")
    except Exception:
        log.exception("Windows Hello failed; asking for the Windows password instead.")
    try:
        return _password(reason)
    except Exception:
        log.exception("Couldn't ask for the Windows password.")
        return False


class _NoHello(Exception):
    pass


def _hello(reason: str) -> bool:
    from winrt.windows.security.credentials.ui import (UserConsentVerificationResult, UserConsentVerifier,
                                                       UserConsentVerifierAvailability)

    async def run():
        if await UserConsentVerifier.check_availability_async() != UserConsentVerifierAvailability.AVAILABLE:
            raise _NoHello()
        return await UserConsentVerifier.request_verification_async(reason)

    result = asyncio.run(run())
    log.info("Windows Hello: %s", result)
    return result == UserConsentVerificationResult.VERIFIED


class _CREDUI_INFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hwndParent", wintypes.HWND), ("pszMessageText", wintypes.LPCWSTR),
                ("pszCaptionText", wintypes.LPCWSTR), ("hbmBanner", wintypes.HBITMAP)]


def _password(reason: str) -> bool:
    """Windows' own credential box with you already picked; the password is checked by Windows (LogonUser)."""
    credui, advapi, kernel = ctypes.windll.credui, ctypes.windll.advapi32, ctypes.windll.kernel32
    info = _CREDUI_INFO(ctypes.sizeof(_CREDUI_INFO), None, reason, "Jarvis: making sure it's you", None)
    package, out_buf, out_size, save = wintypes.ULONG(0), ctypes.c_void_p(), wintypes.ULONG(0), wintypes.BOOL(False)
    CREDUIWIN_ENUMERATE_CURRENT_USER = 0x200
    err = credui.CredUIPromptForWindowsCredentialsW(ctypes.byref(info), 0, ctypes.byref(package), None, 0,
                                                    ctypes.byref(out_buf), ctypes.byref(out_size), ctypes.byref(save),
                                                    CREDUIWIN_ENUMERATE_CURRENT_USER)
    if err:   # 1223 = cancelled
        return False
    user, domain, pwd = (ctypes.create_unicode_buffer(256) for _ in range(3))
    n_user, n_domain, n_pwd = wintypes.DWORD(256), wintypes.DWORD(256), wintypes.DWORD(256)
    try:
        if not credui.CredUnPackAuthenticationBufferW(0, out_buf, out_size, user, ctypes.byref(n_user),
                                                      domain, ctypes.byref(n_domain), pwd, ctypes.byref(n_pwd)):
            return False
        name, dom = user.value, domain.value
        if "\\" in name:
            dom, name = name.split("\\", 1)
        token = wintypes.HANDLE()
        ok = advapi.LogonUserW(name, dom or ".", pwd.value, 2, 0, ctypes.byref(token))   # interactive, default
        if ok:
            kernel.CloseHandle(token)
        log.info("Windows password: %s", "right" if ok else "wrong")
        return bool(ok)
    finally:
        ctypes.memset(pwd, 0, ctypes.sizeof(pwd))   # don't leave it lying around in memory
        ctypes.windll.ole32.CoTaskMemFree(out_buf)
