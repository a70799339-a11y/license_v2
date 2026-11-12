"""安全点击：先确保目标窗口在前台（校验 GetForegroundWindow），再点击逻辑坐标。
用法: python safe_click.py <hwnd> <logical_x> <logical_y>"""
import ctypes
import sys
import time

user32 = ctypes.windll.user32

hwnd = int(sys.argv[1])
x, y = int(sys.argv[2]), int(sys.argv[3])

# 恢复并置前（ALT 技巧解锁 SetForegroundWindow 限制）
user32.ShowWindow(hwnd, 9)
time.sleep(0.3)
user32.keybd_event(0x12, 0, 0, 0)  # ALT down
user32.keybd_event(0x12, 0, 0x0002, 0)  # ALT up
user32.SetForegroundWindow(hwnd)
time.sleep(0.5)
if user32.GetForegroundWindow() != hwnd:
    # 二次尝试：AttachThreadInput 附加到前台线程
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    my_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(my_tid, fg_tid, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(my_tid, fg_tid, False)
    time.sleep(0.5)

fg = user32.GetForegroundWindow()
if fg != hwnd:
    print(f"ABORT: foreground={fg} != target={hwnd}，窗口未获得焦点，不点击")
    sys.exit(2)

user32.SetCursorPos(x, y)
time.sleep(0.15)
user32.mouse_event(0x0002, 0, 0, 0, 0)
time.sleep(0.06)
user32.mouse_event(0x0004, 0, 0, 0, 0)
print(f"OK clicked {x},{y} on hwnd={hwnd}")
