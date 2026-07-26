/* ============================================================
   Theme Manager — 主题切换系统
   支持文件夹式主题，第三方可自定义
   ============================================================ */

const Theme = {
    THEMES_DIR: '/static/themes',
    STORAGE_KEY: 'uniclaw-theme',
    DEFAULT_THEME: 'fluent-dark',

    _themes: {},       // { id: meta }
    _current: null,    // 当前主题 id
    _links: [],        // 当前加载的 <link> 元素
    _dropdown: null,

    // ============================================================
    //  初始化
    // ============================================================
    async init() {
        await this._discover();
        const saved = localStorage.getItem(this.STORAGE_KEY) || this.DEFAULT_THEME;
        await this.set(saved);
        this._bindButton();
    },

    // ============================================================
    //  发现主题：扫描 themes/ 目录
    // ============================================================
    async _discover() {
        try {
            // 获取主题目录列表
            const resp = await fetch(`${this.THEMES_DIR}/index.json`);
            if (resp.ok) {
                const data = await resp.json();
                for (const id of data.themes) {
                    await this._loadMeta(id);
                }
                return;
            }
        } catch {}

        // fallback: 尝试已知主题列表
        const known = ['fluent-dark', 'fluent-light', 'cyberpunk'];
        for (const id of known) {
            await this._loadMeta(id);
        }
    },

    async _loadMeta(id) {
        try {
            const resp = await fetch(`${this.THEMES_DIR}/${id}/meta.json`);
            if (resp.ok) {
                this._themes[id] = await resp.json();
                this._themes[id]._id = id;
            }
        } catch {}
    },

    // ============================================================
    //  获取主题列表
    // ============================================================
    getThemes() {
        return Object.values(this._themes);
    },

    getCurrent() {
        return this._current;
    },

    // ============================================================
    //  设置主题
    // ============================================================
    async set(id) {
        if (!this._themes[id]) {
            console.warn(`Theme "${id}" not found`);
            return;
        }

        // 移除旧的主题 CSS
        for (const link of this._links) {
            link.remove();
        }
        this._links = [];

        // 加载新的主题 CSS
        const meta = this._themes[id];
        for (const css of (meta.css || [])) {
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = `${this.THEMES_DIR}/${id}/${css}`;
            link.dataset.theme = id;
            document.head.appendChild(link);
            this._links.push(link);
        }

        // 处理背景图片
        this._applyBackground(id, meta);

        this._current = id;
        localStorage.setItem(this.STORAGE_KEY, id);

        // 更新下拉菜单选中状态
        this._updateDropdown();
    },

    // ============================================================
    //  应用背景图片
    // ============================================================
    _applyBackground(id, meta) {
        const root = document.documentElement;

        if (meta.background) {
            // 背景图片路径(相对于主题文件夹)
            const imgUrl = `${this.THEMES_DIR}/${id}/${meta.background}`;
            root.style.setProperty('--bg-image', `url('${imgUrl}')`);

            // 可选的背景图片配置
            if (meta.backgroundSize) {
                root.style.setProperty('--bg-image-size', meta.backgroundSize);
            }
            if (meta.backgroundPosition) {
                root.style.setProperty('--bg-image-position', meta.backgroundPosition);
            }
            if (meta.backgroundRepeat) {
                root.style.setProperty('--bg-image-repeat', meta.backgroundRepeat);
            }
            if (meta.backgroundOpacity !== undefined) {
                root.style.setProperty('--bg-image-opacity', meta.backgroundOpacity);
            }
        } else {
            // 无背景图片,清除
            root.style.removeProperty('--bg-image');
            root.style.removeProperty('--bg-image-size');
            root.style.removeProperty('--bg-image-position');
            root.style.removeProperty('--bg-image-repeat');
            root.style.removeProperty('--bg-image-opacity');
        }
    },

    // ============================================================
    //  绑定按钮
    // ============================================================
    _bindButton() {
        const btn = document.getElementById('theme-btn');
        if (!btn) return;

        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            this._toggleDropdown();
        });

        // 点击外部关闭
        document.addEventListener('click', () => {
            this._closeDropdown();
        });
    },

    // ============================================================
    //  下拉菜单
    // ============================================================
    _toggleDropdown() {
        if (this._dropdown) {
            this._closeDropdown();
            return;
        }

        const btn = document.getElementById('theme-btn');
        const rect = btn.getBoundingClientRect();

        const dropdown = document.createElement('div');
        dropdown.className = 'theme-dropdown';
        dropdown.style.top = (rect.bottom + 4) + 'px';
        dropdown.style.right = (window.innerWidth - rect.right) + 'px';

        // 标题
        const title = document.createElement('div');
        title.className = 'theme-dropdown-title';
        title.textContent = '主题风格';
        dropdown.appendChild(title);

        // 主题列表
        for (const theme of this.getThemes()) {
            const item = document.createElement('div');
            item.className = 'theme-dropdown-item';
            if (theme._id === this._current) {
                item.classList.add('active');
            }

            // 色板
            const swatch = document.createElement('div');
            swatch.className = 'theme-swatch';
            for (const color of (theme.colors || []).slice(0, 4)) {
                const dot = document.createElement('span');
                dot.className = 'theme-swatch-dot';
                dot.style.background = color;
                swatch.appendChild(dot);
            }

            // 信息
            const info = document.createElement('div');
            info.className = 'theme-info';

            const name = document.createElement('div');
            name.className = 'theme-name';
            name.textContent = theme.name;

            const desc = document.createElement('div');
            desc.className = 'theme-desc';
            desc.textContent = theme.description || '';

            info.appendChild(name);
            info.appendChild(desc);

            item.appendChild(swatch);
            item.appendChild(info);

            item.addEventListener('click', (e) => {
                e.stopPropagation();
                this.set(theme._id);
                this._closeDropdown();
            });

            dropdown.appendChild(item);
        }

        document.body.appendChild(dropdown);
        this._dropdown = dropdown;

        // 阻止下拉菜单内部点击关闭
        dropdown.addEventListener('click', (e) => e.stopPropagation());
    },

    _closeDropdown() {
        if (this._dropdown) {
            this._dropdown.remove();
            this._dropdown = null;
        }
    },

    _updateDropdown() {
        if (!this._dropdown) return;
        const items = this._dropdown.querySelectorAll('.theme-dropdown-item');
        const themes = this.getThemes();
        items.forEach((item, i) => {
            if (themes[i] && themes[i]._id === this._current) {
                item.classList.add('active');
            } else {
                item.classList.remove('active');
            }
        });
    }
};
