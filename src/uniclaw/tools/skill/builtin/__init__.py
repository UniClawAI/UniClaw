"""内置技能注册模块

每个内置技能定义在单独的文件中,通过 register() 函数注册。
"""

from . import code_review, commit, pr_create, memory_organize, skill_forge, theme


def register_all():
    """注册所有内置技能"""
    code_review.register()
    commit.register()
    pr_create.register()
    memory_organize.register()
    skill_forge.register()
    theme.register()


# 模块加载时自动注册
register_all()
