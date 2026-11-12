"""模拟鼠标点击屏幕坐标：python click.py X Y"""
import ctypes
import sys
import time

x, y = int(sys.argv[1]), int(sys.argv[2])
user32 = ctypes.windll.user32
user32.SetCursorPos(x, y)
time.sleep(0.15)
user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
time.sleep(0.06)
user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
print(f"CLICKED {x},{y}")
