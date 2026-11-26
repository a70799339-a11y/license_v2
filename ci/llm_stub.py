#!/usr/bin/env python3
"""CI 桩模型（Ollama 兼容最小面）：让 T18「经网关调用」在 CI 里走真实 HTTP 回路。

只监听 127.0.0.1；实现 /api/tags、/api/chat、/api/generate 与 OpenAI 兼容
/v1/chat/completions。固定文案响应，stdlib only，无外部依赖、无任何密钥。
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = ("[ci-stub] 本地风险最高的是测试门店：心跳停摆且近期发生设备指纹变更，"
         "建议安排现场核查并重新采集指纹。")


def grounded_reply(prompt: str) -> str:
    """数据接地应答：prompt 里的【单店档案】是唯一事实源，照抄该行（模拟模型受约束转抄）。
    只认行首锚定的档案行——指令行里也提到该标记，抄错行会把指令当回答（T7 CI 教训）。
    单店问题禁止让固定文案混进回答——无档案行才回固定文案。"""
    if "【单店档案】" in (prompt or ""):
        for line in prompt.splitlines():
            if line.strip().startswith("【单店档案】"):
                return line.strip() + "（ci-stub 数据接地转抄）"
    return REPLY


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.split("?")[0].rstrip("/").endswith("/api/tags"):
            self._json({"models": [{"name": "ci-stub"}]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b"{}"
        try:
            req = json.loads(body or b"{}")
        except Exception:
            req = {}
        path = self.path.split("?")[0]
        msgs = req.get("messages") or []
        content = ""
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "user":
                content = m.get("content") or ""
        content = content or req.get("prompt") or ""
        reply = grounded_reply(content)
        if path.endswith("/api/chat") or path.endswith("/api/generate"):
            self._json({
                "model": req.get("model") or "ci-stub",
                "message": {"role": "assistant", "content": reply},
                "response": reply,
                "done": True,
                "prompt_eval_count": 8,
                "eval_count": 16,
            })
        elif path.endswith("/v1/chat/completions"):
            self._json({
                "id": "ci-stub-1",
                "object": "chat.completion",
                "model": req.get("model") or "ci-stub",
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": reply},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 16, "total_tokens": 24},
            })
        else:
            self._json({"error": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11435)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[llm_stub] listening on {args.host}:{args.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
