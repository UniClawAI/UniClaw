/* floating-window.js — 非模态浮动窗口管理器 */

const FloatingWindow = {
    _windows: new Map(),  // id → { el, offsetX, offsetY, isDragging, _initialized }
    _zIndexBase: 1000,
    _topZIndex: 1000,

    /**
     * 初始化一个 modal-overlay 为非模态浮动窗口
     * @param {string} id - modal-overlay 元素的 id
     */
    init(id) {
        const el = document.getElementById(id);
        if (!el) return;

        const state = {
            el,
            isDragging: false,
            offsetX: 0,
            offsetY: 0,
            _initialized: false,  // 是否已初始化位置
        };
        this._windows.set(id, state);

        // 绑定拖拽
        this._bindDrag(id, state);

        // 点击时提升 z-index
        el.addEventListener('mousedown', () => {
            this._bringToFront(id);
        });
    },

    /**
     * 居中显示窗口
     */
    _centerWindow(el) {
        const content = el.querySelector('.modal-content');
        if (!content) return;

        // 强制刷新布局,确保能获取到正确的尺寸
        content.offsetHeight;

        const rect = content.getBoundingClientRect();
        const w = rect.width || 520;
        const h = rect.height || 400;

        const left = Math.max(0, (window.innerWidth - w) / 2);
        const top = Math.max(0, (window.innerHeight - h) / 2);

        content.style.position = 'fixed';
        content.style.left = `${left}px`;
        content.style.top = `${top}px`;
        content.style.right = 'auto';
        content.style.bottom = 'auto';
    },

    /**
     * Center a floating window in the current viewport.
     */
    center(id) {
        const state = this._windows.get(id);
        if (state && state.el) this._centerWindow(state.el);
    },

    /**
     * 绑定拖拽功能
     */
    _bindDrag(id, state) {
        const overlay = state.el;
        const content = overlay.querySelector('.modal-content');
        const title = overlay.querySelector('.modal-title');
        if (!title || !content) return;

        const onMouseDown = (e) => {
            // 忽略按钮点击
            if (e.target.tagName === 'BUTTON' || e.target.closest('button')) return;

            state.isDragging = true;
            const rect = content.getBoundingClientRect();
            state.offsetX = e.clientX - rect.left;
            state.offsetY = e.clientY - rect.top;

            // 添加临时样式
            document.body.style.userSelect = 'none';
            document.body.style.webkitUserSelect = 'none';
            e.preventDefault();
        };

        const onMouseMove = (e) => {
            if (!state.isDragging) return;

            let left = e.clientX - state.offsetX;
            let top = e.clientY - state.offsetY;

            // 限制在视口内
            const maxLeft = window.innerWidth - 100;
            const maxTop = window.innerHeight - 50;
            left = Math.max(-content.offsetWidth + 100, Math.min(maxLeft, left));
            top = Math.max(0, Math.min(maxTop, top));

            content.style.position = 'fixed';
            content.style.left = `${left}px`;
            content.style.top = `${top}px`;
            content.style.right = 'auto';
            content.style.bottom = 'auto';
        };

        const onMouseUp = () => {
            if (!state.isDragging) return;
            state.isDragging = false;
            document.body.style.userSelect = '';
            document.body.style.webkitUserSelect = '';
        };

        title.addEventListener('mousedown', onMouseDown);
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);

        // 保存清理函数
        state._cleanup = () => {
            title.removeEventListener('mousedown', onMouseDown);
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
        };
    },

    /**
     * 将窗口置顶
     */
    _bringToFront(id) {
        this._topZIndex++;
        const state = this._windows.get(id);
        if (state) {
            state.el.style.zIndex = this._topZIndex;
        }
    },

    /**
     * 显示窗口,首次显示时居中,之后保持上次位置
     */
    show(id) {
        let state = this._windows.get(id);
        if (!state) {
            // 自动初始化
            this.init(id);
            state = this._windows.get(id);
        }
        if (!state) return;

        const el = state.el;
        if (!el) return;

        // 首次显示时居中
        if (!state._initialized) {
            state._initialized = true;
            // 先移除 hidden,让元素可以计算尺寸
            el.classList.remove('hidden');
            el.classList.add('entering');
            // 等待浏览器重绘完成后再计算位置
            requestAnimationFrame(() => {
                this._centerWindow(el);
                el.classList.remove('entering');
            });
        } else {
            el.classList.remove('hidden');
            el.classList.add('entering');
            requestAnimationFrame(() => {
                el.classList.remove('entering');
            });
        }

        this._bringToFront(id);
    },

    /**
     * 隐藏窗口
     */
    hide(id) {
        const el = document.getElementById(id);
        if (el) {
            el.classList.add('hidden');
        }
    },

    /**
     * 清理资源
     */
    destroy(id) {
        const state = this._windows.get(id);
        if (state && state._cleanup) {
            state._cleanup();
        }
        this._windows.delete(id);
    },
};
