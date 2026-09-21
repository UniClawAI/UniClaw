from __future__ import annotations
import base64
import hashlib
import logging
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
import json
from pathlib import Path
from typing import Any, TYPE_CHECKING
import uuid

logger = logging.getLogger("session")
from uniclaw.utils.message import MessageRole
from uniclaw.utils.tokens import get_encoder, count_tokens
from uniclaw.provider.types import Usage

_INT16_MAX = 32768.0  # int16 归一化除数

if TYPE_CHECKING:
    from uniclaw.config import AppConfig


def _estimate_visual_tokens(block: dict) -> int:
    btype = block.get("type")
    url = ""
    if btype == "image_url":
        url = block.get("image_url", {}).get("url", "")
    elif btype == "video_url":
        url = block.get("video_url", {}).get("url", "")
    else:
        return 0
    try:
        import base64
        from io import BytesIO
        from PIL import Image

        if not url.startswith("data:"):
            return 85
        _, data = url.split(",", 1)
        img = Image.open(BytesIO(base64.b64decode(data)))
        w, h = img.size
        tiles = ((w + 511) // 512) * ((h + 511) // 512)
        return 85 + tiles * 170
    except Exception as e:
        logger.debug("估算视觉 token 失败,使用默认值: %s", e)
        return 500


def _estimate_audio_tokens(block: dict) -> int:
    if block.get("type") != "input_audio":
        return 0
    try:
        data = block.get("input_audio", {}).get("data", "")
        if not data:
            return 100
        audio_bytes = len(data) * 3 / 4
        duration_seconds = audio_bytes / 24000
        return max(50, int(duration_seconds * 10))
    except Exception as e:
        logger.debug("估算音频 token 失败,使用默认值: %s", e)
        return 500


def _count_str_chars(obj) -> int:
    if isinstance(obj, str):
        return len(obj)
    if isinstance(obj, dict):
        return sum(_count_str_chars(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_count_str_chars(item) for item in obj)
    return 0


class MultimodalType(StrEnum):
    text = "text"
    image_url = "image_url"
    input_audio = "input_audio"
    video_url = "video_url"


@dataclass
class MultimodalBlock:
    type: MultimodalType
    text: str | None = None
    image_url: dict[str, str] | None = None
    input_audio: dict[str, str] | None = None
    video_url: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MultimodalBlock":
        block_type = MultimodalType(data.get("type", "text"))
        return cls(
            type=block_type,
            text=(
                data.get(MultimodalType.text)
                if block_type == MultimodalType.text
                else None
            ),
            image_url=(
                data.get(MultimodalType.image_url)
                if block_type == MultimodalType.image_url
                else None
            ),
            input_audio=(
                data.get(MultimodalType.input_audio)
                if block_type == MultimodalType.input_audio
                else None
            ),
            video_url=(
                data.get(MultimodalType.video_url)
                if block_type == MultimodalType.video_url
                else None
            ),
        )

    def to_openai_message(self) -> dict[str, Any]:
        if self.type == MultimodalType.text:
            return {"type": MultimodalType.text, "text": self.text or ""}
        if self.type == MultimodalType.image_url:
            return {"type": MultimodalType.image_url, "image_url": self.image_url}
        if self.type == MultimodalType.input_audio:
            return {
                "type": MultimodalType.input_audio,
                "input_audio": self.input_audio,
            }
        if self.type == MultimodalType.video_url:
            return {"type": MultimodalType.video_url, "video_url": self.video_url}
        raise ValueError(f"Unsupported multimodal block type: {self.type}")

    def to_anthropic_message(self) -> dict[str, Any]:
        if self.type == MultimodalType.text:
            return {"type": "text", "text": self.text or ""}
        if self.type == MultimodalType.image_url:
            url = self.image_url.get("url", "") if self.image_url else ""
            if url.startswith("data:"):
                parts = url.split(",", 1)
                media_type = parts[0].split(":")[1].split(";")[0]
                source = {
                    "type": "base64",
                    "media_type": media_type,
                    "data": parts[1] if len(parts) > 1 else "",
                }
            else:
                source = {"type": "url", "url": url}
            return {"type": "image", "source": source}
        # Anthropic 不原生支持 audio/video,降级为文本占位
        if self.type == MultimodalType.input_audio:
            return {"type": "text", "text": "[audio]"}
        if self.type == MultimodalType.video_url:
            return {"type": "text", "text": "[video]"}
        raise ValueError(f"Unsupported multimodal block type: {self.type}")

    def to_dict(self) -> dict[str, Any]:
        return self.to_openai_message()

    def to_str(self) -> str:
        if self.type == MultimodalType.text:
            content = self.text
        else:
            content = f"[{self.type}]"
        return content


MultimodalContent = list[MultimodalBlock]
SupportedContent = str | MultimodalContent


@dataclass
class BaseMessage:
    """消息基类提供 content 和 token 估算。"""

    content: SupportedContent = ""
    created_at: datetime | None = None

    @staticmethod
    def _parse_created_at(data: dict[str, Any]) -> datetime | None:
        """从序列化字典中解析 created_at,缺失或格式错误时返回 None。"""
        raw = data.get("created_at")
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                return None
        return raw if isinstance(raw, datetime) else None

    @property
    def role(self) -> str:
        raise NotImplementedError

    def to_openai_message(self) -> dict[str, Any]:
        raise NotImplementedError

    def to_anthropic_message(self) -> dict[str, Any]:
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def to_str(self) -> str:
        raise NotImplementedError

    def to_content(self) -> str:
        raise NotImplementedError

    def estimate_tokens(self, model: str | None = None) -> int:
        """估算本条消息的 token 数量。"""
        total = 0
        content = self.content
        if isinstance(content, str):
            total += count_tokens(content, model)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, MultimodalBlock):
                    block = block.to_dict()
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "text")
                if btype in ("image_url", "video_url"):
                    total += _estimate_visual_tokens(block)
                elif btype == "input_audio":
                    total += _estimate_audio_tokens(block)
                else:
                    text = block.get("text", "")
                    if text:
                        total += count_tokens(text, model)
        msg = self.to_openai_message()
        for tc in msg.get("tool_calls") or []:
            total += _count_str_chars(tc)
        # 框架开销: 每条消息 4 tokens + 5% 缓冲
        return int((total + 4) * 1.05)


@dataclass
class UserMessage(BaseMessage):

    @property
    def role(self) -> str:
        return MessageRole.USER

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserMessage":
        content = data["content"]
        if isinstance(content, list):
            content = [MultimodalBlock.from_dict(block) for block in content]
        return cls(content=content, created_at=cls._parse_created_at(data))

    def to_openai_message(self) -> dict[str, Any]:

        if isinstance(self.content, list):
            content = [block.to_openai_message() for block in self.content]
        else:
            content = self.content
        return {
            "role": MessageRole.USER,
            "content": content,
        }

    def to_anthropic_message(self) -> dict[str, Any]:
        if isinstance(self.content, list):
            content = [block.to_anthropic_message() for block in self.content]
        else:
            content = self.content
        return {
            "role": MessageRole.USER,
            "content": content,
        }

    def to_dict(self) -> dict[str, Any]:
        data = self.to_openai_message()
        if self.created_at is not None:
            data["created_at"] = self.created_at.isoformat()
        return data

    def to_str(self) -> str:
        return f"[user]:{self.to_content()}"

    def to_content(self) -> str:
        if isinstance(self.content, list):
            return "\n".join([block.to_str() for block in self.content])
        else:
            return self.content


@dataclass
class AIMessage(BaseMessage):
    model_name: str = ""
    usage: Usage | None = None
    reasoning_content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    audio: str | None = None

    def audio_to_pcm(self) -> tuple[np.ndarray, int]:
        """将 base64 音频转换为归一化的 float32 PCM 数组。

        支持 WAV 格式(带 header)和 raw PCM16 格式。

        Returns:
            (pcm_data, sample_rate) 元组。
        """
        import io
        import soundfile as sf

        if not self.audio:
            raise ValueError("当前消息没有音频数据")
        raw_bytes = base64.b64decode(self.audio)
        # 检测是否为 WAV 格式(以 RIFF 开头)
        if raw_bytes[:4] == b"RIFF":
            data, sr = sf.read(io.BytesIO(raw_bytes), dtype="float32")
            return data, sr
        # raw PCM16
        return (
            np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / _INT16_MAX,
            24000,
        )

    def play(self, sample_rate: int | None = None, wait: bool = True) -> None:
        """播放音频。

        Args:
            sample_rate: 采样率,默认从音频数据中读取。
            wait: 是否等待播放完成。
        """
        import sounddevice as sd

        pcm, sr = self.audio_to_pcm()
        sd.play(pcm, samplerate=sample_rate or sr)
        if wait:
            sd.wait()

    def save_audio(self, path: str | Path) -> Path:
        """保存音频到文件。

        Args:
            path: 输出文件路径,格式由扩展名决定(如 .wav, .flac, .ogg)。
            sample_rate: 采样率,默认从音频数据中读取。

        Returns:
            保存的文件路径。
        """
        if not self.audio:
            raise ValueError("当前消息没有音频数据")
        audio_bytes = base64.b64decode(self.audio)
        with open(path, "wb") as f:
            f.write(audio_bytes)

    @property
    def role(self) -> str:
        return MessageRole.ASSISTANT

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AIMessage":
        content = data["content"]
        if isinstance(content, list):
            content = [MultimodalBlock.from_dict(block) for block in content]
        return cls(
            content=content,
            model_name=data.get("model_name", ""),
            usage=Usage.from_dict(data.get("usage", {})),
            reasoning_content=data.get("reasoning_content"),
            tool_calls=data.get("tool_calls"),
            created_at=cls._parse_created_at(data),
        )

    def to_openai_message(self) -> dict[str, Any]:
        msg = {
            "role": MessageRole.ASSISTANT,
            "content": (
                self.content.to_openai_message()
                if isinstance(self.content, list)
                else self.content
            ),
        }
        if self.reasoning_content:
            msg["reasoning_content"] = self.reasoning_content
        else:
            msg["reasoning_content"] = " "
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        return msg

    def to_anthropic_message(self) -> dict[str, Any]:
        blocks = []
        if self.reasoning_content:
            blocks.append({"type": "thinking", "thinking": self.reasoning_content})
        else:
            blocks.append({"type": "thinking", "thinking": " "})
        content = self.content
        if isinstance(content, list):
            blocks.extend(b.to_anthropic_message() for b in content)
        elif content:
            blocks.append({"type": "text", "text": content})
        if self.tool_calls:
            for tc in self.tool_calls:
                func = tc.get("function", {})

                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": (
                            json.loads(func.get("arguments", "{}"))
                            if isinstance(func.get("arguments"), str)
                            else func.get("arguments", {})
                        ),
                    }
                )
        return {"role": MessageRole.ASSISTANT, "content": blocks}

    def to_dict(self) -> dict[str, Any]:
        data = self.to_openai_message()
        data["usage"] = self.usage.to_dict()
        data["model_name"] = self.model_name
        if self.created_at is not None:
            data["created_at"] = self.created_at.isoformat()
        return data

    def to_content(self) -> str:
        if isinstance(self.content, list):
            return "\n".join([block.to_str() for block in self.content])
        else:
            return self.content

    def to_str(self) -> str:
        return f"[assistant]:{self.to_content()}"


@dataclass
class StreamChunk(AIMessage):
    """流式 chunk,支持 += 累积。"""

    new_tool_call_name: str = ""
    new_tool_call_args: dict = field(default_factory=dict)

    def __iadd__(self, other: StreamChunk) -> StreamChunk:
        self.content += other.content
        self.reasoning_content += other.reasoning_content
        self.tool_calls.extend(other.tool_calls)
        if other.model_name:
            self.model_name = other.model_name
        if other.usage:
            self.usage = other.usage
        if other.audio:
            self.audio = (self.audio or "") + other.audio
        return self


@dataclass
class ToolCallMessage(BaseMessage):
    name: str = ""
    tool_call_id: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    explain: str = ""  # AI 对本次工具调用的解释(explain 模式)
    # 内容哈希缓存: 惰性计算,__setattr__ 拦截 content/args 赋值自动失效
    _hash_cache: str = field(default="", repr=False, compare=False)

    def __setattr__(self, key: str, value: Any) -> None:
        """content/args 被重新赋值时自动使哈希缓存失效。

        覆盖 msg.content = ... / self.content = ... / setattr() 等所有赋值路径;
        未覆盖列表/字典元素的原地修改(当前代码库无此用法)。
        """
        if key in ("content", "args"):
            object.__setattr__(self, "_hash_cache", "")
        object.__setattr__(self, key, value)

    def _content_text(self) -> str:
        """结果内容扁平化为文本,兼容 MultimodalBlock / 原始 dict 块。"""
        if isinstance(self.content, list):
            parts = []
            for block in self.content:
                if isinstance(block, MultimodalBlock):
                    parts.append(block.to_str())
                elif isinstance(block, dict):
                    parts.append(
                        block.get("text") or f"[{block.get('type', 'unknown')}]"
                    )
                else:
                    parts.append(str(block))
            return "\n".join(parts)
        return self.content or ""

    @property
    def content_hash(self) -> str:
        """工具名+参数+结果内容的 SHA-256 短哈希(前 16 位),惰性计算并缓存。

        参数使用完整 JSON 序列化(排序键),结果内容为原始文本,不经显示截断。
        不持久化到磁盘;content/args 赋值时由 __setattr__ 自动失效。
        """
        if not self._hash_cache:
            material = (
                f"{self.name}\x1f"
                f"{json.dumps(self.args, sort_keys=True, ensure_ascii=False, default=str)}\x1f"
                f"{self._content_text()}"
            )
            self._hash_cache = hashlib.sha256(
                material.encode("utf-8", errors="replace")
            ).hexdigest()[:16]
        return self._hash_cache

    @property
    def role(self) -> str:
        return MessageRole.TOOL

    def to_openai_message(self) -> dict[str, Any]:
        return {
            "role": MessageRole.TOOL,
            "name": self.name,
            "content": self.content,
            "tool_call_id": self.tool_call_id,
        }

    def to_anthropic_message(self) -> dict[str, Any]:
        return {
            "role": MessageRole.USER,
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": self.tool_call_id,
                    "content": self.content,
                }
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        data = self.to_openai_message()
        data["args"] = self.args
        if self.explain:
            data["explain"] = self.explain
        if self.created_at is not None:
            data["created_at"] = self.created_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCallMessage":
        return cls(
            name=data.get("name", ""),
            tool_call_id=data.get("tool_call_id", ""),
            content=data.get("content", ""),
            args=data.get("args", {}),
            explain=data.get("explain", ""),
            created_at=cls._parse_created_at(data),
        )

    def to_content(self) -> str:
        from uniclaw.utils.format import format_args_for_display

        args_str = (
            format_args_for_display(self.args, max_length=1000) if self.args else ""
        )
        call = f"{self.name}({args_str})"
        if isinstance(self.content, list):
            text = "\n".join([block.to_str() for block in self.content])
        else:
            text = self.content or ""
        return f"{call}\n{text}" if text else call

    def to_str(self) -> str:
        content = self.to_content()
        if not content:
            return ""
        return f"[tool]: {content}"


# 结构化 checkpoint 模板 — 续作摘要,替代自由文本摘要
CHECKPOINT_TEMPLATE = """请将以下对话整理为**续作摘要**。这份摘要将**替换原对话**,交给一个全新的 agent,它只能靠这份摘要继续工作。

本次压缩的特别关注点: __FOCUS__

严格按以下 Markdown 格式输出,只输出摘要内容,不要输出其他任何内容:

## 用户需求
{用户的原始请求,尽量保留原话;多次请求按时间顺序逐条列出,简短指令如"继续"也要保留}

## 当前进度
{agent 正在做什么任务,已经推进到哪一步}

## 已完成
{已完成的子任务和操作,简要列出}

## 待完成
{未完成或进行中的子任务}

## 下一步动作
{接下来应该做什么,具体怎么做}

## 涉及文件
{文件路径 + 做了什么操作;若涉及函数/API,附签名或关键返回类型;每行一条}

## 关键决策
{重要的设计/技术决策及原因}

## 关键认知
{对话中沉淀的技术结论、API 行为、模式;如"某函数返回 dict 而非 int""某装饰器是 async",续作不必重新发现}

## 错误与修复
{遇到的问题、原因、解决方案;保留关键报错行}

## 归档信息
{归档消息条数和大致时间范围}
{核心主题词,用于检索,不限于这些词}

硬性要求:
- 文件路径、URL、端口号、变量名、命令、API/工具名称必须一字不改完整保留
- 报错信息保留关键行,可精简不可省略
- 用户需求一节把每条用户消息原文列出,不合并、不概括
- 已写入会话笔记(session_note_add)的内容不展开复述,仅在相关处标注"详见笔记 xxx";笔记正文不会因压缩丢失,无需在摘要中备份
- 信息密度优先,不写空话套话;没有对应内容的分节写"无"
- 总长度控制在 __BUDGET__ token 以内"""

# 压缩摘要消息的前缀 — 生成端(compact)与识别端(recall 前缀扫描)共用。
# 改动此处时两端自动同步,避免压缩会话无法被 recall 识别。
SUMMARY_PREFIX = "[之前的对话摘要]"

# 摘要输出 token 预算下限/上限(自适应: 按被替换的 token 量分配)
_SUMMARY_MIN_TOKENS = 500
_SUMMARY_MAX_TOKENS = 1500

# 压缩预警阈值系数:达到某压力等级阈值 * 此系数时,通过 wake_agent 注入
# "写遗言"提示,让 LLM 在压缩发生前把关键信息存入会话笔记。
# 预警必须先于压缩触发,故取阈值打折(0.9)。
_COMPACT_WARN_FACTOR = 0.9
# 预警的最低压力等级:低于此等级的压缩不损失信息(仅微压缩清空可再生工具结果),
# 无需预警。0 = 含 level 0 在内都预警,1 = 只预警 level 1 及以上。
_WARN_LEVEL_MIN = 1


def _build_summary_transcript(
    messages: list,
    *,
    max_user_chars: int = 2000,
    max_assistant_chars: int = 1500,
    max_result_chars: int = 500,
    max_total_chars: int = 12000,
) -> str:
    """把待归档消息整理为有界、结构化的转录文本,供摘要 LLM 使用。

    相比直接 `[role]: content` 拼接,这里对每类消息做了针对性处理:
    - 用户消息: 截断到 max_user_chars,保留原始请求
    - 助手消息: 截断文本,若发起了工具调用则附一行调用工具名
    - 工具消息: 参数经 format_args_for_display 缩短,结果截断并标记
    - 整体超过 max_total_chars 即停止,防止摘要输入爆炸

    Args:
        messages: 待归档的消息列表。
        max_user_chars: 单条用户消息的最大字符数。
        max_assistant_chars: 单条助手消息的最大字符数。
        max_result_chars: 单条工具结果的最大字符数。
        max_total_chars: 转录总字符数上限。

    Returns:
        str: 有界的转录文本。
    """
    from uniclaw.utils.format import format_args_for_display

    parts: list[str] = []
    total = 0
    for m in messages:
        if isinstance(m, UserMessage):
            text = m.to_content()
            if len(text) > max_user_chars:
                text = text[:max_user_chars] + "\n...(已省略)"
            line = f"[用户]: {text}"
        elif isinstance(m, AIMessage):
            text = m.to_content()
            if len(text) > max_assistant_chars:
                text = text[:max_assistant_chars] + "\n...(已省略)"
            line = f"[助手]: {text}"
            if m.tool_calls:
                names = [tc.get("function", {}).get("name", "?") for tc in m.tool_calls]
                line += f"\n  (调用工具: {', '.join(names)})"
        elif isinstance(m, ToolCallMessage):
            args_str = format_args_for_display(m.args, max_length=120)
            call = f"{m.name}({args_str})" if m.args else m.name
            result = m.content if isinstance(m.content, str) else m.to_content() or ""
            if len(result) > max_result_chars:
                result = result[:max_result_chars] + "\n...(结果已截断)"
            line = f"[工具] {call}"
            if result:
                line += f"\n{result}"
        else:
            continue
        remaining = max_total_chars - total
        if remaining <= 0:
            break
        if len(line) > remaining:
            line = line[:remaining] + "\n...(已省略)"
        parts.append(line)
        total += len(line)
    return "\n\n".join(parts)


class SessionType(StrEnum):
    CONSOLE = "console"
    WECHAT = "wechat"
    FREE_CHAT = "free_chat"
    A2A = "a2a"


# add_* 方法的 created_at 哨兵值: 区分"未传参"(用 datetime.now())和"显式传 None"(保留 None)
_UNSET = object()


@dataclass
class SessionNote:
    """会话笔记 — 单条笔记条目,随会话持久化,压缩时摘要注入。"""

    name: str          # 唯一标识,简短名称
    description: str   # 一句话摘要
    content: str       # 完整内容

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "content": self.content}

    @classmethod
    def from_dict(cls, data: dict) -> "SessionNote":
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            content=data.get("content", ""),
        )


@dataclass
class Session:
    root_dir: Path | None = None
    id: str = ""
    start_time: datetime = field(default_factory=datetime.now)
    title: str | None = None
    session_type: SessionType = SessionType.CONSOLE
    system_prompt: str | None = None
    session_notes: list[SessionNote] = field(default_factory=list)

    @property
    def is_wechat(self) -> bool:
        return self.session_type == SessionType.WECHAT

    _messages: list[UserMessage | AIMessage | ToolCallMessage] = field(
        default_factory=list
    )
    history: list[UserMessage | AIMessage | ToolCallMessage] = field(
        default_factory=list
    )
    _compact_end: int = field(
        default=0, repr=False
    )  # 压缩区域结束索引:_messages[:_compact_end] 为压缩区,_messages[_compact_end:] 为 recent
    _compact_warned_levels: set[int] = field(
        default_factory=set, repr=False
    )  # 已注入过"写遗言"预警的压力等级 (0/1/2)
    from uniclaw.tools.fs import Glob, Read
    from uniclaw.tools.search import webSearch
    from uniclaw.tools.shell import Grep
    from uniclaw.tools.web import webFetch
    from uniclaw.tools.web_browse.tools import (
        browser_get_text,
        browser_get_html,
        browser_get_attribute,
        browser_get_elements,
        browser_get_url,
        
        browser_get_title,
        browser_get_value,
        browser_get_count,
        browser_get_box,
        browser_get_styles,
        browser_list_pages,
    )
    from uniclaw.tools.knowledge.tools import (
        kg_get_entity,
        kg_search,
        kg_neighbors,
        kg_path,
        kg_stats,
        kg_list,
    )
    from uniclaw.tools.memory.tools import memory_list, memory_search
    from uniclaw.tools.rag.tools import rag_search, rag_list_collections
    from uniclaw.tools.todolist import todolist_list
    from uniclaw.tools.help import list_slash_commands, get_command_help
    from uniclaw.tools.monitor.tools import monitor_list, monitor_output
    from uniclaw.tools.scheduler.tools import schedule_list
    from uniclaw.tools.mcp.tools import mcp_list_servers
    from uniclaw.tools.advisor import advisor_list
    from uniclaw.tools.a2a.tools import a2a_list_agents
    from uniclaw.tools.hooks.tools import hook_docs, hook_read
    from uniclaw.tools.session.recall import recall_history
    from uniclaw.tools.ipython.tools import ipython_vars, ipython_list_kernels
    from uniclaw.tools.registry import search_tools

    # 只读工具去重集合
    _DEDUP_TOOLS = frozenset(
        {
            # fs
            Read.name,
            Glob.name,
            # shell
            Grep.name,
            # web
            webFetch.name,
            # search
            webSearch.name,
            # web_browse (只读 getter)
            browser_list_pages.name,
            browser_get_text.name,
            browser_get_html.name,
            browser_get_attribute.name,
            browser_get_elements.name,
            browser_get_url.name,
            browser_get_title.name,
            browser_get_value.name,
            browser_get_count.name,
            browser_get_box.name,
            browser_get_styles.name,
            # knowledge graph (只读查询)
            kg_get_entity.name,
            kg_search.name,
            kg_neighbors.name,
            kg_path.name,
            kg_stats.name,
            kg_list.name,
            # memory (只读查询)
            memory_list.name,
            memory_search.name,
            # rag (只读查询)
            rag_search.name,
            rag_list_collections.name,
            # todolist
            todolist_list.name,
            # help
            list_slash_commands.name,
            get_command_help.name,
            # monitor (只读)
            monitor_list.name,
            monitor_output.name,
            # scheduler
            schedule_list.name,
            # mcp
            mcp_list_servers.name,
            # advisor (只读)
            advisor_list.name,
            # a2a
            a2a_list_agents.name,
            # hooks (只读)
            hook_docs.name,
            hook_read.name,
            # session recall
            recall_history.name,
            # ipython (只读)
            ipython_vars.name,
            ipython_list_kernels.name,
            # registry
            search_tools.name,
        }
    )
    _DEDUP_MIN_CHARS = 2000

    # 可再生工具 — 结果可以重新执行获取,压缩时直接清空
    COMPACTABLE_TOOLS = frozenset(
        {
            Read.name,
            Grep.name,
            Glob.name,
            webFetch.name,
        }
    )

    def __post_init__(self) -> None:
        if not self.id:
            now = datetime.now()
            timestamp = now.strftime("%Y%m%d_%H%M%S")
            self.id = f"{timestamp}_{uuid.uuid4().hex[:12]}"
            self.start_time = now
        # 自由聊天模式:自动分配独立工作目录
        if self.session_type == SessionType.FREE_CHAT and self.root_dir is None:
            from uniclaw.context import Scope, get_app_dir

            self.root_dir = get_app_dir(Scope.USER) / "workspace" / self.id
            self.root_dir.mkdir(parents=True, exist_ok=True)

    def _find_duplicate_hash(self, tool_name: str, dedup_key: str) -> bool:
        """遍历消息列表,判断是否存在同名工具且内容哈希相同的历史调用。

        无独立缓存:哈希取自消息上惰性缓存的 content_hash,
        消息的增删与压缩天然反映到比对结果中。

        Args:
            tool_name: 工具名,先按此过滤再比对哈希。
            dedup_key: 待比对的哈希值。

        Returns:
            bool: 存在相同的历史调用时为 True。
        """
        return any(
            isinstance(msg, ToolCallMessage)
            and msg.name == tool_name
            and msg.content_hash == dedup_key
            for msg in self._messages
        )

    def check_dedup(self, tool_name: str, args: dict, result: str | list) -> str | None:
        """检查只读工具结果是否与历史调用完全相同。重复时返回去重提示,否则返回 None。"""
        if (
            tool_name not in self._DEDUP_TOOLS
            or not isinstance(result, str)
            or len(result) <= self._DEDUP_MIN_CHARS
        ):
            return None
        try:
            args_key = json.dumps(args, sort_keys=True, ensure_ascii=False)
            dedup_key = hashlib.sha256(
                f"{tool_name}\x1f{args_key}\x1f{result}".encode(
                    "utf-8", errors="replace"
                )
            ).hexdigest()[:16]
        except (TypeError, ValueError):
            return None
        if self._find_duplicate_hash(tool_name, dedup_key):
            from uniclaw.utils.format import format_args_for_display

            args_short = format_args_for_display(args, max_length=200)
            return (
                f"[deduped] {tool_name}({args_short}) "
                f"的结果与之前调用完全相同,已省略。"
                f"上次结果开头: {result[:100]}"
            )
        return None

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "Session":
        start_time = data.get("start_time")
        if isinstance(start_time, str):
            try:
                start_time = datetime.fromisoformat(start_time)
            except ValueError:
                start_time = datetime.now()
        elif not isinstance(start_time, datetime):
            start_time = datetime.now()
        session = cls(
            id=data.get("session_id", ""),
            root_dir=(
                None
                if data.get("root_dir") in (None, "None")
                else Path(data["root_dir"])
            ),
            title=data.get("title", ""),
            start_time=start_time,
            session_type=SessionType(data.get("session_type", "console")),
            system_prompt=data.get("system_prompt"),
        )
        # 加载 _messages
        messages_data = data.get("messages", [])
        for message in messages_data:
            role = message.get("role")
            ca = BaseMessage._parse_created_at(message)
            if role == MessageRole.USER:
                session.add_user_message(content=message.get("content", ""), created_at=ca)
            elif role == MessageRole.ASSISTANT:
                session.add_assistant_message(
                    content=message.get("content", ""),
                    model_name=message.get("model_name", ""),
                    usage=message.get("usage", {}),
                    reasoning_content=message.get("reasoning_content"),
                    tool_calls=message.get("tool_calls"),
                    created_at=ca,
                )
            elif role == MessageRole.TOOL:
                session.add_tool_call_message(
                    content=message.get("content", ""),
                    tool_call={
                        "name": message.get("name", ""),
                        "tool_call_id": message.get("tool_call_id", ""),
                        "args": message.get("args", {}),
                        "explain": message.get("explain", ""),
                    },
                    created_at=ca,
                )

        version = data.get("version", 1)
        if version >= 2:
            # 新格式: compacted + messages 重建 history
            compact_count = data.get("compact_end", data.get("compact_count", 0))
            compacted_data = data.get("compacted", [])
            compacted_msgs = []
            for message in compacted_data:
                role = message.get("role")
                if role == MessageRole.USER:
                    compacted_msgs.append(UserMessage.from_dict(message))
                elif role == MessageRole.ASSISTANT:
                    compacted_msgs.append(AIMessage.from_dict(message))
                elif role == MessageRole.TOOL:
                    compacted_msgs.append(ToolCallMessage.from_dict(message))
            # history = compacted + 最近消息(跳过 _messages 开头的摘要)
            recent_msgs = session._messages[compact_count:]
            session.history = compacted_msgs + recent_msgs
            session._compact_end = compact_count
        else:
            # 旧格式: history 字段直接恢复
            history_data = data.get("history")
            if history_data is not None:
                session.history.clear()
                for message in history_data:
                    role = message.get("role")
                    if role == MessageRole.USER:
                        session.history.append(UserMessage.from_dict(message))
                    elif role == MessageRole.ASSISTANT:
                        session.history.append(AIMessage.from_dict(message))
                    elif role == MessageRole.TOOL:
                        session.history.append(ToolCallMessage.from_dict(message))
                # history 和 messages 长度相同 → 未压缩,否则 → 已压缩
                session._compact_end = (
                    0 if len(history_data) == len(messages_data) else 2
                )
        # 加载会话笔记
        for note_data in data.get("session_notes", []):
            session.session_notes.append(SessionNote.from_dict(note_data))
        return session

    def to_openai_messages(self) -> list[dict[str, str | list[dict[str, Any]]]]:
        messages = []
        for message in self._messages:
            messages.append(message.to_openai_message())
        return messages

    def to_messages(self) -> list[dict[str, Any]]:
        """返回 _messages 的消息列表(可能被 compact 压缩过)。"""
        return [message.to_dict() for message in self._messages]

    def to_history_messages(self) -> list[dict[str, Any]]:
        """返回 history 的消息列表(完整历史,不受 compact 影响)。"""
        return [message.to_dict() for message in self.history]

    def to_anthropic_messages(self) -> list[dict[str, str | list[dict[str, Any]]]]:
        messages = []
        for message in self._messages:
            messages.append(message.to_anthropic_message())
        return messages

    def get_recent_text(self, max_chars: int = 8000) -> str:
        """提取最近的对话文本(从后往前截取),用于 judge 评估等场景。"""
        parts: list[str] = []
        total = 0
        for message in reversed(self._messages):
            text = message.to_str()
            if not text:
                continue
            if total + len(text) > max_chars:
                # 截取剩余空间
                remaining = max_chars - total
                if remaining > 0:
                    parts.append(text[-remaining:])
                break
            parts.append(text)
            total += len(text)
        parts.reverse()
        return "\n\n".join(parts)

    async def to_dict(self, config: AppConfig) -> dict | None:
        if len(self._messages) > 0 and (self.title is None or not self.title.strip()):
            self.title = await self.generate_title(config=config)
        now = datetime.now()
        duration = max(0, int((now - self.start_time).total_seconds()))
        total_input_tokens = sum(
            [
                message.usage.input_tokens
                for message in self._messages
                if isinstance(message, AIMessage)
            ]
        )
        total_output_tokens = sum(
            [
                message.usage.output_tokens
                for message in self._messages
                if isinstance(message, AIMessage)
            ]
        )
        api_calls = sum(
            1 for message in self._messages if isinstance(message, AIMessage)
        )
        root_dir = str(self.root_dir) if self.root_dir else None
        # 计算 history 中的旧消息数(压缩前的部分)
        recent_count = len(self._messages) - self._compact_end
        old_count = max(0, len(self.history) - recent_count)
        data = {
            "session_id": self.id,
            "title": self.title,
            "root_dir": root_dir,
            "session_type": self.session_type,
            "system_prompt": self.system_prompt,
            "start_time": self.start_time.isoformat(),
            "end_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_seconds": duration,
            "message_count": len(self._messages),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "api_calls": api_calls,
            "version": 2,
            "compact_end": self._compact_end,
            "compacted": [m.to_dict() for m in self.history[:old_count]],
            "messages": self.to_messages(),
            "session_notes": [n.to_dict() for n in self.session_notes],
        }
        return data

    def to_str(self, include_tools: bool = False) -> str:
        parts = []
        for message in self._messages:
            if not include_tools and isinstance(message, ToolCallMessage):
                continue
            s = message.to_str()
            if s:
                parts.append(s)
        return "\n".join(parts)

    def add_user_message(self, content: str | list[dict, Any], created_at: datetime | None = _UNSET) -> None:
        if isinstance(content, list) and content and isinstance(content[0], dict):
            content = [MultimodalBlock.from_dict(block) for block in content]
        user_message = UserMessage(content=content, created_at=created_at if created_at is not _UNSET else datetime.now())
        self._messages.append(user_message)
        self.history.append(user_message)

    @staticmethod
    def _sanitize_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """修复畸形 tool_call arguments JSON,避免后续 API 调用 400 错误。"""
        for tc in tool_calls:
            fn = tc.get("function")
            if not fn:
                continue
            raw = fn.get("arguments", "")
            if isinstance(raw, str) and raw:
                try:
                    json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    import logging

                    logging.getLogger("session").warning(
                        "畸形 tool_call arguments,已替换为空 JSON: " "tool=%s, args=%s",
                        fn.get("name"),
                        raw[:200],
                    )
                    fn["arguments"] = "{}"
        return tool_calls

    def add_assistant_message(
        self,
        content: SupportedContent,
        model_name: str,
        usage: dict[str, Any],
        reasoning_content: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        created_at: datetime | None = _UNSET,
    ) -> None:
        if tool_calls:
            tool_calls = self._sanitize_tool_calls(tool_calls)
        assistant_message = AIMessage(
            content=content,
            model_name=model_name,
            usage=Usage.from_dict(usage),
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
            created_at=created_at if created_at is not _UNSET else datetime.now(),
        )
        self._messages.append(assistant_message)
        self.history.append(assistant_message)

    def add_tool_call_message(
        self,
        content: SupportedContent,
        tool_call: dict[str, Any],
        created_at: datetime | None = _UNSET,
    ) -> None:
        tool_call_message = ToolCallMessage(
            name=tool_call.get("name", ""),
            tool_call_id=tool_call.get("tool_call_id", ""),
            content=content,
            args=tool_call.get("args", {}),
            explain=tool_call.get("explain", ""),
            created_at=created_at if created_at is not _UNSET else datetime.now(),
        )
        self._messages.append(tool_call_message)
        self.history.append(tool_call_message)

    async def generate_title(self, config: AppConfig) -> str:
        prompt = self.to_str()
        system_prompt = (
            "你为对话生成标题。只输出一个简洁标题,不要解释,不要引号,10个中文字符以内。"
        )
        title_session = Session()
        title_session.add_user_message(content=prompt)

        try:
            from uniclaw.provider.fallback import achat

            resp = await achat(
                system_prompt,
                title_session,
                model_name=config.mini_model_name,
                enable_thinking=False,
                thinking=False,
                config=config,
                temperature=0.3,
            )
            title = resp.content.strip()
        except Exception as e:
            from uniclaw.console.ui import warn
            await warn(f"LLM 生成标题失败,使用回退方案: {e}", config)
            title = self._fallback_title()

        return title

    def _fallback_title(self) -> str:
        for message in self._messages:
            if isinstance(message, UserMessage):
                content = message.to_content()
                return content[:20]
        return ""

    # ── 消息管理兼容方法 ─────────────────────────────────────

    def add_message(
        self, role: MessageRole, content: str | list[dict[str, Any]], **kwargs
    ) -> None:
        """添加消息,内部转为结构化对象。"""
        ca = kwargs.get("created_at", _UNSET)
        if role == MessageRole.USER:
            self.add_user_message(content=content, created_at=ca)
        elif role == MessageRole.ASSISTANT:
            self.add_assistant_message(
                content=content,
                model_name=kwargs.get("model_name", ""),
                usage=kwargs.get("usage", {}),
                reasoning_content=kwargs.get("reasoning_content"),
                tool_calls=kwargs.get("tool_calls"),
                created_at=ca,
            )
        elif role == MessageRole.TOOL:
            self.add_tool_call_message(
                content=content,
                tool_call={
                    "name": kwargs.get("name", ""),
                    "tool_call_id": kwargs.get("tool_call_id", ""),
                    "args": kwargs.get("args", {}),
                    "explain": kwargs.get("explain", ""),
                },
                created_at=ca,
            )
        else:
            raise ValueError(f"不支持的消息角色: {role}")

    def clear(self) -> None:
        """清空所有消息。"""
        self._messages.clear()
        self.history.clear()

    def delete_messages(self, count: int, source: str = "messages") -> int:
        """从末尾删除指定数量的消息。返回实际删除数量。

        Args:
            count: 要删除的消息数量
            source: 消息来源
                - "messages": 从 _messages 末尾删除,同步 history
                - "history": 从 history 末尾删除,同步 _messages

        Returns:
            实际删除的消息数量
        """
        if count <= 0:
            return 0
        if source == "history":
            return self._delete_tail_from_history(count)
        return self._delete_tail_from_messages(count)

    def _delete_tail_from_messages(self, count: int) -> int:
        """从 _messages 末尾删除,同步 history。"""
        count = min(count, len(self._messages))
        if count <= 0:
            return 0
        del self._messages[len(self._messages) - count :]
        # _messages 尾部与 history 尾部对齐,按数量从 history 末尾截掉
        if len(self.history) >= count:
            del self.history[len(self.history) - count :]
        else:
            self.history.clear()
        return count

    def _delete_tail_from_history(self, count: int) -> int:
        """从 history 末尾删除,同步 _messages。"""
        count = min(count, len(self.history))
        if count <= 0:
            return 0
        del self.history[len(self.history) - count :]
        # _messages 尾部与 history 尾部对齐,按数量从 _messages 末尾截掉
        keep = len(self._messages) - count
        if keep < 2:
            # 剩余不足 2 条(compaction 摘要对),无法正常运行,
            # 直接用 history 替换 _messages
            self._messages.clear()
            self._messages.extend(self.history)
        else:
            del self._messages[keep:]
        return count

    def replace_messages(self, messages: list[dict[str, Any]]) -> None:
        """用原始 dict 列表整体替换消息。"""
        self._messages.clear()
        self.history.clear()
        for msg in messages:
            role = msg.get("role", "")
            if role == MessageRole.USER:
                self.add_user_message(content=msg.get("content", ""))
            elif role == MessageRole.ASSISTANT:
                self.add_assistant_message(
                    content=msg.get("content", ""),
                    model_name=msg.get("model_name", ""),
                    usage=msg.get("usage", {}),
                    reasoning_content=msg.get("reasoning_content"),
                    tool_calls=msg.get("tool_calls"),
                )
            elif role == MessageRole.TOOL:
                self.add_tool_call_message(
                    content=msg.get("content", ""),
                    tool_call={
                        "name": msg.get("name", ""),
                        "tool_call_id": msg.get("tool_call_id", ""),
                        "args": msg.get("args", {}),
                    },
                )
            else:
                raise ValueError(f"不支持的消息角色: {role}")

    def __len__(self) -> int:
        return len(self._messages)

    def __iter__(self):
        return iter(self._messages)

    def __reversed__(self):
        return reversed(self._messages)

    def __getitem__(self, key):
        return self._messages[key]

    # ── Token 估算与压缩 ────────────────────────────────────

    def estimate_tokens(self, model: str | None = None) -> int:
        """估算当前消息的 token 数量。"""
        if not self._messages:
            return 0
        return sum(m.estimate_tokens(model) for m in self._messages)

    async def compact(
        self, config: AppConfig, focus: str = "", keep_ratio: float = 0.3
    ) -> None:
        """通过 LLM 将旧消息压缩为结构化续作摘要。"""
        split = self._find_split_point(keep_ratio=keep_ratio)
        if split <= 0:
            return

        old = self._messages[:split]
        recent = self._messages[split:]

        # 有界转录 + 自适应摘要预算(按被替换的 token 量分配)
        transcript = _build_summary_transcript(old)
        replaced_tokens = sum(m.estimate_tokens() for m in old)
        budget = min(
            _SUMMARY_MAX_TOKENS,
            max(_SUMMARY_MIN_TOKENS, replaced_tokens // 20),
        )

        summary_prompt = CHECKPOINT_TEMPLATE.replace(
            "__FOCUS__", focus if focus else "无"
        ).replace("__BUDGET__", str(budget))
        summary_prompt += "\n\n对话记录:\n" + transcript

        wait_id = config.spinner.start("压缩对话...")
        try:
            from uniclaw.provider.fallback import achat

            compact_session = Session()
            compact_session.add_user_message(content=summary_prompt)
            resp = await achat(
                "你是一个对话压缩器。你的任务是把一段即将离开上下文的对话压缩成"
                "结构化续作摘要,让一个没见过原对话的新 agent 只凭这份摘要就能无缝"
                "继续工作。关键信息(文件路径、URL、端口号、变量名、命令、"
                "API/工具名称、报错关键行)必须一字不改地保留。",
                compact_session,
                model_name=config.model_name,
                config=config,
                max_tokens=budget,
                temperature=0.2,
            )
        except Exception as e:
            from uniclaw.console.ui import warn
            await warn(f"对话压缩失败,保留原消息: {e}", config)
            return
        finally:
            config.spinner.stop(wait_id=wait_id)

        if not resp.content or not resp.content.strip():
            from uniclaw.console.ui import warn
            await warn("对话压缩返回空摘要,保留原消息", config)
            return

        self._messages.clear()
        # 历史检索提示追加到摘要消息末尾,与压缩数据同生共死:
        # system prompt 在 run 开始时一次性构建,而压缩可能在运行中途后台触发,
        # 若提示只放在 system prompt 中,压缩发生后 LLM 便无从得知可用
        # recall_history 检索归档消息,且提示中的归档数量也会过期。
        # 归档数 = 完整历史 - 保留的最近消息,用本次压缩的局部变量即可算出。
        from uniclaw.tools.session.recall import get_recall_hint

        archived_count = len(self.history) - len(recent)
        recall_hint = get_recall_hint(archived_count, len(self.history))
        summary_content = f"{SUMMARY_PREFIX}\n{resp.content}"
        if recall_hint:
            summary_content += f"\n\n{recall_hint}"

        # 注入会话笔记快照 — 标注为旧快照,LLM 需要详情时应调用笔记工具
        if self.session_notes:
            from uniclaw.tools.session.notes import session_note_list

            notes_section = "\n\n## 会话笔记(上轮对话快照,可能已过期,仅供参考)\n"
            for note in self.session_notes:
                notes_section += f"- [{note.name}]: {note.description}\n"
            notes_section += f"使用 {session_note_list.name} 查看全部笔记。\n"
            summary_content += notes_section

        # 直接操作 _messages,不走 add_* 以避免污染 history
        self._messages.append(UserMessage(content=summary_content))
        self._messages.append(
            AIMessage(
                content="已阅读之前的对话摘要,继续当前任务。",
                model_name="",
                usage=Usage.from_dict({}),
            )
        )
        self._messages.extend(recent)
        self._compact_end = 2
        self._compact_warned_levels.clear()  # 重置预警状态,下个压缩周期可再次预警

    def _find_split_point(self, keep_ratio: float = 0.3) -> int:
        """查找分割点使最近部分约占总 token 的 keep_ratio。"""
        if not self._messages:
            return 0
        keep_ratio = max(0.0, min(1.0, keep_ratio))
        total = self.estimate_tokens()
        target = int(total * keep_ratio)
        running = 0
        for i in range(len(self._messages) - 1, -1, -1):
            running += self._messages[i].estimate_tokens()
            if running >= target:
                return i
        return 0

    def snip_old_tool_results(
        self, max_chars: int = 2000, preserve_last_n_turns: int = 6
    ) -> None:
        """压缩旧工具结果:可再生工具清空,不可再生工具截断。"""
        cutoff = max(0, len(self._messages) - preserve_last_n_turns)
        for i in range(cutoff):
            msg = self._messages[i]
            if not isinstance(msg, ToolCallMessage):
                continue
            content = msg.content if isinstance(msg.content, str) else ""
            if not content or len(content) <= 200:
                continue
            if msg.name in self.COMPACTABLE_TOOLS:
                # 可再生工具:清空结果,保留工具名和参数信息
                msg.content = f"[{msg.name} 结果已清除,可重新执行获取]"
            elif len(content) > max_chars:
                # 不可再生工具:截断(保留头尾)
                half = max_chars // 2
                quarter = max_chars // 4
                snipped = len(content) - half - quarter
                msg.content = f"{content[:half]}\n[... {snipped} 个字符已省略 ...]\n{content[-quarter:]}"

    async def maybe_compact(self, config: AppConfig) -> bool:
        """根据上下文长度阈值判断是否需要执行消息压缩。

        三级压缩策略:
        - level 0 (50%): 仅微压缩(清空旧工具结果)
        - level 1 (70%): Jev 智能压缩(优先),失败则回退 LLM 摘要
        - level 2 (85%): 更激进的 Jev/LLM 压缩

        压缩前预警:每跨过一个压力阈值,在其 *_COMPACT_WARN_FACTOR 比例处通过
        wake_agent 注入一次"写遗言"提示,让 LLM 在压缩发生前把关键信息存入会话笔记。
        """
        from uniclaw.compaction import (
            PRESSURE_LEVELS,
            get_context_limit,
            get_pressure_level,
        )

        model = config.model_name[0] if config.model_name else "unknown"
        limit = await get_context_limit(model)
        current_tokens = self.estimate_tokens(model)
        ratio = current_tokens / limit if limit > 0 else 0

        # 压缩前预警 — 每个压力等级只预警一次:
        # 跨过 min(该等级阈值, 上一等级阈值)*_COMPACT_WARN_FACTOR 时注入提示,
        # 例如 70% 档在 63% 预警、85% 档在 76.5% 预警。
        # level 0(50%)仅微压缩(清空可再生工具结果,可重新获取),信息无损,不预警。
        # 预警等级下限: WARN_LEVEL_MIN 之下的等级只做 snip,不触发 LLM 摘要。
        for threshold, level in PRESSURE_LEVELS:
            if level < _WARN_LEVEL_MIN:
                continue
            upper = next(
                (t for t, lv in PRESSURE_LEVELS if lv == level + 1), 1.0
            )
            warn_at = min(threshold, upper) * _COMPACT_WARN_FACTOR
            if ratio >= warn_at and level not in self._compact_warned_levels:
                self._compact_warned_levels.add(level)
                await self._notify_compact_warning(config, threshold)

        level = await get_pressure_level(current_tokens, model)

        if level < 0:
            return False

        # level 0+: 微压缩 — 清空可再生工具结果
        self.snip_old_tool_results()
        if self.estimate_tokens(model) <= limit * PRESSURE_LEVELS[-1][0]:
            return True

        # level 1+: 尝试 Jev 智能压缩,失败则回退 LLM 摘要
        from uniclaw.jev_compact import JevCompactConfig, JevCompactSkip, jev_compact

        jev_config = JevCompactConfig(
            keep_call_threshold=0.5,
            keep_result_threshold=0.7,
        )
        try:
            jev_result = await jev_compact(self, jev_config, keep_ratio=0.3)
            jev_done = True
            from uniclaw.console.ui import info
            await info(
                f"Jev 压缩: {jev_result.total_pairs} 配对, "
                f"保留 {jev_result.kept}, 修改/删除 {jev_result.modified}",
                config,
            )
        except (JevCompactSkip, Exception) as e:
            from uniclaw.console.ui import warn
            await warn(f"Jev 压缩跳过: {e}", config)
            jev_done = False

        if not jev_done:
            # Jev 失败,回退 LLM 摘要
            await self.compact(config, keep_ratio=0.3)

        if self.estimate_tokens(model) <= limit * PRESSURE_LEVELS[1][0]:
            return True

        # level 2: LLM 摘要(确定性压缩,保证释放空间)
        await self.compact(config, keep_ratio=0.15)

        return True

    async def _notify_compact_warning(self, config: AppConfig, threshold: float) -> None:
        """通过 wake_agent 注入压缩前"写遗言"提示。

        压缩可能由后台任务触发,当前 agent 可能不在运行,因此走统一唤醒通道:
        运行中 → user_queue 注入;空闲 → 重新拉起。
        """
        from uniclaw.utils.constants import SYSTEM_PREFIX
        from uniclaw.tools.session.notes import session_note_add

        message = (
            f"{SYSTEM_PREFIX}[压缩预警] 上下文用量即将达到 {threshold:.0%} 压缩阈值,"
            "超过该阈值的早期消息将被压缩为摘要,摘要可能丢失细节。"
            "如有压缩后仍需保留的关键信息(配置值、决策结论、路径、密钥格式等),"
            f"请立即调用 {session_note_add.name} 保存到会话笔记。"
        )

        try:
            from uniclaw.utils.wakeup import wake_agent

            await wake_agent(message, config)
        except Exception as e:
            from uniclaw.console.ui import warn
            await warn(f"注入压缩预警失败: {e}", config)

    def build_context_summary(
        self,
        max_messages: int = 0,
        max_chars: int = 0,
        roles: tuple = (MessageRole.USER, MessageRole.ASSISTANT),
    ) -> str:
        """从对话消息中提取最近消息作为上下文摘要。"""
        role_map = {
            UserMessage: MessageRole.USER,
            AIMessage: MessageRole.ASSISTANT,
        }
        filtered = [m for m in self._messages if role_map.get(type(m)) in roles]
        if max_messages > 0:
            filtered = filtered[-max_messages:]
        text = "\n".join([message.to_str() for message in filtered])
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars] + "..."
        return text

    def get_assistant_messages(self, separator: str | None = "\n") -> str | list[str]:
        """提取所有助手消息内容。

        Args:
            separator: 拼接分隔符。None 返回 list,否则用分隔符拼接。
        """
        parts = [
            message.to_content()
            for message in self._messages
            if isinstance(message, AIMessage) and message.content
        ]
        if separator is None:
            return parts
        return separator.join(parts)
