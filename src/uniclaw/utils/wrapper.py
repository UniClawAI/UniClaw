import asyncio
import functools
import time
import traceback
from pathlib import Path

from uniclaw.utils.logger import get_logger


def _extract_task(args, kwargs):
    """从 args/kwargs 中提取 task 对象。"""
    task = kwargs.get("task")
    if not task:
        config = kwargs.get("config")
        if not config:
            for arg in args:
                if hasattr(arg, "current_agent"):
                    config = arg
                    break
        if config and hasattr(config, "current_agent"):
            task = config.current_agent
    return task


def _log_error(name, fun, task, e):
    """记录异常到 logger。"""
    from uniclaw.utils.logger import get_logger

    if not task:
        raise RuntimeError(
            f"error_catch({name}): 缺少 task/config 参数,无法获取 session.root_dir"
        )
    logger = get_logger(name, task.session.root_dir)
    err = str(e) if str(e) else str(type(e))
    detailed = f"{fun.__name__}\n{traceback.format_exc()}"
    logger.error(f"{err}\n\n{detailed}")


def error_catch(name: str):
    """返回一个异常捕获装饰器,兼容同步和异步函数。

    该装饰器将包装目标函数,在函数执行过程中捕获任何异常,
    并将异常信息连同完整堆栈追踪写入指定 logger。
    捕获后会重新抛出异常,保证调用方仍能看到原始错误。

    Args:
        name: logger 名称,会从函数 kwargs 中的 task.session.root_dir 获取日志目录。

    Returns:
        wrapper: 用于装饰目标函数的包装器。
    """

    def wrapper(fun):
        @functools.wraps(fun)
        def inner(*args, **kwargs):
            try:
                ret = fun(*args, **kwargs)
            except Exception as e:
                task = _extract_task(args, kwargs)
                _log_error(name, fun, task, e)
                raise
            if asyncio.iscoroutine(ret):
                return _await_with_error_catch(ret, name, fun, args, kwargs)
            return ret

        inner.__name__ = fun.__name__

        return inner

    return wrapper


async def _await_with_error_catch(coro, name, fun, args, kwargs):
    """await 一个协程,捕获异常并记录日志。"""
    try:
        return await coro
    except Exception as e:
        task = _extract_task(args, kwargs)
        _log_error(name, fun, task, e)
        # 通知前端(WebUI/console/wechat)
        try:
            from uniclaw.console.ui import err

            config = kwargs.get("config")
            if not config:
                for arg in args:
                    if hasattr(arg, "current_agent"):
                        config = arg
                        break
            err_msg = str(e) if str(e) else type(e).__name__
            await err(f"{fun.__name__} 失败: {err_msg}", config=config)
        except Exception as e:
            get_logger(
                "wrapper", task.session.root_dir if task else Path.cwd()
            ).warning("前端错误通知发送失败: %s", e)
        raise
