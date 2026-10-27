"""LLM 后端：本机 Claude Code CLI / Anthropic API / OpenAI 兼容接口（DeepSeek、Qwen 等）/ 关闭。

所有后端都只暴露一个方法：complete_json(system, user, schema) -> dict。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

from .config import DATA_DIR, load_settings


class LLMUnavailable(RuntimeError):
    pass


def _extract_json(text: str):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = text.find(open_c), text.rfind(close_c)
        if i >= 0 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"模型输出不是合法 JSON：{text[:200]}")


class ClaudeCLI:
    """调用本机已登录的 `claude` 命令（Claude Code），无需单独 API Key。"""

    def __init__(self, cfg: dict):
        self.model = cfg.get("claude_cli_model") or "sonnet"
        self.timeout = cfg.get("timeout", 180)
        self.exe = shutil.which("claude")
        if not self.exe:
            raise LLMUnavailable("未找到 claude 命令，请安装 Claude Code 或换用其它后端")

    def complete_json(self, system: str, user: str, schema: dict | None = None):
        prompt = user
        if schema:
            prompt += "\n\n只输出一个符合下列 JSON Schema 的 JSON，不要任何解释或代码块标记：\n" + json.dumps(schema, ensure_ascii=False)
        cmd = [self.exe, "-p", "--model", self.model, "--output-format", "json",
               "--no-session-persistence", "--strict-mcp-config", "--tools", "",
               "--system-prompt", system]
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           timeout=self.timeout, cwd=str(DATA_DIR))
        if p.returncode != 0:
            raise RuntimeError(f"claude CLI 失败：{(p.stderr or p.stdout)[:300]}")
        outer = json.loads(p.stdout)
        if outer.get("is_error"):
            raise RuntimeError(f"claude CLI 报错：{str(outer.get('result'))[:300]}")
        return _extract_json(outer.get("result", ""))


class AnthropicAPI:
    # 这些模型支持 effort 与服务端拒答回退（fallbacks="default"）
    MODERN = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")

    def __init__(self, cfg: dict):
        import anthropic

        key = cfg.get("anthropic_api_key") or None
        self.client = anthropic.Anthropic(api_key=key, timeout=cfg.get("timeout", 180))
        self.model = cfg.get("anthropic_model") or "claude-opus-5-5"

    def complete_json(self, system: str, user: str, schema: dict | None = None):
        output_config: dict = {}
        kwargs: dict = {}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if self.model.startswith(self.MODERN):
            output_config["effort"] = "low"
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        resp = self.client.beta.messages.create(
            model=self.model, max_tokens=8000, system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config or None, **kwargs)
        if resp.stop_reason == "refusal":
            raise RuntimeError("模型拒绝回答该请求")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return _extract_json(text)


class OpenAICompat:
    def __init__(self, cfg: dict):
        from openai import OpenAI

        if not cfg.get("openai_api_key"):
            raise LLMUnavailable("未设置 OpenAI 兼容接口的 API Key")
        self.client = OpenAI(base_url=cfg.get("openai_base_url") or None, api_key=cfg["openai_api_key"],
                             timeout=cfg.get("timeout", 180))
        self.model = cfg.get("openai_model")

    def complete_json(self, system: str, user: str, schema: dict | None = None):
        if schema:
            user += "\n\n只输出一个符合下列 JSON Schema 的 JSON：\n" + json.dumps(schema, ensure_ascii=False)
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            r = self.client.chat.completions.create(model=self.model, messages=msgs,
                                                    response_format={"type": "json_object"})
        except Exception:
            r = self.client.chat.completions.create(model=self.model, messages=msgs)
        return _extract_json(r.choices[0].message.content or "")


def get_llm(cfg: dict | None = None):
    """返回当前配置的后端；后端为 none 时返回 None（全部走规则提取）。"""
    cfg = cfg or load_settings()["llm"]
    backend = cfg.get("backend", "none")
    if backend == "claude_cli":
        return ClaudeCLI(cfg)
    if backend == "anthropic":
        return AnthropicAPI(cfg)
    if backend == "openai":
        return OpenAICompat(cfg)
    return None
