"""
本地大模型后端适配（Ollama / llama.cpp）—— 供 llm_audit.llm_call() 网关调用

部署形态（内网自持，无外网依赖、无 API Key 外泄）：
- Ollama：   POST {endpoint}/api/chat        （默认 http://localhost:11434）
- llama.cpp：POST {endpoint}/v1/chat/completions（默认 http://localhost:8080，OpenAI 兼容）

配置（settings 表，后台「安全设计」可改）：
- llm_provider：ollama / llama.cpp / 空（未接入）
- llm_endpoint：服务地址
- llm_model：    模型名（如 qwen2.5:7b / qwen2.5-7b-instruct）

统一出口：get_backend(db) -> fn(prompt) -> (output, model_name, tokens_in, tokens_out)
任何大模型调用必须经 llm_audit.llm_call() 过账（自动审计+脱敏）。
"""
import json
import urllib.request

from sqlalchemy.orm import Session

from .models import Setting


def _setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    return (row.value.strip() if row and row.value else default) or default


def llm_config(db: Session) -> dict:
    provider = _setting(db, "llm_provider", "")
    if provider == "ollama":
        endpoint = _setting(db, "llm_endpoint", "http://localhost:11434")
    elif provider == "llama.cpp":
        endpoint = _setting(db, "llm_endpoint", "http://localhost:8080")
    else:
        endpoint = _setting(db, "llm_endpoint", "")
    return {
        "provider": provider,
        "endpoint": endpoint.rstrip("/"),
        "model": _setting(db, "llm_model", ""),
    }


def _http_json(url: str, payload: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _chat_ollama(endpoint: str, model: str, prompt: str) -> tuple:
    r = _http_json(
        f"{endpoint}/api/chat",
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        },
        timeout=180,
    )
    output = ((r.get("message") or {}).get("content")) or ""
    tin = int(r.get("prompt_eval_count") or 0)
    tout = int(r.get("eval_count") or 0)
    return output, model, tin, tout


def _chat_llamacpp(endpoint: str, model: str, prompt: str) -> tuple:
    r = _http_json(
        f"{endpoint}/v1/chat/completions",
        {
            "model": model or "local",
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=180,
    )
    choices = r.get("choices") or []
    output = ((choices[0].get("message") or {}).get("content")) if choices else ""
    usage = r.get("usage") or {}
    tin = int(usage.get("prompt_tokens") or 0)
    tout = int(usage.get("completion_tokens") or 0)
    return output or "", model or "llama.cpp", tin, tout


def get_backend(db: Session):
    """返回 fn(prompt)->(output, model_name, tokens_in, tokens_out)；未配置返回 None"""
    cfg = llm_config(db)
    provider, endpoint, model = cfg["provider"], cfg["endpoint"], cfg["model"]
    if not provider or not endpoint:
        return None

    def backend(prompt: str) -> tuple:
        if provider == "ollama":
            return _chat_ollama(endpoint, model, prompt)
        if provider == "llama.cpp":
            return _chat_llamacpp(endpoint, model, prompt)
        raise ValueError(f"不支持的模型后端：{provider}")

    return backend


def ping(db: Session) -> dict:
    """连通性检查（列出可用模型/版本）"""
    cfg = llm_config(db)
    if not cfg["provider"]:
        return {"ok": False, "detail": "未接入（llm_provider 为空）"}
    try:
        if cfg["provider"] == "ollama":
            with urllib.request.urlopen(f"{cfg['endpoint']}/api/tags", timeout=6) as r:
                data = json.loads(r.read().decode("utf-8"))
            names = [m.get("name") for m in (data.get("models") or [])]
            return {"ok": True, "detail": f"Ollama 可用，已装模型 {len(names)} 个", "models": names[:10]}
        if cfg["provider"] == "llama.cpp":
            with urllib.request.urlopen(f"{cfg['endpoint']}/health", timeout=6) as r:
                return {"ok": r.status == 200, "detail": f"llama.cpp 健康检查 HTTP {r.status}"}
        return {"ok": False, "detail": f"未知后端 {cfg['provider']}"}
    except Exception as e:
        return {"ok": False, "detail": f"连不上：{str(e)[:120]}"}
