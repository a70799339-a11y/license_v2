"""企微智能机器人 —— WebSocket 长连接适配器（官方 aibot-python-sdk）

职责：把企微消息接入已有指令管道 process_command，回复走长连接。
SDK 内置：认证 / 心跳 30s / 指数退避断线重连 / 消息分发。
配置读取优先级（密钥不入库、不进代码）：
  1. 环境变量 WECHAT_BOT_ID / WECHAT_BOT_SECRET
  2. 服务端本地配置文件 backend/.env（gitignore，手工维护）
  3. settings 兼容旧方式
"""
import asyncio
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from .database import SessionLocal
from .wecom import ACK_TEXT, get_setting, process_command

_STATUS = {
    "running": False,
    "authenticated": False,
    "last_error": "",
    "started_at": "",
    "msg_count": 0,
}
_thread: threading.Thread | None = None
_stop_event = threading.Event()


def ws_status() -> dict:
    return dict(_STATUS)


def _load_env_file() -> dict:
    """解析本地配置文件（KEY=VALUE，# 注释）：优先 .env，其次 wecom-bot.conf"""
    vals = {}
    base = Path(__file__).resolve().parent.parent
    for name in (".env", "wecom-bot.conf"):
        path = base / name
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except OSError:
            continue
    return vals


def _bot_config(db) -> tuple:
    """(bot_id, secret, enabled) —— 优先级：环境变量 > .env 文件 > settings"""
    f = _load_env_file()
    bot_id = os.environ.get("WECHAT_BOT_ID") or f.get("WECHAT_BOT_ID") or get_setting(db, "wecom_bot_id")
    secret = os.environ.get("WECHAT_BOT_SECRET") or f.get("WECHAT_BOT_SECRET") or get_setting(db, "wecom_bot_secret")
    enabled = (os.environ.get("WECHAT_BOT_ENABLED") or f.get("WECHAT_BOT_ENABLED") or "on") != "off"
    return bot_id, secret, enabled


def _strip_mention(text: str) -> str:
    return re.sub(r"^@[\w\u4e00-\u9fa5·-]+\s*", "", text or "").strip()


def _run(bot_id: str, secret: str) -> None:
    """在独立线程的事件循环里跑长连接"""
    try:
        from aibot import WSClient, WSClientOptions, generate_req_id
    except ImportError:
        _STATUS["last_error"] = "缺少依赖 wecom-aibot-python-sdk（pip install wecom-aibot-python-sdk）"
        _STATUS["running"] = False
        return

    async def main():
        client = WSClient(
            WSClientOptions(
                bot_id=bot_id,
                secret=secret,
                max_reconnect_attempts=-1,  # 无限重连（服务常驻）
            )
        )

        @client.on("authenticated")
        def on_auth():
            _STATUS["authenticated"] = True
            _STATUS["last_error"] = ""
            _STATUS["started_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        @client.on("disconnected")
        def on_disc(reason=""):
            _STATUS["authenticated"] = False
            _STATUS["last_error"] = f"连接断开：{reason}"

        @client.on("error")
        def on_err(error=None):
            _STATUS["last_error"] = str(error)[:120]

        @client.on("event.enter_chat")
        async def on_enter(frame):
            try:
                await client.reply_welcome(
                    frame,
                    {
                        "msgtype": "text",
                        "text": {"content": "您好！我是 License 授权管理助手。\n回复『帮助』查看可用指令（状态/告警/续期）。"},
                    },
                )
            except Exception:
                pass

        @client.on("message.text")
        async def on_text(frame):
            _STATUS["msg_count"] += 1
            try:
                body = frame.get("body", {}) or {}
                text = _strip_mention((body.get("text") or {}).get("content", ""))
                userid = ((body.get("from") or {}).get("userid")) or "unknown"
                print(f"[wecom] 收到消息 #{_STATUS['msg_count']} from={userid} text={text[:30]!r}", flush=True)
                if not text:
                    return
                sid = generate_req_id("stream")
                loop = asyncio.get_running_loop()
                ack_fut = None

                def on_slow():
                    """慢路径（AI 检索/生成）入口：秒发『检索中』提示（同一气泡流式）"""
                    nonlocal ack_fut
                    if ack_fut is None:
                        ack_fut = asyncio.run_coroutine_threadsafe(
                            client.reply_stream(frame, sid, f"{ACK_TEXT}\n", False), loop
                        )

                db = SessionLocal()
                try:
                    reply = await asyncio.to_thread(process_command, db, text, userid, on_slow)
                finally:
                    db.close()
                if ack_fut is not None:
                    try:
                        await asyncio.wrap_future(ack_fut)  # 等提示先落地，保证时序
                    except Exception:
                        pass
                    await client.reply_stream(frame, sid, reply, True)  # 续写同一气泡
                else:
                    await client.reply_stream(frame, sid, reply, True)
            except Exception as e:  # 单条消息失败不影响连接，但必须回话（杜绝静默）
                err = str(e)[:100]
                _STATUS["last_error"] = f"消息处理失败：{err}"
                print(f"[wecom] 消息处理失败：{err}", flush=True)
                try:
                    await client.reply_stream(
                        frame, generate_req_id("stream"),
                        f"⚠️ 处理出错（{err}），请稍后重试或回复『帮助』。", True,
                    )
                except Exception:
                    pass

        await client.connect()
        await asyncio.Event().wait()  # 常驻（SDK 内部负责心跳与重连）

    asyncio.run(main())


def start_ws(force: bool = False) -> dict:
    """启动长连接（幂等：已在跑则跳过；force=重启）"""
    global _thread
    db = SessionLocal()
    try:
        bot_id, secret, enabled = _bot_config(db)
    finally:
        db.close()
    if not enabled or not bot_id or not secret:
        _STATUS["running"] = False
        _STATUS["last_error"] = "未启用或缺少 BotID/Secret"
        return ws_status()
    if _thread and _thread.is_alive() and not force:
        return ws_status()
    if _thread and _thread.is_alive():
        _stop_event.set()
        _thread.join(timeout=3)
    _stop_event.clear()
    _STATUS["running"] = True
    _STATUS["last_error"] = ""
    _thread = threading.Thread(target=_run, args=(bot_id, secret), daemon=True, name="wecom-ws")
    _thread.start()
    return ws_status()


def stop_ws() -> dict:
    global _thread
    _stop_event.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=3)
    _thread = None
    _STATUS["running"] = False
    _STATUS["authenticated"] = False
    return ws_status()
