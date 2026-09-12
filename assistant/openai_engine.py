"""LLM engine for providers that speak the OpenAI chat-completions API (stdlib only, no extra packages).

Lets the assistant run on a free tier - Groq, OpenRouter or Google Gemini all expose an OpenAI-compatible
endpoint - instead of the Anthropic path in agent.py. Same tools, same SQL guard, same answers contract.

Configure with environment variables:
    LLM_PROVIDER   groq | openrouter | gemini | openai        (or set LLM_BASE_URL yourself)
    LLM_API_KEY    the provider's key   (GROQ_API_KEY / OPENROUTER_API_KEY / GEMINI_API_KEY / OPENAI_API_KEY also work)
    LLM_MODEL      optional override - check the provider's current model list, names change often
    LLM_BASE_URL   optional override for any other OpenAI-compatible gateway
"""
import json
import os
import re
import urllib.error
import urllib.request

PROVIDERS = {
    # base_url, default model, env var for the key
    "groq":       ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free", "OPENROUTER_API_KEY"),
    "gemini":     ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash", "GEMINI_API_KEY"),
    "openai":     ("https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY"),
}
MAX_STEPS = 8
TIMEOUT = 90


def configured():
    """-> (provider, base_url, model, key) if an OpenAI-compatible provider is set up, else None."""
    name = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if name not in PROVIDERS and not os.getenv("LLM_BASE_URL"):
        return None
    base_default, model_default, key_env = PROVIDERS.get(name, ("", "", "LLM_API_KEY"))
    key = os.getenv("LLM_API_KEY") or os.getenv(key_env) or ""
    base = (os.getenv("LLM_BASE_URL") or base_default).rstrip("/")
    model = os.getenv("LLM_MODEL") or model_default
    if not (key and base and model):
        return None
    return name or "custom", base, model, key


def to_openai_tools(tools):
    """Anthropic tool schemas -> OpenAI function-tool schemas."""
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in tools]


class OpenAIToolAgent:
    """Drop-in alternative to the Anthropic loop in agent.py. Owns no state beyond the message history."""

    def __init__(self, system, tools, run_tool, provider_cfg):
        self.system, self.tools, self.run_tool = system, to_openai_tools(tools), run_tool
        self.provider, self.base, self.model, self.key = provider_cfg
        self.messages = []

    def _post(self, payload):
        req = urllib.request.Request(
            f"{self.base}/chat/completions", method="POST",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())

    def ask(self, question, Result):
        start = len(self.messages)
        self.messages.append({"role": "user", "content": question})
        trace = []
        try:
            for _ in range(MAX_STEPS):
                body = self._post({"model": self.model, "temperature": 0,
                                   "messages": [{"role": "system", "content": self.system}] + self.messages,
                                   "tools": self.tools, "tool_choice": "auto"})
                msg = body["choices"][0]["message"]
                self.messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
                calls = msg.get("tool_calls") or []
                if not calls:
                    text = (msg.get("content") or "").strip()
                    last = next((t for t in reversed(trace) if "rows" in t), None)
                    # A model that cannot (or did not) call tools will happily invent figures. Any answer that
                    # states numbers without a query behind it is not trustworthy - hand over to the offline engine.
                    if last is None and re.search(r"\d", text) and not text.endswith("?"):
                        raise LLMUnavailable("the model answered with figures but never queried the data "
                                             "(the chosen model may not support tool calling)")
                    kind = "clarify" if text.endswith("?") and not trace else "answer"
                    return Result(text, last and last["sql"], last["columns"] if last else [],
                                  last["rows"] if last else [], kind=kind)
                for call in calls:
                    fn = call["function"]
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        out = "Arguments were not valid JSON; send them again as a JSON object."
                    else:
                        out, _ = self.run_tool(fn["name"], args, trace)
                    self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": out})
            del self.messages[start:]
            return Result("That question needed more steps than I allow. Could you make it more specific?", kind="error")
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, TimeoutError) as e:
            del self.messages[start:]
            detail = e.read().decode()[:200] if isinstance(e, urllib.error.HTTPError) else str(e)
            raise LLMUnavailable(f"{type(e).__name__}: {detail}") from None

    def reset(self):
        self.messages.clear()


class LLMUnavailable(RuntimeError):
    pass
