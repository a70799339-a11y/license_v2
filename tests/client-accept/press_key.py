"""按键工具：python press_key.py [VK码十进制，默认13=回车] —— 给原生 confirm 对话框用（默认焦点=确定）"""
import ctypes
import sys
import time

user32 = ctypes.windll.user32
vk = int(sys.argv[1]) if len(sys.argv) > 1 else 13
time.sleep(0.1)
user32.keybd_event(vk, 0, 0, 0)
time.sleep(0.05)
user32.keybd_event(vk, 0, 0x0002, 0)
print("OK key", vk)
