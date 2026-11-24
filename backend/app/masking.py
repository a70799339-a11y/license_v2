"""日志/审计脱敏 —— 统一在写入口对敏感字段掩码，数据库中不留明文

脱敏范围（正则识别）：
1. JWT / 令牌类（eyJ... 三段式）        → ＊＊＊TOKEN＊＊＊
2. 身份证号（15/18 位）                 → 前3位 + ＊ + 后2位
3. 手机号（1[3-9]xxxxxxxxx）            → 前3位 + ＊＊＊＊ + 后4位
4. 邮箱                                → 首字符 + ＊＊＊ + @域名
5. 银行卡号（13-19 位纯数字）           → ＊＊＊＊ + 后4位
6. 设备指纹 / 长十六进制串（≥32 位）    → 前6位 + … + 后4位
7. 业务编号（LIC-/HT-/GP-）             → 前缀 + ＊＊＊＊ + 后2位（保留可追溯性）

原则：审计要能追溯「谁对哪条业务做了什么」，所以操作人、业务动作、
酒店名保留；个人隐私（手机/身份证/邮箱/卡号）、凭证（令牌）、
设备指纹必须掩码。

掩码符用全角＊（U+FF0A）而非半角星号：推送内容走企微 markdown，
半角 **** 会被当强调符吞掉（HT-****26 渲染成 HT-26，2026-11-17 实锤），
全角＊在纯文本和 markdown 渲染里都原样可见。
"""
import re

_RE_TOKEN = re.compile(r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*")
_RE_ID18 = re.compile(r"(?<!\d)(\d{3})\d{12}(\d{2}[\dXx])(?!\d)")
_RE_ID15 = re.compile(r"(?<!\d)(\d{3})\d{9}(\d{3})(?!\d)")
_RE_PHONE = re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)")
_RE_EMAIL = re.compile(r"([\w.+-])[\w.+-]*@([\w.-]+\.[A-Za-z]{2,})")
_RE_BANK = re.compile(r"(?<!\d)(\d{4})\d{7,13}(\d{4})(?!\d)")
_RE_FINGERPRINT = re.compile(r"(?<![a-fA-F0-9])([a-fA-F0-9]{6})[a-fA-F0-9]{22,58}([a-fA-F0-9]{4})(?![a-fA-F0-9])")
_RE_WEBHOOK_KEY = re.compile(r"(key=)[A-Za-z0-9_-]{6,}")


def mask_text(text: str) -> str:
    """对自由文本做敏感信息掩码（幂等：已掩码内容不再二次变形）"""
    if not text:
        return text
    s = text
    s = _RE_TOKEN.sub("＊＊＊TOKEN＊＊＊", s)
    s = _RE_ID18.sub(lambda m: f"{m.group(1)}{'＊' * 11}{m.group(2)[-2:]}", s)
    s = _RE_ID15.sub(lambda m: f"{m.group(1)}{'＊' * 8}{m.group(2)[-2:]}", s)
    s = _RE_PHONE.sub(r"\1＊＊＊＊\3", s)
    s = _RE_EMAIL.sub(r"\1＊＊＊@\2", s)
    s = _RE_BANK.sub(r"\1＊＊＊＊＊＊＊＊＊＊＊＊\2", s)  # N11：raw 串 + 保留首 4 后 4（\2 否则变控制字符）
    s = _RE_FINGERPRINT.sub(r"\1…\2", s)
    s = _RE_WEBHOOK_KEY.sub(r"\1＊＊＊", s)

    def _biz(m: re.Match) -> str:
        prefix, body = m.group(1), m.group(2)
        tail = body[-2:] if len(body) >= 2 else body
        return f"{prefix}-＊＊＊＊{tail}"

    s = re.sub(r"(?<![A-Za-z0-9-])(LIC|HT|GP)-([A-Za-z0-9-]+)", _biz, s)
    return s
