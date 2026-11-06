"""
大模型后端适配 —— 云端 API + 本地（Ollama / llama.cpp）双配置，供 llm_audit.llm_call() 网关调用

配置（settings 表，后台「安全设计」可改；两套并存互不覆盖）：
- 激活槽位 llm_active：local（本地） / cloud（云端） / 空（未接入）
- 兜底 llm_fallback：on 时激活槽位失败自动切另一套
- 本地槽位：llm_local_provider（ollama / llama.cpp）、llm_local_endpoint、llm_local_model
- 云端槽位（OpenAI 兼容，DeepSeek/腾讯混元/Kimi 等通用）：
  llm_cloud_endpoint（含 /v1）、llm_cloud_model、llm_cloud_api_key（接口只回显是否已配，不回明文）

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


def _set(db: Session, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def llm_config(db: Session) -> dict:
    """完整配置（api key 只回是否已配，绝不回明文）"""

    def get(key: str, legacy: str = "") -> str:
        """新键存在则用新键（显式清空生效）；新键从未写过才回落旧键（v1 兼容）"""
        row = db.query(Setting).filter(Setting.key == key).first()
        if row is not None:
            return (row.value or "").strip()
        return _setting(db, legacy, "")

    local_provider = get("llm_local_provider", "llm_provider")
    if local_provider not in ("ollama", "llama.cpp"):
        local_provider = ""
    local_endpoint = get("llm_local_endpoint", "llm_endpoint")
    local_model = get("llm_local_model", "llm_model")
    cloud_key = _setting(db, "llm_cloud_api_key", "")
    active = get("llm_active") or (local_provider and "local") or ""
    return {
        "active": active,
        "fallback": _setting(db, "llm_fallback", "on"),
        "local": {
            "provider": local_provider,
            "endpoint": local_endpoint,
            "model": local_model,
        },
        "cloud": {
            "endpoint": _setting(db, "llm_cloud_endpoint", ""),
            "model": _setting(db, "llm_cloud_model", ""),
            "apiKeySet": bool(cloud_key),
        },
        "fastModel": _setting(db, "llm_fast_model", ""),
    }


def save_config(db: Session, *, active: str, fallback: bool, local: dict, cloud: dict) -> None:
    if active not in ("", "local", "cloud"):
        raise ValueError("active 须为 local / cloud / 空")
    lp = (local.get("provider") or "").strip()
    if lp not in ("", "ollama", "llama.cpp"):
        raise ValueError("本地后端须为 ollama / llama.cpp / 空")
    _set(db, "llm_active", active)
    _set(db, "llm_fallback", "on" if fallback else "off")
    _set(db, "llm_local_provider", lp)
    _set(db, "llm_local_endpoint", (local.get("endpoint") or "").strip())
    _set(db, "llm_local_model", (local.get("model") or "").strip())
    _set(db, "llm_fast_model", (local.get("fastModel") or "").strip())
    _set(db, "llm_cloud_endpoint", (cloud.get("endpoint") or "").strip())
    _set(db, "llm_cloud_model", (cloud.get("model") or "").strip())
    key = (cloud.get("apiKey") or "").strip()
    if key:  # 空值 = 不修改既有 Key
        _set(db, "llm_cloud_api_key", key)


def _http_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 180) -> dict:
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _chat_ollama(endpoint: str, model: str, prompt: str) -> tuple:
    r = _http_json(
        f"{endpoint.rstrip('/')}/api/chat",
        {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False,
         "options": {"temperature": 0.3}},
    )
    output = ((r.get("message") or {}).get("content")) or ""
    return output, model, int(r.get("prompt_eval_count") or 0), int(r.get("eval_count") or 0)


def _chat_llamacpp(endpoint: str, model: str, prompt: str) -> tuple:
    r = _http_json(
        f"{endpoint.rstrip('/')}/v1/chat/completions",
        {"model": model or "local", "messages": [{"role": "user", "content": prompt}], "temperature": 0.3},
    )
    choices = r.get("choices") or []
    output = ((choices[0].get("message") or {}).get("content")) if choices else ""
    usage = r.get("usage") or {}
    return output or "", model or "llama.cpp", int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def _chat_openai_compatible(endpoint: str, api_key: str, model: str, prompt: str) -> tuple:
    """云端 OpenAI 兼容接口（DeepSeek / 腾讯混元 / Kimi 等）"""
    base = endpoint.rstrip("/")
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    r = _http_json(
        url,
        {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3},
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        timeout=120,
    )
    choices = r.get("choices") or []
    output = ((choices[0].get("message") or {}).get("content")) if choices else ""
    usage = r.get("usage") or {}
    return output or "", model, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def _slot_backend(db: Session, slot: str):
    """单槽位可调用后端；不可用返回 None"""
    cfg = llm_config(db)
    if slot == "local":
        lp = cfg["local"]["provider"]
        ep, mo = cfg["local"]["endpoint"], cfg["local"]["model"]
        if not lp or not ep:
            return None
        if lp == "ollama":
            return lambda p: _chat_ollama(ep, mo, p)
        return lambda p: _chat_llamacpp(ep, mo, p)
    if slot == "cloud":
        ep = cfg["cloud"]["endpoint"]
        key = _setting(db, "llm_cloud_api_key", "")
        mo = cfg["cloud"]["model"]
        if not ep:
            return None
        return lambda p: _chat_openai_compatible(ep, key, mo, p)
    return None


def get_backend(db: Session):
    """激活槽位优先、可选另一槽位兜底；全不可用返回 None"""
    cfg = llm_config(db)
    order = [cfg["active"]] if cfg["active"] in ("local", "cloud") else []
    if cfg["fallback"] == "on":
        for s in ("local", "cloud"):
            if s not in order:
                order.append(s)
    slots = [(s, _slot_backend(db, s)) for s in order]
    slots = [(s, fn) for s, fn in slots if fn is not None]
    if not slots:
        return None

    def backend(prompt: str) -> tuple:
        errs = []
        for s, fn in slots:
            try:
                return fn(prompt)
            except Exception as e:
                errs.append(f"{s}: {str(e)[:80]}")
        raise RuntimeError("模型后端全部失败（" + "；".join(errs) + "）")

    return backend


def get_fast_backend(db: Session):
    """短咨询类问题的快通道（v1.1）：本地小模型（llm_fast_model，如 qwen2.5:3b）。
    未配置或不可用返回 None（调用方回落主力模型）。数据类问题一律不走快通道。"""
    fast = _setting(db, "llm_fast_model", "").strip()
    if not fast:
        return None
    lp = _setting(db, "llm_local_provider", "llm_provider").strip()
    ep = _setting(db, "llm_local_endpoint", "llm_endpoint").strip()
    if lp not in ("ollama", "llama.cpp") or not ep:
        return None
    if lp == "ollama":
        return lambda p: _chat_ollama(ep, fast, p)
    return lambda p: _chat_llamacpp(ep, fast, p)


def ping(db: Session) -> dict:
    """连通性检查（本地 + 云端分别报告）"""
    out = {}
    cfg = llm_config(db)
    # 本地
    lp = cfg["local"]["provider"]
    ep = cfg["local"]["endpoint"]
    if lp and ep:
        try:
            if lp == "ollama":
                with urllib.request.urlopen(f"{ep.rstrip('/')}/api/tags", timeout=6) as r:
                    data = json.loads(r.read().decode("utf-8"))
                n = len(data.get("models") or [])
                out["local"] = {"ok": True, "detail": f"Ollama 可用，已装模型 {n} 个"}
            else:
                with urllib.request.urlopen(f"{ep.rstrip('/')}/health", timeout=6) as r:
                    out["local"] = {"ok": r.status == 200, "detail": f"llama.cpp 健康检查 HTTP {r.status}"}
        except Exception as e:
            out["local"] = {"ok": False, "detail": f"连不上：{str(e)[:100]}"}
    else:
        out["local"] = {"ok": False, "detail": "未配置"}
    # 云端（只验连通与鉴权，不发完整生成）
    ep = cfg["cloud"]["endpoint"]
    if ep:
        key = _setting(db, "llm_cloud_api_key", "")
        try:
            base = ep.rstrip("/")
            url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
            _http_json(url, {"model": cfg["cloud"]["model"] or "unknown",
                             "messages": [{"role": "user", "content": "ping"}],
                             "max_tokens": 1},
                       headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=20)
            out["cloud"] = {"ok": True, "detail": "云端 API 可用"}
        except Exception as e:
            out["cloud"] = {"ok": False, "detail": f"云端不可用：{str(e)[:100]}"}
    else:
        out["cloud"] = {"ok": False, "detail": "未配置"}
    out["ok"] = (cfg["active"] in ("local", "cloud") and out.get(cfg["active"], {}).get("ok", False)) or (
        cfg["fallback"] == "on" and any(out[s]["ok"] for s in ("local", "cloud"))
    )
    out["detail"] = f"本地：{out['local']['detail']}｜云端：{out['cloud']['detail']}"
    return out
