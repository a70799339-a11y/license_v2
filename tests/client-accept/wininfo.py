"""诊断目标窗口几何与可见性，并尝试唤到前台"""
import ctypes
import sys

user32 = ctypes.windll.user32

title_kw = sys.argv[1] if len(sys.argv) > 1 else "酒店AI管理客户端"
found = []


def cb(hwnd, _):
    if user32.IsWindowVisible(hwnd):
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if title_kw in buf.value:
            found.append((hwnd, buf.value))
    return True


WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
user32.EnumWindows(WNDENUMPROC(cb), 0)

for hwnd, title in found:
    rect = ctypes.wintypes.RECT() if hasattr(ctypes, "wintypes") else None
    import ctypes.wintypes as wt

    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    placement_cmd = user32.IsIconic(hwnd)
    print(f"hwnd={hwnd} title={title!r}")
    print(f"  rect=({r.left},{r.top})-({r.right},{r.bottom}) iconic={placement_cmd}")
    # SW_RESTORE then foreground
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    import time

    time.sleep(1.0)
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    print(f"  after restore rect=({r.left},{r.top})-({r.right},{r.bottom})")
print("visible_count=", len(found))
