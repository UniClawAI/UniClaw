/* settings.js — 全局设置弹窗 */

const Settings = {
    _data: null,   // 从后端加载的原始数据
    _providers: {}, // 当前编辑中的 providers

    /** 打开设置弹窗 */
    async open() {
        const modal = document.getElementById('settings-modal');
        const errEl = document.getElementById('settings-error');
        errEl.textContent = '';
        modal.classList.remove('hidden');

        try {
            const token = localStorage.getItem('uniclaw_token');
            const resp = await fetch('/api/settings', {
                headers: { 'Authorization': 'Bearer ' + token },
            });
            if (!resp.ok) {
                const d = await resp.json();
                errEl.textContent = d.detail || '加载失败';
                return;
            }
            this._data = await resp.json();
            this._render();
        } catch (e) {
            errEl.textContent = '网络错误: ' + e.message;
        }
    },

    /** 关闭弹窗 */
    close() {
        document.getElementById('settings-modal').classList.add('hidden');
        this._data = null;
        this._providers = {};
    },

    /** 将数据渲染到表单 */
    _render() {
        const d = this._data;

        // config path
        document.getElementById('settings-config-path').textContent = d.config_path || '';

        // providers
        this._providers = {};
        for (const [name, p] of Object.entries(d.providers || {})) {
            this._providers[name] = { ...p };
        }
        this._renderProviders();

        // 模型字段：list → 逗号分隔字符串
        document.getElementById('settings-model-name').value = (d.model_name || []).join(', ');
        document.getElementById('settings-mini-model').value = (d.mini_model_name || []).join(', ');
        document.getElementById('settings-multimodal-model').value = (d.multimodal_model_name || []).join(', ');
        document.getElementById('settings-tts-model').value = d.tts_model || '';
        document.getElementById('settings-asr-model').value = d.asr_model || '';

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
    },

    /** 渲染 provider 卡片列表 */
    _renderProviders() {
        const container = document.getElementById('settings-providers');
        container.innerHTML = '';

        const names = Object.keys(this._providers);
        if (names.length === 0) {
            container.innerHTML = '<div style="color:var(--text-3);font-size:var(--text-sm);padding:8px 0">暂无 Provider，点击上方"添加"按钮创建</div>';
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
                        <input type="text" class="input settings-p-proxy" value="${this._esc(p.proxy_url || '')}" placeholder="留空则使用全局代理" />
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

        // 添加按钮
        document.getElementById('settings-add-provider').onclick = () => this._addProvider();
    },

    /** 添加新 provider */
    _addProvider() {
        // 生成唯一名称
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

        // 滚动到底部并聚焦名称输入
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

    /** 从表单收集数据并保存 */
    async save() {
        const errEl = document.getElementById('settings-error');
        errEl.textContent = '';

        // 收集 providers
        const providers = {};
        const cards = document.querySelectorAll('.settings-provider-card');
        for (const card of cards) {
            const oldName = card.dataset.name;
            const newName = card.querySelector('.settings-p-name').value.trim() || oldName;
            const protocol = card.querySelector('.settings-p-protocol').value;
            const apiKey = card.querySelector('.settings-p-key').value;
            const baseUrl = card.querySelector('.settings-p-url').value.trim();
            const proxyUrl = card.querySelector('.settings-p-proxy').value.trim();

            if (!newName) {
                errEl.textContent = 'Provider 名称不能为空';
                return;
            }

            // 如果名称变了，检查冲突
            if (newName !== oldName && providers[newName]) {
                errEl.textContent = `Provider 名称 "${newName}" 重复`;
                return;
            }

            providers[newName] = {
                name: newName,
                protocol,
                api_key: apiKey,
                base_url: baseUrl,
                proxy_url: proxyUrl,
            };
        }

        // 解析模型名
        const parseModels = (str) => str.split(',').map(s => s.trim()).filter(Boolean);
        const modelName = parseModels(document.getElementById('settings-model-name').value);
        const miniModel = parseModels(document.getElementById('settings-mini-model').value);
        const multimodalModel = parseModels(document.getElementById('settings-multimodal-model').value);

        // 验证模型名的 provider 前缀
        const providerNames = new Set(Object.keys(providers));
        const allModels = [
            ...modelName.map(m => ({ field: '主模型', value: m })),
            ...miniModel.map(m => ({ field: '轻量模型', value: m })),
            ...multimodalModel.map(m => ({ field: '多模态模型', value: m })),
        ];
        for (const { field, value } of allModels) {
            if (!value.includes('/')) {
                errEl.textContent = `${field} 格式错误："${value}" 必须是 "provider/model" 格式`;
                return;
            }
            const prefix = value.split('/')[0];
            if (!providerNames.has(prefix)) {
                errEl.textContent = `${field} "${value}" 引用了不存在的 provider "${prefix}"`;
                return;
            }
        }

        // 收集其他字段
        const temperature = document.getElementById('settings-temperature').value;
        const maxTokens = document.getElementById('settings-max-tokens').value;
        const topP = document.getElementById('settings-top-p').value;

        const body = {
            model_name: modelName,
            mini_model_name: miniModel,
            multimodal_model_name: multimodalModel,
            tts_model: document.getElementById('settings-tts-model').value.trim(),
            asr_model: document.getElementById('settings-asr-model').value.trim(),
            temperature: temperature !== '' ? parseFloat(temperature) : null,
            max_tokens: maxTokens !== '' ? parseInt(maxTokens) : null,
            top_p: topP !== '' ? parseFloat(topP) : null,
            proxy_url: document.getElementById('settings-proxy').value.trim(),
            GITHUB_TOKEN: document.getElementById('settings-github-token').value,
            EXA_API_KEY: document.getElementById('settings-exa-key').value,
            max_agent_depth: parseInt(document.getElementById('settings-max-depth').value) || 3,
            permission_timeout: parseInt(document.getElementById('settings-perm-timeout').value) || 300,
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
                errEl.textContent = d.detail || '保存失败';
                return;
            }

            Utils.showToast('设置已保存');
            this.close();
        } catch (e) {
            errEl.textContent = '网络错误: ' + e.message;
        } finally {
            saveBtn.disabled = false;
            saveBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg> 保存`;
        }
    },

    /** 切换密码可见性 */
    _toggleEye(inputId) {
        const input = document.getElementById(inputId);
        if (input) {
            input.type = input.type === 'password' ? 'text' : 'password';
        }
    },

    /** HTML 转义 */
    _esc(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },
};

// 保存按钮绑定
document.addEventListener('DOMContentLoaded', () => {
    const saveBtn = document.getElementById('settings-save-btn');
    if (saveBtn) saveBtn.addEventListener('click', () => Settings.save());

    // ESC 关闭
    document.getElementById('settings-modal')?.addEventListener('click', (e) => {
        if (e.target.id === 'settings-modal') Settings.close();
    });
});
