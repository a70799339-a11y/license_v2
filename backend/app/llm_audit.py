"""
大模型调用审计网关 —— 所有 LLM 调用的唯一入口（强制过账）

架构约束：集团总端 / 门店客户端 / 管理后台 一律不直连 LLM API，
统一经本模块调用，保证每次调用全量留痕：
  调用来源（集团/门店/后台）+ 发起人 + 场景 + 用户输入 + 模型输入/输出
  + 模型名 + token 用量 + 耗时 + 状态 —— 写入 llm_call_logs（自动脱敏）。

用法（未来接入任意 LLM 时）：
    from .llm_audit import llm_call
    result = llm_call(
        db, source_type="hotel", source_id=hotel.id, source_name=hotel.name,
        user_id="wofUQmZQ...", scene="告警解读",
        user_input="这条告警怎么处理？",
        prompt=f"请解读告警：{alert.detail}",
        backend=my_llm_backend,   # fn(prompt) -> (output, model_name, tokens_in, tokens_out)
    )
    # result["output"] 为模型输出；无论成功失败都已记入审计日志
"""
import time
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy.orm import Session

from .masking import mask_text
from .models import LLMCallLog


def log_llm_call(
    db: Session,
    *,
    source_type: str,
    source_id: str = "",
    source_name: str = "",
    user_id: str = "",
    scene: str = "",
    user_input: str = "",
    model_input: str = "",
    model_output: str = "",
    model_name: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int = 0,
    status: str = "success",
    error: str = "",
) -> LLMCallLog:
    """写入一条 LLM 调用审计记录（所有文本字段统一脱敏后落库）"""
    rec = LLMCallLog(
        source_type=source_type,
        source_id=source_id[:32],
        source_name=mask_text(source_name)[:128],
        user_id=user_id[:64],
        scene=scene[:64],
        user_input=mask_text(user_input)[:4000],
        model_input=mask_text(model_input)[:8000],
        model_output=mask_text(model_output)[:8000],
        model_name=model_name[:64],
        tokens_in=tokens_in or 0,
        tokens_out=tokens_out or 0,
        latency_ms=latency_ms or 0,
        status=status,
        error=mask_text(error)[:256],
    )
    db.add(rec)
    db.flush()
    return rec


def llm_call(
    db: Session,
    *,
    source_type: str,
    source_id: str = "",
    source_name: str = "",
    user_id: str = "",
    scene: str = "",
    user_input: str = "",
    prompt: str,
    backend: Optional[Callable] = None,
) -> dict:
    """
    大模型调用唯一入口：执行 + 自动审计。
    backend: fn(prompt) -> (output, model_name, tokens_in, tokens_out)。
    未传 backend 时自动使用本地模型后端（Ollama / llama.cpp，settings 配置），
    本地后端未配置时返回占位结果（审计管道照常运转）。
    """
    t0 = time.time()
    try:
        if backend is None:
            from .llm_backend import get_backend
            backend = get_backend(db)
        if backend is not None:
            output, model_name, tin, tout = backend(prompt)
            status, error = "success", ""
        else:
            # 尚未接入具体模型供应商时的占位（审计管道照常运转）
            output, model_name, tin, tout = (
                "（大模型供应商未接入，本次为审计管道占位调用）",
                "none",
                0,
                0,
            )
            status, error = "success", ""
    except Exception as e:  # 调用失败同样留痕
        output, model_name, tin, tout = "", "unknown", 0, 0
        status, error = "error", str(e)[:250]

    latency = int((time.time() - t0) * 1000)
    log_llm_call(
        db,
        source_type=source_type,
        source_id=source_id,
        source_name=source_name,
        user_id=user_id,
        scene=scene,
        user_input=user_input,
        model_input=prompt,
        model_output=output,
        model_name=model_name,
        tokens_in=tin,
        tokens_out=tout,
        latency_ms=latency,
        status=status,
        error=error,
    )
    db.commit()
    return {
        "output": output,
        "model": model_name,
        "tokens_in": tin,
        "tokens_out": tout,
        "latency_ms": latency,
        "status": status,
        "error": error,
    }
