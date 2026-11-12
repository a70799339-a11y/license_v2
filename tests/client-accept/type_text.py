"""剪贴板粘贴输入：python type_text.py "文本" [x y]（可选先点击输入框）"""
import ctypes
import sys
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

text = sys.argv[1]
if len(sys.argv) >= 4:
    x, y = int(sys.argv[2]), int(sys.argv[3])
    user32.SetCursorPos(x, y)
    time.sleep(0.12)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.3)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
buf = ctypes.create_unicode_buffer(text)

if not user32.OpenClipboard(None):
    print("ERR open clipboard")
    sys.exit(1)
user32.EmptyClipboard()
h = kernel32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(buf))
p = kernel32.GlobalLock(h)
ctypes.memmove(p, buf, ctypes.sizeof(buf))
kernel32.GlobalUnlock(h)
user32.SetClipboardData(CF_UNICODETEXT, h)
user32.CloseClipboard()

# Ctrl+V
user32.keybd_event(0x11, 0, 0, 0)
user32.keybd_event(0x56, 0, 0, 0)
user32.keybd_event(0x56, 0, 0x0002, 0)
user32.keybd_event(0x11, 0, 0x0002, 0)
print("TYPED", repr(text))
