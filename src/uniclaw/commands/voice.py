"""语音模式切换命令。"""

from uniclaw.config import AppConfig, RunMode
from uniclaw.console.ui import info, ok, err


async def cmd_voice(args: str, config: AppConfig) -> bool:
    """切换语音模式。/voice [on|off]"""

    if config.run_mode == RunMode.WECHAT:
        await err("微信模式不支持语音模式切换", config)
        return True
    if not config.tts_model or not config.audio:
        await err("TTS 未配置(tts_model 或 audio 为空),无法切换语音模式", config)
        return True

    args = args.strip().lower()
    if args in ("on", "1", "true"):
        config.voice_mode = True
    elif args in ("off", "0", "false"):
        config.voice_mode = False
    elif args == "":
        config.voice_mode = not config.voice_mode
    else:
        await info("用法: /voice [on|off]", config)
        return True

    mode = "开启" if config.voice_mode else "关闭"
    await ok(f"语音模式已{mode}", config)

    # 关闭语音模式: 清理 TTS 队列
    if not config.voice_mode:
        from uniclaw.tools.tts.tts import tts_cleanup

        await tts_cleanup(config.current_agent.session.id)

    # WebUI 模式: 通知前端刷新配置
    if config.run_mode == RunMode.WEBUI:
        from uniclaw.webui.ws import _notify_config_changed

        session_id = config.current_agent.session.id
        await _notify_config_changed(session_id)
    return True
