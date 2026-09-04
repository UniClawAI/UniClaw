"""TTS 工具 — 文本转语音。"""

from pathlib import Path

from uniclaw.tools.base import tool, ToolRuntime


@tool
async def text_to_speech(
    text: str,
    style: str = "",
    save_path: str = "",
    play: bool = False,
    tool_runtime: ToolRuntime = None,
) -> str:
    """将文本转换为语音。

    Args:
        text: 要转换的文本内容。支持在文本中嵌入风格标签与音频标签进行精细控制。
               风格标签:在文本开头添加括号 (风格) 标签,如"(开心)你好世界""(唱歌)歌词"。
               支持基础情绪(开心/悲伤/愤怒)、复合情绪(怅然/欣慰/无奈)、整体语调(温柔/高冷/活泼)、
               音色定位(磁性/醇厚/清亮)、人设腔调(夹子音/御姐音)、方言(东北话/四川话/粤语)、角色扮演等。
               音频标签:在文本任意位置插入 [音频标签],如[笑声][呼吸][停顿],实现细粒度控制。
        style: 说话风格控制,支持自然语言描述,如"开心""悲伤""温柔""激动"等。
               支持复杂控制:多风格切换("播报→低语→嘶吼")、多情绪混合("压抑的愤怒""带着哽咽的笑意")、
               多粒度控制(段落级基调→句子级节奏→词级重音→字粒度哽咽拖音)。
               不填则使用默认风格。
        save_path: 保存路径(如 output.wav),不填则不保存文件。
        play: 是否播放语音,默认 false。

    Returns:
        操作结果描述。
    """
    from uniclaw.tools.tts.tts import tts

    return await tts(
        text=text,
        style=style,
        save_path=save_path,
        play=play,
        config=tool_runtime.config,
    )


def get_tools(config):
    """获取当前可用的 TTS 工具。

    仅当配置了 tts_model 和 audio 时才返回工具。
    """
    if not config or not config.tts_model or not config.audio:
        if config:
            config.record_unavailable_tools(
                [text_to_speech.name],
                "未配置 tts_model 或 audio,TTS 工具不可用",
            )
        return []
    config.clear_unavailable_tools([text_to_speech.name])
    return [text_to_speech]


def get_all_tools():
    """获取所有 TTS 工具。"""
    return [text_to_speech]
