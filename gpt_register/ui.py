import json
from typing import Any

from rich.console import Console, RenderableType
from rich.json import JSON
from rich.panel import Panel
from rich.text import Text


console = Console(highlight=False, soft_wrap=True)


def _style_for_text(text: str) -> str | None:
    lowered = text.lower()
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("[error]") or " 本次注册失败" in text or "失败" in text and "成功" not in text:
        return "bold red"
    if stripped.startswith("[warning]") or "warning" in lowered:
        return "yellow"
    if "抓到啦" in text or "注册成功" in text or "预检通过" in text or "验证通过" in text:
        return "bold green"
    if stripped.startswith("[graph调试]") or stripped.startswith("[debug]") or "[debug][" in lowered:
        return "magenta"
    if "开始注册" in text or stripped.startswith("[*]"):
        return "cyan"
    if stripped.startswith("[状态]") or stripped.startswith("● 实时状态"):
        return "bold blue"
    return None


def _maybe_json_renderable(text: str) -> RenderableType | None:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except Exception:
        return None
    return Panel(
        JSON.from_data(payload),
        title="响应 JSON",
        border_style="red",
        padding=(0, 1),
    )


def rich_print(*args: Any, sep: str = " ", end: str = "\n", flush: bool = False, **kwargs: Any) -> None:
    del flush, kwargs
    text = sep.join(str(arg) for arg in args)

    # 压制轮询中的 dot spam，避免终端被 "." 刷屏
    if text == "." and end == "":
        return

    json_renderable = _maybe_json_renderable(text)
    if json_renderable is not None and end == "\n":
        console.print(json_renderable)
        return

    style = _style_for_text(text)
    renderable: RenderableType = Text(text, style=style) if style else Text(text)
    console.print(renderable, end=end, highlight=False, soft_wrap=True)

