"""内置 Skill: 主题定制"""

from pathlib import Path
from uniclaw.tools.skill.loader import SkillDef, register_builtin

_THEMES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "webui" / "static" / "themes"
_THEMES_DIR_STR = str(_THEMES_DIR).replace("\\", "/")

_THEME_PROMPT = f"""## UniClaw 主题定制指南

帮助用户创建、修改或模仿定制 UniClaw WebUI 主题。

### 主题系统概述

UniClaw 使用基于 CSS 变量的主题系统。每个主题是一个文件夹,位于主题目录下。

**主题目录: `{_THEMES_DIR_STR}`**

**目录结构:**
```
{_THEMES_DIR_STR}/
├── index.json              # 主题注册表(必须注册才能被发现)
├── fluent-dark/            # 主题文件夹
│   ├── meta.json           # 主题元数据
│   └── variables.css       # CSS 变量覆盖(可多个 CSS 文件)
├── fluent-light/
│   ├── meta.json
│   └── variables.css
└── cyberpunk/
    ├── meta.json
    └── variables.css
```

### 创建新主题的步骤

#### 1. 创建主题文件夹

在 `{_THEMES_DIR_STR}` 下新建文件夹,用小写英文命名(如 `nord-dark`)。

#### 2. 编写 meta.json

```json
{{
  "name": "Nord Dark",
  "description": "基于 Nord 配色方案的暗色主题",
  "author": "Your Name",
  "version": "1.0",
  "colors": ["#5e81ac", "#2e3440", "#3b4252", "#eceff4"],
  "css": ["variables.css"]
}}
```

字段说明:
- `name`: 主题显示名称
- `description`: 主题描述
- `author`: 作者名
- `version`: 版本号
- `colors`: 预览色板(4 个主色,用于主题选择器显示)
- `css`: CSS 文件列表(按顺序加载),可包含多个文件

#### 3. 编写 variables.css

只需要覆盖你要修改的变量。完整的变量列表:

**背景层级(7 个):**
```css
:root {{
    --bg-0: #0a0a0a;       /* 最深背景 */
    --bg-1: #141414;       /* 主表面 */
    --bg-2: #1f1f1f;       /* 卡片/面板 */
    --bg-3: #292929;       /* 悬浮元素 */
    --bg-4: #333333;       /* hover 状态 */
    --bg-inset: #0a0a0a;   /* 代码块/输入框 */
    --bg-elevated: #1f1f1f; /* 弹窗 */
}}
```

**品牌色(5 个):**
```css
    --accent: #0078d4;         /* 主强调色 */
    --accent-bright: #2899f5;  /* 高亮强调色 */
    --accent-dim: rgba(0, 120, 212, 0.12);   /* 淡强调色背景 */
    --accent-glow: rgba(0, 120, 212, 0.25);  /* 发光效果 */
    --accent-subtle: rgba(0, 120, 212, 0.06); /* 极淡强调色 */
```

**文字(7 个):**
```css
    --text-0: #ffffff;     /* 主标题 */
    --text-1: #d6d6d6;     /* 正文 */
    --text-2: #adadad;     /* 次要文字 */
    --text-3: #8a8a8a;     /* 占位符/提示 */
    --text-4: #616161;     /* 禁用状态 */
    --text-accent: #2899f5; /* 强调文字 */
    --text-link: #4db8ff;   /* 链接 */
```

**边框(4 个):**
```css
    --border: rgba(255, 255, 255, 0.08);
    --border-hover: rgba(255, 255, 255, 0.16);
    --border-focus: rgba(0, 120, 212, 0.5);
    --border-glow: rgba(0, 120, 212, 0.2);
```

**亚克力材质(4 个):**
```css
    --glass-bg: rgba(20, 20, 20, 0.85);
    --glass-bg-light: rgba(30, 30, 30, 0.9);
    --glass-border: rgba(255, 255, 255, 0.08);
    --glass-blur: 20px;
```

**阴影(6 个):**
```css
    --shadow-sm: 0 2px 4px rgba(0, 0, 0, 0.14);
    --shadow-md: 0 4px 16px rgba(0, 0, 0, 0.24);
    --shadow-lg: 0 8px 32px rgba(0, 0, 0, 0.32);
    --shadow-glow: 0 0 12px var(--accent-glow);
    --shadow-glow-sm: 0 0 6px rgba(0, 120, 212, 0.15);
    --shadow-neon-cyan: 0 0 12px var(--neon-cyan-glow);
```

**语义色(10 个):**
```css
    --neon-cyan: #2899f5;
    --neon-green: #07b364;
    --neon-pink: #d13438;
    --neon-orange: #f7630c;
    --neon-yellow: #fce100;
    --red: #d13438;
    --green: #07b364;
    --yellow: #fce100;
    --orange: #f7630c;
```
(每个语义色都有对应的 `-dim` 透明度版本)

**渐变(6 个):**
```css
    --gradient-accent: linear-gradient(135deg, #0078d4 0%, #2899f5 100%);
    --gradient-accent-hover: linear-gradient(135deg, #106ebe 0%, #3aa0f3 100%);
    --gradient-accent-dim: linear-gradient(135deg, rgba(0,120,212,0.12) 0%, rgba(40,153,245,0.12) 100%);
    --gradient-accent-subtle: linear-gradient(135deg, rgba(0,120,212,0.06) 0%, rgba(40,153,245,0.06) 100%);
    --gradient-danger: linear-gradient(135deg, #d13438 0%, #e74856 100%);
    --gradient-surface: linear-gradient(180deg, var(--bg-2) 0%, var(--bg-1) 100%);
```

**背景图片(5 个):**
```css
    --bg-image: none;                /* 图片 URL */
    --bg-image-size: cover;          /* 尺寸: cover/contain/具体值 */
    --bg-image-position: center;     /* 位置: center/top/具体坐标 */
    --bg-image-repeat: no-repeat;    /* 重复: no-repeat/repeat/repeat-x */
    --bg-image-opacity: 0.15;        /* 透明度: 0-1 */
```

**代码高亮(14 个):**
```css
    --hljs-keyword: #c586c0;         /* 关键字 */
    --hljs-string: #ce9178;          /* 字符串 */
    --hljs-number: #b5cea8;          /* 数字 */
    --hljs-comment: #6a9955;         /* 注释 */
    --hljs-function: #dcdcaa;        /* 函数名 */
    --hljs-type: #4ec9b0;            /* 类型 */
    --hljs-variable: #9cdcfe;        /* 变量 */
    --hljs-params: #d4d4d4;          /* 参数 */
    --hljs-meta: #569cd6;            /* 元数据/标签 */
    --hljs-tag: #569cd6;             /* HTML 标签 */
    --hljs-name: #569cd6;            /* 标签名 */
    --hljs-attribute: #9cdcfe;       /* 属性 */
    --hljs-addition: #07b364;        /* 新增(diff) */
    --hljs-deletion: #d13438;        /* 删除(diff) */
```

#### 4. 注册主题

编辑 `{_THEMES_DIR_STR}/index.json`,将主题 ID 添加到列表:

```json
{{
  "themes": ["fluent-dark", "fluent-light", "cyberpunk", "nord-dark"]
}}
```

### 模仿其他网站的配色

当用户想要模仿某个网站的风格时:

1. **提取主色调**:观察网站的背景色、文字色、强调色
2. **确定色阶**:通常需要 4-5 个灰度层级(从深到浅)
3. **提取强调色**:按钮、链接、高亮使用的颜色
4. **注意暗/亮模式**:确认是暗色还是亮色主题

常用配色方案参考:
- **GitHub Dark**: `#0d1117`, `#161b22`, `#21262d`, `#f0f6fc`
- **VS Code Dark**: `#1e1e1e`, `#252526`, `#2d2d2d`, `#d4d4d4`
- **Nord**: `#2e3440`, `#3b4252`, `#434c5e`, `#eceff4`
- **Dracula**: `#282a36`, `#44475a`, `#6272a4`, `#f8f8f2`
- **Solarized Dark**: `#002b36`, `#073642`, `#586e75`, `#fdf6e3`
- **One Dark**: `#282c34`, `#353b45`, `#3e4451`, `#abb2bf`
- **Catppuccin Mocha**: `#1e1e2e`, `#313244`, `#45475a`, `#cdd6f4`
- **Tokyo Night**: `#1a1b26`, `#24283b`, `#414868`, `#c0caf5`

### 多 CSS 文件主题

一个主题可以包含多个 CSS 文件,适合复杂主题:

```json
{{
  "name": "My Theme",
  "css": ["variables.css", "syntax.css", "components.css"]
}}
```

- `variables.css`: 设计令牌(必须)
- `syntax.css`: 代码高亮配配色(可选)
- `components.css`: 特定组件样式覆盖(可选)

### 背景图片

主题可以设置背景图片,在 `meta.json` 中添加 `background` 字段:

```json
{{
  "name": "Starry Night",
  "description": "星空主题",
  "author": "Your Name",
  "colors": ["#6366f1", "#0f172a", "#1e293b", "#e2e8f0"],
  "css": ["variables.css"],
  "background": "bg.jpg",
  "backgroundSize": "cover",
  "backgroundPosition": "center",
  "backgroundRepeat": "no-repeat",
  "backgroundOpacity": 0.15
}}
```

**背景图片配置字段:**

| 字段 | 说明 | 默认值 |
|---|---|---|
| `background` | 图片文件名(相对于主题文件夹) | 无 |
| `backgroundSize` | CSS `background-size` | `cover` |
| `backgroundPosition` | CSS `background-position` | `center` |
| `backgroundRepeat` | CSS `background-repeat` | `no-repeat` |
| `backgroundOpacity` | 图片透明度(0-1) | `0.15` |

**使用方式:**

1. 将图片文件(如 `bg.jpg`、`bg.png`、`bg.webp`)放入主题文件夹
2. 在 `meta.json` 中添加 `"background": "bg.jpg"`
3. 可选调整 `backgroundOpacity`(建议 0.1-0.3,过高会影响可读性)

**支持的图片来源:**

- 本地图片:将图片文件放入主题文件夹,使用相对路径
- 外部 URL:在 CSS 中直接使用 `url('https://...')` 覆盖变量

**最佳实践:**

- 透明度建议 0.1-0.3,过高会影响文字可读性
- 暗色主题适合深色/低饱和度图片(星空、暗纹、渐变)
- 亮色主题适合浅色/高亮度图片(白纹理、浅色图案)
- 推荐使用 `.jpg` 或 `.webp` 格式(文件较小)
- 图片分辨率建议 1920x1080 或更高(cover 模式会自动缩放)

**CSS 变量(可在 variables.css 中覆盖):**

```css
:root {{
    --bg-image: none;                    /* 图片 URL */
    --bg-image-size: cover;              /* 尺寸 */
    --bg-image-position: center;         /* 位置 */
    --bg-image-repeat: no-repeat;        /* 重复 */
    --bg-image-opacity: 0.15;            /* 透明度 */
}}
```

### 工作流程

当用户要求创建/修改主题时:

1. 确认用户想要的主题风格(暗色/亮色,模仿哪个网站等)
2. 提取或生成配色方案
3. 在 `{_THEMES_DIR_STR}` 下创建主题文件夹和文件
4. 注册到 index.json
5. 提示用户刷新页面并在右上角主题选择器中查看效果

### 注意事项

- 只需覆盖要修改的变量,不需要复制全部
- `colors` 数组用于主题选择器预览,取 4 个最有代表性的颜色
- CSS 变量名保持不变,只改值
- 亮色主题需要特别注意文字颜色(深色文字)和边框(半透明黑色)
"""


def register():
    """注册 uniclaw-theme 技能"""
    register_builtin(
        SkillDef(
            name="uniclaw-theme",
            description="创建、修改或模仿定制 UniClaw WebUI 主题。"
            "当用户要求换肤、创建主题、修改配色、模仿网站风格时使用。",
            triggers=["/uniclaw-theme", "/theme", "create theme", "new theme", "主题", "换肤", "配色"],
            tools=["Read", "Write", "Edit", "Glob", "Bash"],
            prompt=_THEME_PROMPT,
            file_path=__file__,
            source="builtin",
            when_to_use="用户要求创建主题、修改配色、换肤、模仿网站风格、定制皮肤时",
            argument_hint="[主题名或描述]",
        )
    )
