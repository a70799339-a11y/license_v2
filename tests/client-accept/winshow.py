"""按句柄诊断/唤起窗口"""
import ctypes
import sys
import time

import ctypes.wintypes as wt

user32 = ctypes.windll.user32

hwnd = int(sys.argv[1])
print("IsWindow:", bool(user32.IsWindow(hwnd)))
print("IsWindowVisible:", bool(user32.IsWindowVisible(hwnd)))
print("IsIconic:", bool(user32.IsIconic(hwnd)))

n = user32.GetWindowTextLengthW(hwnd)
buf = ctypes.create_unicode_buffer(n + 1)
user32.GetWindowTextW(hwnd, buf, n + 1)
print("title:", repr(buf.value))

r = wt.RECT()
user32.GetWindowRect(hwnd, ctypes.byref(r))
print(f"rect: ({r.left},{r.top})-({r.right},{r.bottom})")

# 强制显示 + 前台 + 移到屏幕可见区
user32.ShowWindow(hwnd, 9)  # SW_RESTORE
user32.SetWindowPos(hwnd, -1, 100, 50, 1280, 800, 0x0040)  # HWND_TOPMOST-ish flags: SWP_SHOWWINDOW
user32.SetForegroundWindow(hwnd)
time.sleep(1.2)
user32.GetWindowRect(hwnd, ctypes.byref(r))
print(f"after: ({r.left},{r.top})-({r.right},{r.bottom})")
print("IsWindowVisible now:", bool(user32.IsWindowVisible(hwnd)))
