"""验收截屏小工具：python shot.py <输出路径> [窗口标题关键字]"""
import sys
import ctypes
import ctypes.wintypes

from PIL import ImageGrab


def bring_to_front(title_kw: str) -> None:
    user32 = ctypes.windll.user32
    found = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if title_kw in buf.value:
                found.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    if found:
        hwnd = found[0]
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)


def main() -> None:
    out = sys.argv[1]
    if len(sys.argv) > 2:
        bring_to_front(sys.argv[2])
    img = ImageGrab.grab()
    img.save(out)
    print("SAVED", out, img.size)


if __name__ == "__main__":
    main()
