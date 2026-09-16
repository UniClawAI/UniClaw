/* settings.js — 全局设置弹窗(含 combo 下拉选择组件) */

const Settings = {
    _data: null,        // 从后端加载的原始数据
    _providers: {},     // 当前编辑中的 providers
    _models: [],        // 从 /api/models 获取的模型列表
    _providersInfo: {}, // 各 provider 的额外信息(如 allow_custom)

    // ── 打开 / 关闭 ──────────────────────────────────────

    async open() {
        const modal = document.getElementById('settings-modal');
        modal.classList.remove('hidden');
        // 隐藏 banner(如果是正常打开设置,不需要显示提示)
        const banner = document.getElementById('settings-banner');
        if (banner) banner.style.display = 'none';

        try {
            const token = localStorage.getItem('uniclaw_token');
            const authHeaders = { 'Authorization': 'Bearer ' + token };

            // 先加载 settings
            const settingsResp = await fetch('/api/settings', { headers: authHeaders });
            if (!settingsResp.ok) {
                const d = await settingsResp.json();
                Utils.showError(d.detail || '加载失败');
                return;
            }
            this._data = await settingsResp.json();

            // 用 settings 中的 providers 请求模型列表
            try {
                const modelsResp = await fetch('/api/models', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', ...authHeaders },
                    body: JSON.stringify({
                        providers: this._data.providers || {},
                    }),
                });
                if (modelsResp.ok) {
                    const modelsData = await modelsResp.json();
                    this._models = modelsData.models || [];
                    this._embeddingModels = modelsData.embedding_models || [];
                    this._providersInfo = modelsData.providers_info || {};
                } else {
                    this._models = [];
                    this._embeddingModels = [];
                    this._providersInfo = {};
                }
            } catch {
                this._models = [];
                this._embeddingModels = [];
                this._providersInfo = {};
            }

            this._render();
        } catch (e) {
            Utils.showError('网络错误: ' + e.message);
        }
    },

    close() {
        document.getElementById('settings-modal').classList.add('hidden');
        this._data = null;
        this._providers = {};
        this._models = [];
        this._providersInfo = {};
        // 隐藏 banner
        const banner = document.getElementById('settings-banner');
        if (banner) banner.style.display = 'none';
    },

    // ── 渲染表单 ─────────────────────────────────────────

    _render() {
        const d = this._data;

        const levelText = d.config_level === 'project' ? 'project' : 'user';
        document.getElementById('settings-config-path').textContent = levelText;

        // providers
        this._providers = {};
        for (const [name, p] of Object.entries(d.providers || {})) {
            this._providers[name] = { ...p };
        }
        this._renderProviders();

        // 模型字段:初始化 combo 组件
        this._embeddingModels = this._embeddingModels || [];
        this._initCombo('settings-model-name', d.model_name || []);
        this._initCombo('settings-mini-model', d.mini_model_name || []);
        this._initCombo('settings-multimodal-model', d.multimodal_model_name || []);
        this._initCombo('settings-large-model', d.large_model_name || []);
        this._initCombo('settings-tts-model', d.tts_model ? [d.tts_model] : []);
        this._initCombo('settings-asr-model', d.asr_model ? [d.asr_model] : []);
        this._initCombo('settings-image-model', d.image_model ? [d.image_model] : []);
        this._initCombo('settings-embedding-model', d.embedding_model ? [d.embedding_model] : []);

        // 音频配置
        const audioEl = document.getElementById('settings-audio');
        audioEl.value = d.audio ? JSON.stringify(d.audio, null, 2) : '';

        // 生成参数
        document.getElementById('settings-temperature').value = d.temperature ?? '';
        document.getElementById('settings-max-tokens').value = d.max_tokens ?? '';
        document.getElementById('settings-top-p').value = d.top_p ?? '';

        // 其他
        document.getElementById('settings-proxy').value = d.proxy_url || '';
        document.getElementById('settings-github-token').value = d.GITHUB_TOKEN || '';
        document.getElementById('settings-exa-key').value = d.EXA_API_KEY || '';
        document.getElementById('settings-max-depth').value = d.max_agent_depth ?? 2;
        document.getElementById('settings-perm-timeout').value = d.permission_timeout ?? 300;
        document.getElementById('settings-perm-mode').value = d.permission_mode || 'auto';
        document.getElementById('settings-trusted-ips').value = (d.trusted_ips || []).join(', ');
    },

    // ── Combo 组件 ────────────────────────────────────────

    /** 初始化一个 combo 容器 */
    _initCombo(containerId, selectedValues) {
        const container = document.getElementById(containerId);
        if (!container) return;

        const isMulti = container.dataset.multi === 'true';
        const input = container.querySelector('.combo-input');
        const dropdown = container.querySelector('.combo-dropdown');
        const selectedEl = container.querySelector('.combo-selected');

        // 清空
        selectedEl.innerHTML = '';
        input.value = '';
        dropdown.innerHTML = '';
        dropdown.classList.remove('open');

        // 渲染已选标签
        for (const val of selectedValues) {
            if (val) this._addTag(container, val);
        }

        // 点击容器聚焦输入框
        container.addEventListener('click', () => input.focus());

        // 输入过滤
        input.addEventListener('input', () => {
            this._renderDropdown(container, input.value.trim());
            dropdown.classList.add('open');
        });

        // 聚焦时打开下拉
        input.addEventListener('focus', () => {
            this._renderDropdown(container, input.value.trim());
            dropdown.classList.add('open');
        });

        // 键盘事件
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                if (Utils.isImeComposing(e)) return;  // IME 组合中: Enter 仅上屏, 不添加自定义模型
                e.preventDefault();
                const text = input.value.trim();
                if (text) {
                    const added = this._addTag(container, text);
                    if (added) {
                        input.value = '';
                        dropdown.classList.remove('open');
                    }
                }
            } else if (e.key === 'Escape') {
                dropdown.classList.remove('open');
            } else if (e.key === 'Backspace' && !input.value) {
                // 删除最后一个标签
                const tags = selectedEl.querySelectorAll('.combo-tag');
                if (tags.length > 0) {
                    const last = tags[tags.length - 1];
                    this._removeTag(container, last.dataset.value);
                }
            }
        });

        // 点击外部关闭下拉
        document.addEventListener('click', (e) => {
            if (!container.contains(e.target)) {
                dropdown.classList.remove('open');
            }
        });
    },

    /** 渲染下拉列表 */
    _renderDropdown(container, filter) {
        const dropdown = container.querySelector('.combo-dropdown');
        const selected = this._getComboValues(container);
        const selectedSet = new Set(selected);
        const isTtsAsr = ['settings-tts-model', 'settings-asr-model', 'settings-image-model', 'settings-embedding-model'].includes(container.id);
        const isMultimodal = container.id === 'settings-multimodal-model';
        const filterLower = filter.toLowerCase();

        // TTS/ASR/图片生成/Embedding: 只显示 OpenAI 协议的模型
        // 多模态模型: 只显示支持视觉的模型
        let availableModels = this._models;
        if (container.id === 'settings-embedding-model') {
            availableModels = this._embeddingModels || [];
        } else if (isTtsAsr) {
            availableModels = this._models.filter(m => {
                const info = this._providersInfo[m.provider];
                return info && !info.allow_custom;
            });
        } else if (isMultimodal) {
            // 只过滤明确不支持视觉的模型,未知的保留
            availableModels = this._models.filter(m => m.supports_vision !== false);
        }

        // 过滤
        const filtered = filterLower
            ? availableModels.filter(m => {
                const fullId = m.provider + '/' + m.id;
                return m.id.toLowerCase().includes(filterLower) ||
                       m.provider.toLowerCase().includes(filterLower) ||
                       fullId.toLowerCase().includes(filterLower);
            })
            : availableModels;

        // 按 provider 分组
        const groups = {};
        for (const m of filtered) {
            if (!groups[m.provider]) groups[m.provider] = [];
            groups[m.provider].push(m);
        }

        // 构建 HTML
        let html = '';
        for (const [provider, models] of Object.entries(groups)) {
            html += `<div class="combo-dropdown-group">`;
            html += `<div class="combo-dropdown-group-label">${this._esc(provider)}</div>`;
            for (const m of models) {
                const fullId = provider + '/' + m.id;
                const isSelected = selectedSet.has(fullId);
                // 三种状态: true=支持, false=不支持, null/undefined=未知(不显示)
                const visionIcon = m.supports_vision === true ? '<span class="capability-icon vision" title="支持视觉">👁</span>'
                                 : m.supports_vision === false ? '<span class="capability-icon no-vision" title="不支持视觉">👁</span>' : '';
                const videoIcon = m.supports_video === true ? '<span class="capability-icon video" title="支持视频">🎬</span>'
                                : m.supports_video === false ? '<span class="capability-icon no-video" title="不支持视频">🎬</span>' : '';
                const audioIcon = m.supports_audio === true ? '<span class="capability-icon audio" title="支持音频">🎵</span>'
                                : m.supports_audio === false ? '<span class="capability-icon no-audio" title="不支持音频">🎵</span>' : '';
                const toolsIcon = m.supports_tools === true ? '<span class="capability-icon tools" title="支持工具调用">🔧</span>'
                                : m.supports_tools === false ? '<span class="capability-icon no-tools" title="不支持工具调用">🔧</span>' : '';
                const icons = [visionIcon, videoIcon, audioIcon, toolsIcon].filter(Boolean).join('');
                // 价格信息 (转换为每百万token,保留3位有效数字)
                const formatPrice3 = (v) => {
                    if (v == null || v === 0) return '?';
                    const n = v * 1000000;
                    if (n >= 100) return n.toFixed(0);
                    if (n >= 10) return n.toFixed(1);
                    if (n >= 1) return n.toFixed(2);
                    return n.toPrecision(3);
                };
                let priceHtml = '';
                if (m.price) {
                    const p = m.price;
                    const promptStr = formatPrice3(p.prompt);
                    const completionStr = formatPrice3(p.completion);
                    const cacheReadStr = p.input_cache_read ? formatPrice3(p.input_cache_read) : null;
                    const cacheWriteStr = p.input_cache_write ? formatPrice3(p.input_cache_write) : null;
                    let tip = `输入: $${promptStr}/M\n输出: $${completionStr}/M`;
                    if (cacheReadStr) tip += `\n缓存读取: $${cacheReadStr}/M`;
                    if (cacheWriteStr) tip += `\n缓存写入: $${cacheWriteStr}/M`;
                    tip += '\n价格仅供参考,不同提供商可能不同';
                    priceHtml = `<span class="model-price" title="${tip}">$${promptStr}/$${completionStr}</span>`;
                }
                html += `<div class="combo-item${isSelected ? ' selected' : ''}" data-value="${this._esc(fullId)}">`;
                html += `<span>${this._esc(m.id)}</span>`;
                if (icons) {
                    html += `<span class="capability-icons">${icons}</span>`;
                }
                if (priceHtml) {
                    html += priceHtml;
                }
                if (!isSelected) {
                    html += `<span class="combo-item-provider">${this._esc(provider)}</span>`;
                }
                html += `</div>`;
            }
            html += `</div>`;
        }

        // 判断是否允许自定义输入
        const allowCustom = this._allowCustomInput(container);
        if (filter && allowCustom) {
            // 检查是否已有完全匹配
            const exactMatch = availableModels.some(m => {
                const fullId = m.provider + '/' + m.id;
                return fullId.toLowerCase() === filterLower;
            });
            if (!exactMatch) {
                html += `<div class="combo-input-hint">按 Enter 添加自定义模型: ${this._esc(filter)}</div>`;
            }
        }

        if (!html) {
            html = `<div class="combo-empty">${filter ? '无匹配模型' : '暂无可用模型'}</div>`;
        }

        dropdown.innerHTML = html;

        // 绑定点击事件
        dropdown.querySelectorAll('.combo-item').forEach(item => {
            // 阻止 mousedown 防止 input 失焦导致 dropdown 重新渲染
            item.addEventListener('mousedown', (e) => {
                e.preventDefault();
                e.stopPropagation();
            });
            item.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                const value = item.dataset.value;
                if (item.classList.contains('selected')) {
                    this._removeTag(container, value);
                } else {
                    this._addTag(container, value);
                }
                const input = container.querySelector('.combo-input');
                input.value = '';
                this._renderDropdown(container, '');
            });
        });
    },

    /** 判断当前 combo 是否允许自定义输入 */
    _allowCustomInput(container) {
        const isTtsAsr = ['settings-tts-model', 'settings-asr-model', 'settings-image-model', 'settings-embedding-model'].includes(container.id);
        if (isTtsAsr) {
            // TTS/ASR/图片生成/Embedding:仅 OpenAI 协议,不允许自定义输入
            return false;
        }
        // 主模型/轻量/多模态:只要有任一 provider 是 allow_custom(Anthropic)就允许
        for (const info of Object.values(this._providersInfo)) {
            if (info.allow_custom) return true;
        }
        return false;
    },

    /** 添加标签,返回是否成功添加 */
    _addTag(container, value) {
        const isMulti = container.dataset.multi === 'true';
        const selectedEl = container.querySelector('.combo-selected');

        // 同步表单中的最新 providers(用户可能改了名称)
        this._syncProviders();

        // 验证:非自定义输入必须存在于模型列表中
        if (!this._isValidModel(value, container)) {
            return false;
        }

        // 单选模式:替换
        if (!isMulti) {
            selectedEl.innerHTML = '';
        }

        // 检查重复
        const existing = selectedEl.querySelectorAll('.combo-tag');
        for (const tag of existing) {
            if (tag.dataset.value === value) return false;
        }

        // 创建标签
        const tag = document.createElement('div');
        tag.className = 'combo-tag';
        tag.dataset.value = value;
        tag.draggable = isMulti;

        let tagHtml = '';
        if (isMulti) {
            tagHtml += `<span class="combo-tag-drag" title="拖拽排序">⋮⋮</span>`;
        }
        tagHtml += `<span class="combo-tag-text">${this._esc(this._formatModelDisplay(value))}</span>`;
        tagHtml += `<span class="combo-tag-remove" title="移除">✕</span>`;
        tag.innerHTML = tagHtml;

        // 删除按钮
        tag.querySelector('.combo-tag-remove').addEventListener('click', (e) => {
            e.stopPropagation();
            this._removeTag(container, value);
        });

        // 拖拽排序(多选模式)
        if (isMulti) {
            tag.addEventListener('dragstart', (e) => {
                tag.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', value);
            });
            tag.addEventListener('dragend', () => tag.classList.remove('dragging'));
            tag.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                tag.classList.add('drag-over');
            });
            tag.addEventListener('dragleave', () => tag.classList.remove('drag-over'));
            tag.addEventListener('drop', (e) => {
                e.preventDefault();
                tag.classList.remove('drag-over');
                const fromValue = e.dataTransfer.getData('text/plain');
                if (fromValue && fromValue !== value) {
                    this._reorderTag(container, fromValue, value);
                }
            });
        }

        selectedEl.appendChild(tag);
        return true;
    },

    /** 移除标签 */
    _removeTag(container, value) {
        const selectedEl = container.querySelector('.combo-selected');
        const tags = selectedEl.querySelectorAll('.combo-tag');
        for (const tag of tags) {
            if (tag.dataset.value === value) {
                tag.remove();
                break;
            }
        }
    },

    /** 拖拽排序:将 fromValue 移动到 toValue 前面 */
    _reorderTag(container, fromValue, toValue) {
        const selectedEl = container.querySelector('.combo-selected');
        const tags = Array.from(selectedEl.querySelectorAll('.combo-tag'));
        const fromTag = tags.find(t => t.dataset.value === fromValue);
        const toTag = tags.find(t => t.dataset.value === toValue);
        if (fromTag && toTag) {
            selectedEl.insertBefore(fromTag, toTag);
        }
    },

    /** 验证模型名是否有效 */
    _isValidModel(value, container) {
        // 格式检查
        if (!value.includes('/')) return false;

        const providerName = value.split('/')[0];

        // 检查是否存在于已配置的 providers
        if (!this._providers[providerName]) return false;

        const info = this._providersInfo[providerName];
        const isTtsAsr = ['settings-tts-model', 'settings-asr-model', 'settings-image-model', 'settings-embedding-model'].includes(container.id);

        // TTS/ASR/图片生成/Embedding:仅允许 OpenAI 协议(非 allow_custom)的 provider
        if (isTtsAsr && info && info.allow_custom) return false;

        // allow_custom 的 provider:自由输入
        if (info && info.allow_custom) return true;

        // 非 allow_custom 的 provider:必须在模型列表中
        const modelId = value.split('/').slice(1).join('/');
        if (container.id === 'settings-embedding-model' && this._embeddingModels) {
            return this._embeddingModels.some(m => m.provider === providerName && m.id === modelId);
        }
        return this._models.some(m => m.provider === providerName && m.id === modelId);
    },

    /** 格式化模型显示名 */
    _formatModelDisplay(value) {
        // 显示完整的 provider/model
        return value;
    },

    /** 获取 combo 中所有已选值(按 DOM 顺序) */
    _getComboValues(container) {
        const selectedEl = container.querySelector('.combo-selected');
        return Array.from(selectedEl.querySelectorAll('.combo-tag'))
            .map(tag => tag.dataset.value)
            .filter(Boolean);
    },

    // ── Provider 渲染(保持原逻辑) ──────────────────────

    _renderProviders() {
        const container = document.getElementById('settings-providers');
        container.innerHTML = '';

        // 添加按钮(始终绑定)
        document.getElementById('settings-add-provider').onclick = () => this._addProvider();

        const names = Object.keys(this._providers);
        if (names.length === 0) {
            container.innerHTML = '<div style="color:var(--text-3);font-size:var(--text-sm);padding:8px 0">暂无模型提供商,点击上方"添加"按钮创建</div>';
            return;
        }

        for (const name of names) {
            const p = this._providers[name];
            const card = document.createElement('div');
            card.className = 'settings-provider-card';
            card.dataset.name = name;

            card.innerHTML = `
                <div class="settings-provider-header">
                    <span class="settings-provider-name">${this._esc(name)}</span>
                    <button class="btn-icon settings-provider-delete" title="删除此 Provider">
                        <svg viewBox="0 0 24 24" fill="none" stroke="var(--red)" stroke-width="2" width="14" height="14"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
                    </button>
                </div>
                <div class="settings-provider-fields">
                    <div class="form-group">
                        <label>名称</label>
                        <input type="text" class="input settings-p-name" value="${this._esc(p.name || name)}" placeholder="provider 名称" />
                    </div>
                    <div class="form-group">
                        <label>协议</label>
                        <select class="input settings-p-protocol">
                            <option value="openai" ${p.protocol === 'openai' ? 'selected' : ''}>OpenAI</option>
                            <option value="anthropic" ${p.protocol === 'anthropic' ? 'selected' : ''}>Anthropic</option>
                        </select>
                    </div>
                    <div class="form-group" style="grid-column:1/-1">
                        <label>API Key</label>
                        <div class="settings-key-input-wrap">
                            <input type="password" class="input settings-p-key" value="${this._esc(p.api_key || '')}" placeholder="sk-..." />
                            <button class="btn-icon settings-eye-btn" title="显示/隐藏">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                            </button>
                        </div>
                    </div>
                    <div class="form-group" style="grid-column:1/-1">
                        <label>Base URL</label>
                        <input type="text" class="input settings-p-url" value="${this._esc(p.base_url || '')}" placeholder="https://api.openai.com/v1" />
                    </div>
                    <div class="form-group" style="grid-column:1/-1">
                        <label>代理 (proxy_url)</label>
                        <input type="text" class="input settings-p-proxy" value="${this._esc(p.proxy_url || '')}" placeholder="留空则直连(不使用代理)" />
                    </div>
                </div>
            `;

            // 删除按钮
            card.querySelector('.settings-provider-delete').addEventListener('click', () => {
                delete this._providers[name];
                this._renderProviders();
            });

            // 眼睛按钮
            card.querySelector('.settings-eye-btn').addEventListener('click', () => {
                const input = card.querySelector('.settings-p-key');
                input.type = input.type === 'password' ? 'text' : 'password';
            });

            container.appendChild(card);
        }
    },

    _addProvider() {
        let baseName = 'new-provider';
        let name = baseName;
        let i = 1;
        while (this._providers[name]) {
            name = baseName + '-' + i;
            i++;
        }

        this._providers[name] = {
            name: name,
            protocol: 'openai',
            api_key: '',
            base_url: '',
            proxy_url: '',
        };
        this._renderProviders();

        const container = document.getElementById('settings-providers');
        const lastCard = container.lastElementChild;
        if (lastCard) {
            lastCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            const nameInput = lastCard.querySelector('.settings-p-name');
            if (nameInput) {
                nameInput.focus();
                nameInput.select();
            }
        }
    },

    // ── 保存 ──────────────────────────────────────────────

    async save() {
        // 未加载到配置(接口失败/尚未打开)时禁止保存, 否则会以空表单覆盖写盘
        if (!this._data) { Utils.showError('设置尚未加载完成, 无法保存'); return; }
        // 收集 providers
        const providers = {};
        const cards = document.querySelectorAll('.settings-provider-card');
        // 解析每张卡片的最终名称(空时回退到原 dataset.name)
        const resolvedName = (card) => card.querySelector('.settings-p-name').value.trim() || card.dataset.name;
        for (const card of cards) {
            const oldName = card.dataset.name;
            const newName = resolvedName(card);
            const protocol = card.querySelector('.settings-p-protocol').value;
            const apiKey = card.querySelector('.settings-p-key').value;
            const baseUrl = card.querySelector('.settings-p-url').value.trim();
            const proxyUrl = card.querySelector('.settings-p-proxy').value.trim();

            if (!newName) {
                Utils.showError('Provider 名称不能为空');
                return;
            }

            // 全局重名检查: 不能只查 newName !== oldName — 未改名的 provider 也可能
            // 被另一张改成同名(静默覆盖)。遍历所有卡片比对最终名称
            for (const other of cards) {
                if (other !== card && resolvedName(other) === newName) {
                    Utils.showError(`Provider 名称 "${newName}" 重复`);
                    return;
                }
            }

            providers[newName] = {
                name: newName,
                protocol,
                api_key: apiKey,
                base_url: baseUrl,
                proxy_url: proxyUrl,
            };
        }

        // 从 combo 收集模型列表
        const modelName = this._getComboValues(document.getElementById('settings-model-name'));
        const miniModel = this._getComboValues(document.getElementById('settings-mini-model'));
        const multimodalModel = this._getComboValues(document.getElementById('settings-multimodal-model'));
        const largeModel = this._getComboValues(document.getElementById('settings-large-model'));
        const ttsValues = this._getComboValues(document.getElementById('settings-tts-model'));
        const asrValues = this._getComboValues(document.getElementById('settings-asr-model'));
        const imageValues = this._getComboValues(document.getElementById('settings-image-model'));
        const embeddingValues = this._getComboValues(document.getElementById('settings-embedding-model'));

        // 验证模型名的 provider 前缀
        const providerNames = new Set(Object.keys(providers));
        const allModels = [
            ...modelName.map(m => ({ field: '主模型', value: m })),
            ...miniModel.map(m => ({ field: '轻量模型', value: m })),
            ...multimodalModel.map(m => ({ field: '多模态模型', value: m })),
            ...largeModel.map(m => ({ field: '顾问模型', value: m })),
            ...ttsValues.map(m => ({ field: 'TTS 模型', value: m })),
            ...asrValues.map(m => ({ field: 'ASR 模型', value: m })),
            ...imageValues.map(m => ({ field: '图片生成模型', value: m })),
            ...embeddingValues.map(m => ({ field: 'Embedding 模型', value: m })),
        ];
        for (const { field, value } of allModels) {
            if (!value.includes('/')) {
                Utils.showError(`${field} 格式错误:"${value}" 必须是 "provider/model" 格式`);
                return;
            }
            const prefix = value.split('/')[0];
            if (!providerNames.has(prefix)) {
                Utils.showError(`${field} "${value}" 引用了不存在的 provider "${prefix}"`);
                return;
            }
        }

        // 收集其他字段
        const temperature = document.getElementById('settings-temperature').value;
        const maxTokens = document.getElementById('settings-max-tokens').value;
        const topP = document.getElementById('settings-top-p').value;

        // 解析 audio JSON(可能返回 undefined 表示格式错误)
        const audio = this._parseAudio();
        if (audio === undefined) return;

        const body = {
            session_id: SessionPanel.activeSessionId || '',
            model_name: modelName,
            mini_model_name: miniModel,
            multimodal_model_name: multimodalModel,
            large_model_name: largeModel,
            tts_model: ttsValues[0] || '',
            asr_model: asrValues[0] || '',
            image_model: imageValues[0] || '',
            embedding_model: embeddingValues[0] || '',
            audio: audio,
            temperature: temperature !== '' ? parseFloat(temperature) : null,
            max_tokens: maxTokens !== '' ? parseInt(maxTokens) : null,
            top_p: topP !== '' ? parseFloat(topP) : null,
            proxy_url: document.getElementById('settings-proxy').value.trim(),
            // 注意:不能写 `value || this._data.xxx` 回退 — 用户清空输入框(想删除密钥)时
            // 空串会回退成回填的脱敏值 "sk****abcd",后端见 **** 又恢复原密钥,永远删不掉。
            GITHUB_TOKEN: document.getElementById('settings-github-token').value.trim(),
            EXA_API_KEY: document.getElementById('settings-exa-key').value.trim(),
            max_agent_depth: (() => { const n = parseInt(document.getElementById('settings-max-depth').value, 10); return Number.isFinite(n) ? n : 2; })(),
            permission_timeout: (() => { const n = parseInt(document.getElementById('settings-perm-timeout').value, 10); return Number.isFinite(n) ? n : 300; })(),
            permission_mode: document.getElementById('settings-perm-mode').value || 'auto',
            trusted_ips: document.getElementById('settings-trusted-ips').value
                .split(/[,,\s]+/)
                .map(s => s.trim())
                .filter(Boolean),
            providers,
        };

        // 发送请求
        const saveBtn = document.getElementById('settings-save-btn');
        saveBtn.disabled = true;
        saveBtn.textContent = '保存中...';

        try {
            const token = localStorage.getItem('uniclaw_token');
            const resp = await fetch('/api/settings', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + token,
                },
                body: JSON.stringify(body),
            });

            if (!resp.ok) {
                const d = await resp.json();
                Utils.showError(d.detail || '保存失败');
                return;
            }

            Utils.showToast(body.session_id ? '会话设置已保存' : '全局设置已保存');
            this.close();
            // 会话设置保存后刷新状态栏(模型名等)
            if (body.session_id) {
                SessionPanel._updateStatusBar(SessionPanel.activeProjectDir, body.session_id);
            }
            // 更新状态栏权限模式显示
            const permEl = document.getElementById('status-permission');
            if (permEl) {
                const modeLabels = { 'auto': 'Auto', 'manual': 'Manual', 'accept-all': 'Accept All', 'plan': 'Plan' };
                permEl.textContent = modeLabels[body.permission_mode] || 'Auto';
                permEl.className = `perm-mode ${body.permission_mode}`;
            }
        } catch (e) {
            Utils.showError('网络错误: ' + e.message);
        } finally {
            saveBtn.disabled = false;
            saveBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg> 保存`;
        }
    },

    // ── 工具方法 ──────────────────────────────────────────

    /** 解析 audio textarea 为 dict,空或无效返回 null */
    _parseAudio() {
        const raw = document.getElementById('settings-audio').value.trim();
        if (!raw) return null;
        try {
            const obj = JSON.parse(raw);
            if (typeof obj !== 'object' || obj === null || Array.isArray(obj)) {
                Utils.showError('audio 必须是 JSON 对象');
                return undefined;
            }
            return obj;
        } catch (e) {
            Utils.showError('audio JSON 格式错误: ' + e.message);
            return undefined;
        }
    },

    _toggleEye(inputId) {
        const input = document.getElementById(inputId);
        if (input) {
            input.type = input.type === 'password' ? 'text' : 'password';
        }
    },

    _esc(str) {
        return Utils.escapeHtml(String(str ?? ''));
    },

    /** 从表单同步最新的 providers 到 this._providers */
    _syncProviders() {
        const providers = {};
        const cards = document.querySelectorAll('.settings-provider-card');
        for (const card of cards) {
            const name = card.querySelector('.settings-p-name').value.trim();
            if (!name) continue;
            providers[name] = {
                name,
                protocol: card.querySelector('.settings-p-protocol').value,
                api_key: card.querySelector('.settings-p-key').value,
                base_url: card.querySelector('.settings-p-url').value.trim(),
                proxy_url: card.querySelector('.settings-p-proxy').value.trim(),
            };
        }
        this._providers = providers;
    },

    /** 从当前表单的 providers 刷新模型列表 */
    async _refreshModels() {
        const refreshBtn = document.getElementById('settings-refresh-models');
        refreshBtn.disabled = true;
        refreshBtn.textContent = '刷新中...';

        this._syncProviders();

        try {
            const token = localStorage.getItem('uniclaw_token');
            const resp = await fetch('/api/models', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + token,
                },
                body: JSON.stringify({ providers: this._providers }),
            });
            if (resp.ok) {
                const data = await resp.json();
                this._models = data.models || [];
                this._embeddingModels = data.embedding_models || [];
                this._providersInfo = data.providers_info || {};
            }
        } catch {
            // 静默失败
        }

        // 重新渲染所有 combo(保留已选值)
        const comboIds = [
            'settings-model-name',
            'settings-mini-model',
            'settings-multimodal-model',
            'settings-large-model',
            'settings-tts-model',
            'settings-asr-model',
            'settings-image-model',
            'settings-embedding-model',
        ];
        for (const id of comboIds) {
            const container = document.getElementById(id);
            if (!container) continue;
            const savedValues = this._getComboValues(container);
            this._initCombo(id, savedValues);
        }

        refreshBtn.disabled = false;
        refreshBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 16h5v5"/></svg> 刷新`;
    },
};

// 保存按钮绑定
document.addEventListener('DOMContentLoaded', () => {
    const saveBtn = document.getElementById('settings-save-btn');
    if (saveBtn) saveBtn.addEventListener('click', () => Settings.save());

    const refreshBtn = document.getElementById('settings-refresh-models');
    if (refreshBtn) refreshBtn.addEventListener('click', () => Settings._refreshModels());

    // ESC 关闭
    document.getElementById('settings-modal')?.addEventListener('click', (e) => {
        // 不再点击外部关闭,只保留 ESC 关闭
    });
});
