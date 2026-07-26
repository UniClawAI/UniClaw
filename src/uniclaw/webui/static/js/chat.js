/* chat.js — 聊天区组件 */

const Chat = {
    currentSessionId: null,
    streamingEl: null,
    streamingContent: '',
    streamingBody: null,
    thinkingEl: null,
    thinkingContent: '',
    toolBlocks: {},
    _historyData: null,
    _compactData: null,
    _currentView: 'history',

    // subagent 状态追踪
    _subagentToolId: null,        // 当前 subagent 对应的 tool_call_id
    _subagentName: "",            // 当前 subagent 名称
    _subagentStreamingEl: null,   // tool-block 内的流式内容容器
    _subagentStreamingContent: '',
    _subagentThinkingEl: null,
    _subagentThinkingContent: '',

    init() {
        document.getElementById('history-toggle')?.addEventListener('click', () => {
            this._switchView(this._currentView === 'history' ? 'compact' : 'history');
        });
        WS.on('session_created', msg => this._onSessionCreated(msg));
        WS.on('user', msg => this._onUser(msg));
        WS.on('thinking_start', msg => this._onThinkingStart(msg));
        WS.on('thinking', msg => this._onThinking(msg));
        WS.on('text', msg => this._onText(msg));
        WS.on('assistant', msg => this._onAssistant(msg));
        WS.on('tool_preparing', () => {});
        WS.on('tool_start', msg => this._onToolStart(msg));
        WS.on('tool_stream', msg => this._onToolStream(msg));
        WS.on('tool_end', msg => this._onToolEnd(msg));
        WS.on('config_changed', msg => this._onConfigChanged(msg));
        WS.on('end', msg => this._onEnd(msg));
        WS.on('subagent_end', msg => this._onSubagentEnd(msg));
        WS.on('error', msg => this._onError(msg));
        WS.on('interrupted', msg => this._onInterrupted(msg));
        WS.on('shell_result', msg => this._onShellResult(msg));
        WS.on('command_output', msg => this._onCommandOutput(msg));
        WS.on('command_result', msg => this._onCommandResult(msg));
        WS.on('spinner_start', msg => this._onSpinner(msg));
        WS.on('spinner_update', msg => this._onSpinner(msg));
        WS.on('spinner_stop', msg => this._onSpinnerStop(msg));

        // 初始加载时显示欢迎页面
        this._appendWelcomeScreen();
    },

    // ============================================================
    //  历史消息回放
    // ============================================================

    async loadHistory(sessionId) {
        this.currentSessionId = sessionId;
        this.toolBlocks = {};
        this._historyData = null;
        this._compactData = null;
        try {
            Utils.showLoading('加载历史消息...');
            const resp = await fetch(`/api/sessions/${sessionId}`);
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({ detail: resp.statusText }));
                this.clear(); this._appendSystemMessage(`加载失败: ${err.detail || resp.statusText}`); return;
            }
            const data = await resp.json();
            this._historyData = data.history || null;
            this._compactData = data.messages || null;
            const toggle = document.getElementById('history-toggle');
            const hasDiff = this._historyData && this._compactData && this._historyData.length !== this._compactData.length;
            if (toggle) {
                toggle.style.display = hasDiff ? '' : 'none';
                if (hasDiff) toggle.innerHTML = icon('history'); // compact 视图下显示 history 图标
            }
            this._currentView = 'compact';
            this._renderCurrentView();
            this._fetchAndRenderTodolist(sessionId);
        } catch (e) {
            this.clear(); this._appendSystemMessage(`加载失败: ${e.message}`);
        } finally { Utils.hideLoading(); }
    },

    _switchView(view) {
        if (view === this._currentView) return;
        this._currentView = view;
        const btn = document.getElementById('history-toggle');
        // 显示可切换到的视图图标:当前 compact 显示 history 图标,反之亦然
        if (btn) btn.innerHTML = view === 'compact' ? icon('history') : icon('save');
        this._renderCurrentView();
    },

    _renderCurrentView() {
        const c = document.getElementById('chat-messages');
        c.innerHTML = '';
        this._stopSpinnerTimer();
        const spinner = document.getElementById('spinner-area');
        if (spinner) spinner.innerHTML = '';
        const msgs = this._currentView === 'history' ? this._historyData : this._compactData;
        if (msgs?.length) this._replayMessages(msgs);
        else this._appendWelcomeScreen();
        MsgNav?.refresh?.();
        this._forceScrollToBottom();
    },

    _replayMessages(messages) {
        const toolResults = {};
        messages.forEach(m => { if (m.role === 'tool' && m.tool_call_id) toolResults[m.tool_call_id] = m; });

        messages.forEach((msg, msgIdx) => {
            const role = msg.role;
            if (role === 'system') {
                this._appendSystemMessage(this._extractText(msg.content));
            } else if (role === 'user') {
                const text = this._extractText(msg.content);
                const images = this._extractImages(msg.content);
                const videos = this._extractVideos(msg.content);
                const audio = this._extractAudio(msg.content);
                const files = this._extractFiles(msg.content);
                let el;
                if (text.startsWith('[system]')) {
                    if (text.includes('(用户执行Shell命令)')) el = this._appendShellResultFromHistory(text, msgIdx);
                    else if (images.length > 0) this._appendSystemMessageWithImages(text, images);
                    else this._appendSystemMessage(text);
                } else if (images.length > 0 || videos.length > 0 || audio.length > 0 || files.length > 0) {
                    // 清除 [附件: ...] 后缀,只保留纯文本
                    const cleanText = text.replace(/\n?\[附件: [^\]]+\]/g, '').trim();
                    el = this._appendUserMessageWithMedia(cleanText, images, videos, audio, files);
                } else {
                    el = this._appendUserMessage(text);
                }
                if (el) el.dataset.msgIdx = msgIdx;
            } else if (role === 'assistant') {
                const el = this._appendAssistantMessage('');
                el.closest('.message').dataset.msgIdx = msgIdx;
                const body = el.querySelector('.markdown-body');
                if (msg.reasoning_content) this._appendThinkingBlock(el, msg.reasoning_content, true, body);
                if (body && msg.content) {
                    body.innerHTML = Utils.renderMarkdown(msg.content);
                    Utils.addCopyButtons(body);
                    const btn = body.parentElement?.querySelector('.msg-copy-btn');
                    if (btn && btn.style.display === 'none' && msg.content.trim()) btn.style.display = '';
                }
                if (msg.tool_calls?.length) {
                    msg.tool_calls.forEach(tc => {
                        const tcId = tc.id || '';
                        const name = tc.function?.name || tc.name || 'tool';
                        const args = tc.function?.arguments || tc.arguments || '{}';
                        const result = toolResults[tcId];
                        const resultContent = result ? this._extractText(result.content) : null;
                        const success = result ? !(resultContent?.startsWith('[TOOL_ERROR]')) : null;
                        const explain = result?.explain || '';
                        this._appendToolBlock(el, name, args, resultContent, success, tcId, explain);
                    });
                }
                this._appendUsageInfo(el, msg.usage?.input_tokens, msg.usage?.output_tokens, msg.model_name);
            }
        });
    },

    // ============================================================
    //  消息创建
    // ============================================================

    clear() {
        document.getElementById('chat-messages').innerHTML = '';
        this._resetStreamingState();
        this.toolBlocks = {};
        this._stopSpinnerTimer();
        const spinner = document.getElementById('spinner-area');
        if (spinner) spinner.innerHTML = '';
        const todo = document.getElementById('todolist-area');
        if (todo) todo.style.display = 'none';
    },

    _appendSystemMessage(content) {
        this._removeWelcomeScreen();
        const c = document.getElementById('chat-messages');
        this._saveScrollState();
        const el = document.createElement('div');
        el.className = 'system-message';
        el.innerHTML = `<div class="msg-content" style="background:transparent;border:none;padding:4px 12px;display:inline-block;font-size:var(--text-sm);color:var(--text-3)">${Utils.escapeHtml(content)}</div>`;
        c.appendChild(el);
        this._scrollToBottom();
        return el;
    },

    /** 移除欢迎页面 */
    _removeWelcomeScreen() {
        const ws = document.querySelector('#chat-messages .welcome-screen');
        if (ws) ws.remove();
    },

    /** 显示欢迎页面(空状态) */
    _appendWelcomeScreen() {
        const c = document.getElementById('chat-messages');
        this._saveScrollState();
        const el = document.createElement('div');
        el.className = 'welcome-screen';
        const allSuggestions = [
            { icon: '💡', text: '你能做什么？' },
            { icon: '📝', text: '帮我写一封请假邮件' },
            { icon: '🌐', text: '翻译这段英文到中文' },
            { icon: '📋', text: '帮我制定一个学习计划' },
            { icon: '🧮', text: '帮我算一下房贷月供' },
            { icon: '📅', text: '帮我安排一下明天的行程' },
            { icon: '📖', text: '用简单的语言解释量子计算' },
            { icon: '🍎', text: '推荐几道简单的家常菜谱' },
            { icon: '✈️', text: '规划一个三天的旅行攻略' },
            { icon: '📝', text: '帮我写一篇朋友圈文案' },
            { icon: '💡', text: '给我讲一个有趣的冷知识' },
            { icon: '🎓', text: '推荐几本值得读的好书' },
            { icon: '🏋️', text: '制定一个健身减脂计划' },
            { icon: '💼', text: '帮我润色一下这段简历' },
            { icon: '🎬', text: '推荐几部好看的电影' },
            { icon: '🧹', text: '帮我整理一份待办清单' },
            { icon: '📊', text: '帮我分析一下这份数据' },
            { icon: '🤔', text: '用比喻解释什么是 AI' },
            { icon: '📧', text: '帮我写一封商务合作邮件' },
            { icon: '🎯', text: '帮我拆解一个复杂的任务' },
            { icon: '💬', text: '帮我写一段年终总结' },
            { icon: '🧪', text: '给我出几道脑筋急转弯' },
            { icon: '📰', text: '帮我写一段产品宣传文案' },
            { icon: '🌸', text: '推荐一个周末放松的方式' },
            { icon: '📝', text: '帮我写一篇读书笔记模板' },
            { icon: '🎤', text: '帮我准备一段自我介绍' },
            { icon: '🎨', text: '给我推荐几个配色方案' },
            { icon: '🛒', text: '帮我列一个年货采购清单' },
            { icon: '📱', text: '推荐几个好用的手机 App' },
            { icon: '🧳', text: '帮我整理出行李打包清单' },
            { icon: '🎵', text: '推荐一些适合工作时听的音乐' },
            { icon: '💭', text: '给我一些写日记的灵感' },
            { icon: '🔍', text: '帮我搜索一下这个问题的最新答案' },
            { icon: '🔊', text: '把这段话朗读给我听' },
            { icon: '⏰', text: '每天早上 9 点提醒我喝水' },
            { icon: '📸', text: '帮我识别一下这张图片里的内容' },
            { icon: '📑', text: '帮我记住这个知识点,以后能查到' },
            { icon: '🖥️', text: '帮我运行一下这段代码看看结果' },
            { icon: '📑', text: '帮我总结一下这个网页的内容' },
            { icon: '📐', text: '帮我截图并分析屏幕上的内容' },
            { icon: '📄', text: '帮我把这个文件发送给我' },
            { icon: '🧩', text: '搜索 GitHub 上类似的开源项目' },
            { icon: '📂', text: '帮我读取并分析这个文件' },
        ];
        // Fisher-Yates shuffle, pick first 4
        const shuffled = [...allSuggestions];
        for (let i = shuffled.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
        }
        const suggestions = shuffled.slice(0, 4);
        const tips = [
            '输入 ! 可直接执行 Shell 命令',
            '输入 / 可查看所有可用命令',
            '拖拽文件到输入框可添加附件',
            '按 Esc 可中断 AI 回复',
        ];
        const tip = tips[Math.floor(Math.random() * tips.length)];
        el.innerHTML = `
            <div class="welcome-logo">🦞</div>
            <div class="welcome-title">UniClaw</div>
            <div class="welcome-subtitle">你的 AI 智能助手,随时准备帮你解决问题</div>
            <div class="welcome-suggestions">
                ${suggestions.map(s => `
                    <div class="welcome-suggestion-card" data-text="${Utils.escapeHtml(s.text)}">
                        <span class="card-icon">${s.icon}</span>
                        <span>${Utils.escapeHtml(s.text)}</span>
                    </div>
                `).join('')}
            </div>
            <div class="welcome-tip">
                ${icon('sparkles')}
                <span>${Utils.escapeHtml(tip)}</span>
            </div>
        `;
        el.querySelectorAll('.welcome-suggestion-card').forEach(card => {
            card.addEventListener('click', () => this._fillInput(card.dataset.text));
        });
        c.appendChild(el);
        this._scrollToBottom();
        // 尝试获取公告
        this._fetchAnnouncement().catch(() => {});
        return el;
    },

    /** 从公告服务器获取公告内容(HTTPS 优先,失败回退 HTTP) */
    async _fetchAnnouncement() {
        // 去重:已有公告栏则跳过
        if (document.querySelector('.announcement-banner')) return;

        const host = 'uniclawai.top:8001';
        // no-cors 模式探测可达性(服务器无 CORS 头,ok/status 不可用,只能靠是否抛异常判断)
        let server = null;
        for (const proto of ['https', 'http']) {
            try {
                await fetch(`${proto}://${host}/`, { method: 'HEAD', mode: 'no-cors', signal: AbortSignal.timeout(2000) });
                server = `${proto}://${host}`;
                break;
            } catch {}
        }
        if (!server) return;

        // 再次检查(异步间隙中可能已有其他实例插入)
        if (document.querySelector('.announcement-banner')) return;

        // 创建 object 容器
        const banner = document.createElement('div');
        banner.className = 'announcement-banner';

        const obj = document.createElement('object');
        obj.data = `${server}/api/announcement`;
        obj.type = 'text/html';
        obj.style.cssText = 'width:100%;border:none;min-height:40px;';

        // 加载失败时隐藏
        obj.onerror = () => banner.remove();
        obj.onload = () => {
            // 调整高度适应内容
            try {
                obj.style.height = obj.contentDocument?.body?.scrollHeight + 'px';
            } catch (e) {}
        };

        banner.appendChild(obj);

        // 插入到聊天区域最顶部
        const chatMessages = document.getElementById('chat-messages');
        chatMessages.insertBefore(banner, chatMessages.firstChild);
    },

    /** 将文本填入输入框并聚焦 */
    _fillInput(text) {
        const input = document.getElementById('chat-input');
        if (!input) return;
        input.value = text;
        input.focus();
        input.dispatchEvent(new Event('input', { bubbles: true }));
    },

    _appendUserMessage(content) {
        const c = document.getElementById('chat-messages');
        this._saveScrollState();
        const el = document.createElement('div');
        el.className = 'message user';
        el.dataset.rawContent = content;
        el.innerHTML = `
            <div class="msg-avatar user">${icon('send')}</div>
            <div class="msg-body">
                <div class="msg-content"><button class="msg-delete-btn" onclick="Chat._onEditUserMessage(this)" title="删除并重新编辑">${icon('close')}</button><div class="msg-text-aligner"><div class="markdown-body">${Utils.renderMarkdown(content)}</div></div></div>
            </div>`;
        c.appendChild(el);
        Utils.addCopyButtons(el);
        this._scrollToBottom();
        return el;
    },

    _appendUserMessageWithImages(content, imageUrls) {
        const c = document.getElementById('chat-messages');
        this._saveScrollState();
        const el = document.createElement('div');
        el.className = 'message user';
        el.dataset.rawContent = content || '';
        let html = `<div class="msg-avatar user">${icon('send')}</div><div class="msg-body">`;
        if (content) html += `<div class="msg-content"><button class="msg-delete-btn" onclick="Chat._onEditUserMessage(this)" title="删除并重新编辑">${icon('close')}</button><div class="msg-text-aligner"><div class="markdown-body">${Utils.renderMarkdown(content)}</div></div></div>`;
        html += '<div class="image-grid">';
        imageUrls.forEach(url => { html += `<img src="${url}" onclick="Chat._showLightbox('${url}')" />`; });
        html += '</div>';
        html += '</div>';
        el.innerHTML = html;
        c.appendChild(el);
        Utils.addCopyButtons(el);
        this._scrollToBottom();
        return el;
    },

    /** 追加带多媒体附件的用户消息 */
    _appendUserMessageWithMedia(content, images, videos, audio, files) {
        const c = document.getElementById('chat-messages');
        this._saveScrollState();
        const el = document.createElement('div');
        el.className = 'message user';
        el.dataset.rawContent = content || '';
        let html = `<div class="msg-avatar user">${icon('send')}</div><div class="msg-body">`;
        if (content) html += `<div class="msg-content"><button class="msg-delete-btn" onclick="Chat._onEditUserMessage(this)" title="删除并重新编辑">${icon('close')}</button><div class="msg-text-aligner"><div class="markdown-body">${Utils.renderMarkdown(content)}</div></div></div>`;

        // 图片网格
        if (images.length > 0) {
            html += '<div class="image-grid">';
            images.forEach(url => { html += `<img src="${url}" onclick="Chat._showLightbox('${url}')" />`; });
            html += '</div>';
        }

        // 视频
        videos.forEach(url => {
            html += `<div class="media-attachment"><video controls preload="metadata" style="max-width:300px;max-height:200px;border-radius:var(--r-md)"><source src="${url}"></video></div>`;
        });

        // 音频
        audio.forEach(a => {
            html += `<div class="media-attachment"><audio controls preload="metadata"><source src="data:audio/${a.format};base64,${a.data}"></audio></div>`;
        });

        // 文件附件
        if (files.length > 0) {
            html += '<div class="file-attachments">';
            files.forEach(name => {
                html += `<div class="file-attachment-item">${Icons.file || '📄'}<span>${Utils.escapeHtml(name)}</span></div>`;
            });
            html += '</div>';
        }

        html += '</div>';
        el.innerHTML = html;
        c.appendChild(el);
        Utils.addCopyButtons(el);
        this._scrollToBottom();
        return el;
    },

    _appendAssistantMessage(content) {
        const c = document.getElementById('chat-messages');
        this._saveScrollState();
        const el = document.createElement('div');
        el.className = 'message assistant';
        const msgContent = document.createElement('div');
        msgContent.className = 'msg-content';
        const body = document.createElement('div');
        body.className = 'markdown-body';
        if (content) { body.innerHTML = Utils.renderMarkdown(content); Utils.addCopyButtons(body); }
        msgContent.appendChild(body);
        // 消息整体复制按钮
        const copyBtn = document.createElement('button');
        copyBtn.className = 'msg-copy-btn';
        copyBtn.innerHTML = icon('copy');
        copyBtn.title = '复制';
        if (!content || !content.trim()) copyBtn.style.display = 'none';
        copyBtn.onclick = async () => {
            const ok = await Utils.copyToClipboard(body.innerText);
            if (ok) {
                copyBtn.innerHTML = icon('check');
                copyBtn.classList.add('copied');
                setTimeout(() => {
                    copyBtn.innerHTML = icon('copy');
                    copyBtn.classList.remove('copied');
                }, 1500);
            }
        };
        msgContent.appendChild(copyBtn);
        el.innerHTML = `<div class="msg-avatar assistant">${icon('lobster')}</div><div class="msg-body"></div>`;
        el.querySelector('.msg-body').appendChild(msgContent);
        c.appendChild(el);
        this._scrollToBottom();
        return msgContent;
    },

    _appendThinkingBlock(parentEl, content, collapsed = true, beforeNode = null) {
        const block = document.createElement('div');
        block.className = 'thinking-block' + (collapsed ? '' : ' expanded');
        const charCount = content?.length || 0;
        block.innerHTML = `
            <div class="thinking-header">${icon('brain')} <span class="thinking-label">思考完成 (${charCount}字)</span></div>
            <div class="thinking-content">${Utils.escapeHtml(content)}</div>`;
        block.querySelector('.thinking-header').onclick = () => block.classList.toggle('expanded');
        if (beforeNode) parentEl.insertBefore(block, beforeNode);
        else parentEl.appendChild(block);
        return block;
    },

    _appendToolBlock(parentEl, name, args, content, success, toolCallId, explain) {
        const block = document.createElement('div');
        block.className = 'tool-block';
        if (toolCallId) {
            block.dataset.toolCallId = toolCallId;
            const key = this.currentSessionId ? `${this.currentSessionId}:${toolCallId}` : toolCallId;
            this.toolBlocks[key] = block;
        }
        const statusClass = success === false ? 'error' : success === null ? 'running' : 'done';
        const statusText = success === false ? '失败' : success === null ? '执行中' : '完成';
        const argPreview = Utils.formatArgs(args, 60);
        const resultPreview = content ? content.split('\n')[0] : '';

        let headerHtml = `<span class="tool-icon">${icon('tool')}</span>`;
        headerHtml += `<span class="tool-text">`;
        if (explain) headerHtml += `<span class="tool-explain">${Utils.escapeHtml(explain)}</span>`;
        headerHtml += `<span class="tool-name">${Utils.escapeHtml(name)}</span>`;
        if (argPreview) headerHtml += `<span class="tool-args-preview">(${Utils.escapeHtml(argPreview)})</span>`;
        if (resultPreview) headerHtml += `<span class="tool-result-preview">→ ${Utils.escapeHtml(resultPreview)}</span>`;
        headerHtml += `</span>`;
        headerHtml += `<span class="tool-status ${statusClass}">${statusText}</span><span class="tool-chevron">${icon('chevronRight')}</span>`;

        const header = document.createElement('div');
        header.className = 'tool-header';
        header.innerHTML = headerHtml;
        header.onclick = () => block.classList.toggle('expanded');

        const body = document.createElement('div');
        body.className = 'tool-body';
        if (args && args !== '{}') {
            body.innerHTML += `<div class="tool-args"><div class="tool-args-label">参数</div><pre>${Utils.escapeHtml(this._formatJson(args))}</pre></div>`;
        }
        if (name === 'Edit' && content) {
            body.innerHTML += `<div class="tool-result">${this._renderEditDiff(args, content)}</div>`;
        } else if (name === 'send_file' && content) {
            const fileHtml = this._renderFileDownload(content);
            if (fileHtml) {
                body.innerHTML += `<div class="tool-result"><div class="tool-result-label">输出</div>${fileHtml}</div>`;
            } else {
                body.innerHTML += `<div class="tool-result"><div class="tool-result-label">输出</div><pre>${Utils.escapeHtml(content)}</pre></div>`;
            }
        } else if (content) {
            body.innerHTML += `<div class="tool-result"><div class="tool-result-label">输出</div><pre>${Utils.escapeHtml(content)}</pre></div>`;
        }

        block.appendChild(header);
        block.appendChild(body);
        parentEl.appendChild(block);
        return block;
    },

    _renderEditDiff(args, content) {
        let oldText = '', newText = '';
        try {
            const parsed = typeof args === 'string' ? JSON.parse(args) : args;
            oldText = parsed.old_string || parsed.old_text || '';
            newText = parsed.new_string || parsed.new_text || '';
        } catch (_) { return `<pre>${Utils.escapeHtml(content)}</pre>`; }
        if (!oldText && !newText) return `<pre>${Utils.escapeHtml(content)}</pre>`;
        const escOld = Utils.escapeHtml(oldText).replace(/"/g, '&quot;');
        const escNew = Utils.escapeHtml(newText).replace(/"/g, '&quot;');
        let html = `<div class="tool-diff-toggle" data-diff-old="${escOld}" data-diff-new="${escNew}">`;
        html += `<button class="active" onclick="Chat._switchDiff(this,'unified')">Unified</button>`;
        html += `<button onclick="Chat._switchDiff(this,'split')">Split</button></div>`;
        html += `<div class="diff-body">${Utils.renderDiff(oldText, newText, 'unified')}</div>`;
        return html;
    },

    _switchDiff(btn, mode) {
        btn.parentElement.querySelectorAll('button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const ctrl = btn.parentElement;
        const body = ctrl.nextElementSibling;
        body.innerHTML = Utils.renderDiff(ctrl.dataset.diffOld || '', ctrl.dataset.diffNew || '', mode);
    },

    _appendUsageInfo(parentEl, inTokens, outTokens, modelName) {
        if (!inTokens && !outTokens && !modelName) return;
        const el = document.createElement('div');
        el.className = 'msg-tokens';
        const parts = [];
        if (modelName) parts.push(modelName);
        if (inTokens || outTokens) parts.push(`${this._fmtTk(inTokens)}→${this._fmtTk(outTokens)}`);
        el.textContent = parts.join(' · ');
        parentEl.appendChild(el);
    },

    _fmtTk(n) { return !n ? '0' : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`; },

    _showLightbox(url) {
        const lb = document.createElement('div');
        lb.className = 'lightbox';
        lb.innerHTML = `<img src="${url}" />`;
        lb.onclick = () => lb.remove();
        document.body.appendChild(lb);
    },

    // ============================================================
    //  流式输出
    // ============================================================

    _resetStreamingState() {
        this.streamingEl = null; this.streamingContent = ''; this.streamingBody = null;
        this.thinkingEl = null; this.thinkingContent = '';
        this._clearSubagentState();
    },

    _clearSubagentState() {
        this._subagentToolId = null;
        this._subagentName = '';
        this._subagentStreamingEl = null;
        this._subagentStreamingContent = '';
        this._subagentThinkingEl = null;
        this._subagentThinkingContent = '';
    },

    /** 获取当前 subagent 对应的 tool-block body 元素 */
    _getSubagentToolBody() {
        if (!this._subagentToolId || !this.currentSessionId) return null;
        const key = `${this.currentSessionId}:${this._subagentToolId}`;
        const block = this.toolBlocks[key];
        return block ? block.querySelector('.tool-body') : null;
    },

    _findLastRunningTool() {
        // 在 toolBlocks 中查找最近一个状态为 running 的非 subagent 工具块
        const prefix = this.currentSessionId ? `${this.currentSessionId}:` : '';
        let last = null;
        for (const [key, block] of Object.entries(this.toolBlocks)) {
            if (!key.startsWith(prefix)) continue;
            if (block.classList.contains('subagent-tool')) continue;
            const status = block.querySelector('.tool-header .tool-status');
            if (status && status.classList.contains('running')) {
                last = { toolCallId: key.slice(prefix.length) };
            }
        }
        return last;
    },

    // ============================================================
    //  辅助函数
    // ============================================================

    _extractText(content) {
        if (typeof content === 'string') return content;
        if (Array.isArray(content)) return content.filter(b => b.type === 'text').map(b => b.text).join('\n');
        return String(content || '');
    },
    _extractImages(content) {
        if (!Array.isArray(content)) return [];
        return content.filter(b => b.type === 'image_url').map(b => b.image_url.url);
    },
    _extractVideos(content) {
        if (!Array.isArray(content)) return [];
        return content.filter(b => b.type === 'video_url').map(b => b.video_url.url);
    },
    _extractAudio(content) {
        if (!Array.isArray(content)) return [];
        return content.filter(b => b.type === 'input_audio').map(b => ({ data: b.input_audio.data, format: b.input_audio.format }));
    },
    /** 提取附件文件(非多媒体的 [附件: ...] 文本块) */
    _extractFiles(content) {
        if (!Array.isArray(content)) return [];
        return content.filter(b => b.type === 'text' && /^\[附件: .+\]$/.test(b.text)).map(b => b.text.match(/^\[附件: (.+)\]$/)[1]);
    },
    _formatJson(str) {
        if (typeof str !== 'string') { try { return JSON.stringify(str, null, 2); } catch (_) { return String(str); } }
        try { return JSON.stringify(JSON.parse(str), null, 2); } catch (_) { return str; }
    },
    _saveScrollState() {
        const c = document.getElementById('chat-messages');
        if (!c) return;
        const threshold = Math.max(100, c.clientHeight * 0.2);
        this._wasAtBottom = c.scrollHeight - c.scrollTop - c.clientHeight < threshold;
    },
    _scrollToBottom() {
        if (!this._wasAtBottom) return;
        const c = document.getElementById('chat-messages');
        requestAnimationFrame(() => { c.scrollTop = c.scrollHeight; });
    },
    _forceScrollToBottom() {
        const c = document.getElementById('chat-messages');
        requestAnimationFrame(() => { c.scrollTop = c.scrollHeight; });
    },

    // ============================================================
    //  WS 事件处理器
    // ============================================================

    _onSessionCreated(msg) {
        this.currentSessionId = msg.session_id;
        SessionPanel.activeSessionId = msg.session_id;
        this._resetStreamingState();
        if (msg.root_dir) SessionPanel.activeProjectDir = msg.root_dir;
        WS.send({ type: 'set_active', session_id: msg.session_id });
        SessionPanel._updateStatusBar(SessionPanel.activeProjectDir, msg.session_id, true);
        SessionPanel._refreshSessions();
        // 检查是否有缓存的权限请求需要显示
        Permission.onSessionSwitched(msg.session_id);
    },

    _onUser(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;
        this._removeWelcomeScreen();
        // 收到用户消息确认,清空等待区
        if (typeof PendingMessages !== 'undefined') {
            PendingMessages.clear();
        }
        let el;
        if (Array.isArray(msg.content)) {
            const text = this._extractText(msg.content);
            const images = this._extractImages(msg.content);
            const videos = this._extractVideos(msg.content);
            const audio = this._extractAudio(msg.content);
            const files = this._extractFiles(msg.content);
            if (text.startsWith('[system]')) {
                // Shell 命令消息已由 _onShellResult 渲染(带格式化),跳过避免重复
                if (text.includes('(用户执行Shell命令)')) return;
                if (images.length > 0) { this._appendSystemMessageWithImages(text, images); return; }
                el = this._appendSystemMessage(text);
                if (el && msg.msg_idx != null) el.dataset.msgIdx = msg.msg_idx;
                return;
            }
            // 清除 [附件: ...] 后缀,只保留纯文本
            const cleanText = text.replace(/\n?\[附件: [^\]]+\]/g, '').trim();
            el = this._appendUserMessageWithMedia(cleanText, images, videos, audio, files);
        } else if (typeof msg.content === 'string') {
            const text = msg.content;
            if (text.startsWith('[system]')) {
                // Shell 命令消息已由 _onShellResult 渲染(带格式化),跳过避免重复
                if (text.includes('(用户执行Shell命令)')) return;
                el = this._appendSystemMessage(text);
            } else {
                el = this._appendUserMessage(text);
            }
        }
        // 后端广播的 msg_idx 赋给元素,供删除功能精确定位
        if (el && msg.msg_idx != null) el.dataset.msgIdx = msg.msg_idx;
    },

    /** 追加带图片的系统消息 */
    _appendSystemMessageWithImages(content, imageUrls) {
        const c = document.getElementById('chat-messages');
        this._saveScrollState();
        const el = document.createElement('div');
        el.className = 'system-message';
        let html = '';
        if (content) html += `<div style="font-size:var(--text-sm);color:var(--text-3);margin-bottom:4px">${Utils.escapeHtml(content)}</div>`;
        html += '<div class="image-grid">';
        imageUrls.forEach(url => { html += `<img src="${url}" onclick="Chat._showLightbox('${url}')" />`; });
        html += '</div>';
        el.innerHTML = html;
        c.appendChild(el);
        this._scrollToBottom();
        return el;
    },

    _onThinkingStart(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;

        // subagent 的 thinking 渲染在 tool-block 内
        if (msg.is_subagent && this._subagentToolId) {
            const body = this._getSubagentToolBody();
            // 新思考阶段开始时,重置流式内容容器(与主 agent 的 _appendAssistantMessage 行为一致)
            if (this._subagentStreamingEl) {
                this._subagentStreamingEl = null;
                this._subagentStreamingContent = '';
            }
            if (body && !this._subagentThinkingEl) {
                const block = document.createElement('div');
                block.className = 'thinking-block';
                const agentLabel = msg.agent_name || this._subagentName || 'subagent';
                block.innerHTML = `<div class="thinking-header">${icon('brain')} <span class="thinking-label">[${Utils.escapeHtml(agentLabel)}] 思考中...</span></div><div class="thinking-content"></div>`;
                block.querySelector('.thinking-header').onclick = () => block.classList.toggle('expanded');
                body.appendChild(block);
                this._subagentThinkingEl = block;
                this._subagentThinkingContent = '';
                this._scrollToBottom();
            }
            return;
        }

        if (this.thinkingEl) return;
        if (!this.streamingEl) {
            this.streamingEl = this._appendAssistantMessage('');
            this.streamingBody = this.streamingEl.querySelector('.markdown-body');
            this.streamingContent = '';
        }
        const block = document.createElement('div');
        block.className = 'thinking-block';
        block.innerHTML = `<div class="thinking-header">${icon('brain')} <span class="thinking-label">思考中...</span></div><div class="thinking-content"></div>`;
        block.querySelector('.thinking-header').onclick = () => block.classList.toggle('expanded');
        this.streamingEl.insertBefore(block, this.streamingEl.firstChild);
        this.thinkingEl = block;
        this.thinkingContent = '';
        this._scrollToBottom();
    },

    _onThinking(msg) {
        if (!msg || msg.session_id !== this.currentSessionId) return;

        // subagent 的 thinking 渲染在 tool-block 内
        if (msg.is_subagent && this._subagentThinkingEl) {
            this._saveScrollState();
            this._subagentThinkingContent += msg.content;
            const content = this._subagentThinkingEl.querySelector('.thinking-content');
            if (content) content.textContent = this._subagentThinkingContent;
            const label = this._subagentThinkingEl.querySelector('.thinking-label');
            const agentLabel = msg.agent_name || this._subagentName || 'subagent';
            if (label) label.textContent = `[${agentLabel}] 思考中... (${this._subagentThinkingContent.length}字)`;
            this._scrollToBottom();
            return;
        }

        if (!this.thinkingEl) return;
        this._saveScrollState();
        this.thinkingContent += msg.content;
        const content = this.thinkingEl.querySelector('.thinking-content');
        if (content) content.textContent = this.thinkingContent;
        const label = this.thinkingEl.querySelector('.thinking-label');
        if (label) label.textContent = `思考中... (${this.thinkingContent.length}字)`;
        this._scrollToBottom();
    },

    _onText(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;

        // subagent 的 text 渲染在 tool-block 内
        if (msg.is_subagent && this._subagentToolId) {
            const body = this._getSubagentToolBody();
            if (body) {
                // 完成 thinking 显示
                if (this._subagentThinkingEl) {
                    const label = this._subagentThinkingEl.querySelector('.thinking-label');
                    if (label) label.textContent = `[${msg.agent_name || this._subagentName || 'subagent'}] 思考完成 (${this._subagentThinkingContent.length}字)`;
                    this._subagentThinkingEl = null; this._subagentThinkingContent = '';
                }
                // 创建或更新流式内容区域
                if (!this._subagentStreamingEl) {
                    const el = document.createElement('div');
                    el.className = 'subagent-streaming markdown-body';
                    body.appendChild(el);
                    this._subagentStreamingEl = el;
                    this._subagentStreamingContent = '';
                }
                this._subagentStreamingContent += msg.content;
                this._subagentStreamingEl.innerHTML = Utils.renderMarkdown(this._subagentStreamingContent);
                Utils.addCopyButtons(this._subagentStreamingEl);
                this._scrollToBottom();
            }
            return;
        }

        this._saveScrollState();
        if (this.thinkingEl) {
            const label = this.thinkingEl.querySelector('.thinking-label');
            if (label) label.textContent = `思考完成 (${this.thinkingContent.length}字)`;
            this.thinkingEl = null; this.thinkingContent = '';
        }
        if (!this.streamingEl) {
            this.streamingEl = this._appendAssistantMessage('');
            this.streamingBody = this.streamingEl.querySelector('.markdown-body');
            this.streamingContent = '';
        }
        if (!this.streamingBody) {
            this.streamingBody = document.createElement('div');
            this.streamingBody.className = 'markdown-body';
            this.streamingEl.appendChild(this.streamingBody);
        }
        this.streamingContent += msg.content;
        this.streamingBody.innerHTML = Utils.renderMarkdown(this.streamingContent);
        Utils.addCopyButtons(this.streamingBody);
        const btn = this.streamingBody.parentElement?.querySelector('.msg-copy-btn');
        if (btn && btn.style.display === 'none' && this.streamingContent.trim()) btn.style.display = '';
        this._scrollToBottom();
    },

    _onAssistant(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;
        // subagent 的 assistant 事件不需要在主聊天区显示(流式内容已在 tool-block 内渲染)
        if (msg.is_subagent) return;
        if (this.thinkingEl) {
            const label = this.thinkingEl.querySelector('.thinking-label');
            if (label) label.textContent = `思考完成 (${this.thinkingContent.length}字)`;
            this.thinkingEl = null; this.thinkingContent = '';
        }
        if (!this.streamingEl && (msg.content || msg.tool_calls?.length)) {
            this.streamingEl = this._appendAssistantMessage('');
            this.streamingBody = this.streamingEl.querySelector('.markdown-body');
            this.streamingContent = '';
        }
        if (this.streamingEl && !this.streamingBody) {
            this.streamingBody = this.streamingEl.querySelector('.markdown-body');
            if (!this.streamingBody) { this.streamingBody = document.createElement('div'); this.streamingBody.className = 'markdown-body'; this.streamingEl.appendChild(this.streamingBody); }
        }
        if (this.streamingBody && msg.content) {
            this.streamingBody.innerHTML = Utils.renderMarkdown(msg.content);
            Utils.addCopyButtons(this.streamingBody);
            const btn = this.streamingBody.parentElement?.querySelector('.msg-copy-btn');
            if (btn && btn.style.display === 'none' && msg.content.trim()) btn.style.display = '';
        }
        if (msg.tool_calls?.length && this.streamingEl) {
            msg.tool_calls.forEach(tc => {
                const name = tc.function?.name || tc.name || 'tool';
                const args = tc.function?.arguments || tc.arguments || '{}';
                this._appendToolBlock(this.streamingEl, name, args, null, null, tc.id || '');
            });
        }
        if (this.streamingEl) this._appendUsageInfo(this.streamingEl, msg.in_tokens, msg.out_tokens, msg.model_name);
        this.streamingEl = null; this.streamingContent = ''; this.streamingBody = null;
    },

    _onToolStart(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;

        // 检测需要跟踪子智能体的工具调用
        if (!msg.is_subagent && msg.name === 'subagent_create') {
            let agentName = '';
            try {
                const args = typeof msg.args === 'string' ? JSON.parse(msg.args) : msg.args;
                agentName = args?.name || '';
            } catch (_) {}
            this._subagentToolId = msg.tool_call_id || null;
            this._subagentName = agentName;
        }

        // subagent 事件到达但 _subagentToolId 未设置:关联到最近一个正在执行的工具块
        if (msg.is_subagent && !this._subagentToolId) {
            const lastRunning = this._findLastRunningTool();
            if (lastRunning) {
                this._subagentToolId = lastRunning.toolCallId;
                this._subagentName = msg.agent_name || '';
            }
        }

        // subagent 的 tool 事件渲染在 tool-block 内
        if (msg.is_subagent && this._subagentToolId) {
            // 完成本轮 thinking 显示(与主 agent 的 tool_start 行为一致)
            if (this._subagentThinkingEl) {
                const label = this._subagentThinkingEl.querySelector('.thinking-label');
                if (label) label.textContent = `[${msg.agent_name || this._subagentName || 'subagent'}] 思考完成 (${this._subagentThinkingContent.length}字)`;
                this._subagentThinkingEl = null; this._subagentThinkingContent = '';
            }
            const body = this._getSubagentToolBody();
            if (body) {
                // 用 tool_call_id 做唯一 key,和主工具一样存入 toolBlocks
                const subKey = msg.tool_call_id ? `${msg.session_id}:${msg.tool_call_id}` : null;
                if (subKey && this.toolBlocks[subKey]) {
                    // 已存在(重复事件),更新状态
                    const existing = this.toolBlocks[subKey];
                    const status = existing.querySelector('.tool-header .tool-status');
                    if (status) { status.className = 'tool-status running'; status.textContent = '执行中'; }
                    return;
                }
                const agentLabel = msg.agent_name || this._subagentName || 'subagent';
                const el = document.createElement('div');
                el.className = 'tool-block subagent-tool';
                if (subKey) this.toolBlocks[subKey] = el;
                const header = document.createElement('div');
                header.className = 'tool-header';
                const argPreview = Utils.formatArgs(msg.args, 60);
                let headerHtml = `<span class="tool-icon">${icon('tool')}</span><span class="tool-text"><span class="tool-name">[${Utils.escapeHtml(agentLabel)}] ${Utils.escapeHtml(msg.name)}</span>`;
                if (argPreview) headerHtml += `<span class="tool-args-preview">(${Utils.escapeHtml(argPreview)})</span>`;
                headerHtml += `</span><span class="tool-status running">执行中</span><span class="tool-chevron">${icon('chevronRight')}</span>`;
                header.innerHTML = headerHtml;
                header.onclick = (e) => { e.stopPropagation(); el.classList.toggle('expanded'); };
                const toolBody = document.createElement('div');
                toolBody.className = 'tool-body';
                if (msg.args && Object.keys(msg.args).length) {
                    toolBody.innerHTML = `<div class="tool-args"><div class="tool-args-label">参数</div><pre>${Utils.escapeHtml(this._formatJson(msg.args))}</pre></div>`;
                }
                el.appendChild(header);
                el.appendChild(toolBody);
                body.appendChild(el);
            }
            return;
        }

        if (this.thinkingEl) {
            const label = this.thinkingEl.querySelector('.thinking-label');
            if (label) label.textContent = `思考完成 (${this.thinkingContent.length}字)`;
            this.thinkingEl = null; this.thinkingContent = '';
        }
        const key = msg.tool_call_id ? `${msg.session_id}:${msg.tool_call_id}` : null;
        const existing = key ? this.toolBlocks[key] : null;
        if (existing) {
            const header = existing.querySelector('.tool-header');
            if (header) {
                const status = header.querySelector('.tool-status');
                if (status) { status.className = 'tool-status running'; status.textContent = '执行中'; }
                // 更新 explain(从 AssistantEvent 创建时没有,tool_start 才带过来)
                if (msg.explain && !header.querySelector('.tool-explain')) {
                    const toolText = header.querySelector('.tool-text');
                    if (toolText) {
                        const explainEl = document.createElement('span');
                        explainEl.className = 'tool-explain';
                        explainEl.textContent = msg.explain;
                        const toolName = toolText.querySelector('.tool-name');
                        if (toolName) toolText.insertBefore(explainEl, toolName);
                        else toolText.appendChild(explainEl);
                    }
                }
            }
        } else {
            const parent = this.streamingEl || document.getElementById('chat-messages');
            this._appendToolBlock(parent, msg.name, msg.args, '执行中...', null, msg.tool_call_id, msg.explain);
        }

    },

    _onToolStream(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;
        const key = msg.tool_call_id ? `${msg.session_id}:${msg.tool_call_id}` : null;
        const block = key ? this.toolBlocks[key] : null;
        if (!block) return;

        const body = block.querySelector('.tool-body');
        if (!body) return;

        // 查找或创建流式输出容器
        let streamEl = body.querySelector('.tool-stream-output');
        if (!streamEl) {
            streamEl = document.createElement('div');
            streamEl.className = 'tool-stream-output';
            const pre = document.createElement('pre');
            pre.className = 'tool-stream-content';
            streamEl.appendChild(pre);
            // 插入到 tool-args 之后(如果有)
            const argsEl = body.querySelector('.tool-args');
            if (argsEl && argsEl.nextSibling) {
                body.insertBefore(streamEl, argsEl.nextSibling);
            } else {
                body.appendChild(streamEl);
            }
        }

        const pre = streamEl.querySelector('pre');
        // 判断用户是否在底部附近(距离底部 50px 以内)
        const isNearBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 50;
        // 处理 \r 回车:回退到当前行开头再覆盖,用于渲染进度条
        // \r\n 视为普通换行,单独 \r 清空当前行(进度条覆盖)
        if (!pre._streamBuf) { pre._streamBuf = ''; pre._lineBuf = ''; }
        for (let i = 0; i < msg.content.length; i++) {
            const ch = msg.content[i];
            if (ch === '\r' && msg.content[i + 1] === '\n') {
                pre._streamBuf += pre._lineBuf + '\n';
                pre._lineBuf = '';
                i++; // 跳过 \n
            } else if (ch === '\r') {
                pre._lineBuf = '';
            } else if (ch === '\n') {
                pre._streamBuf += pre._lineBuf + '\n';
                pre._lineBuf = '';
            } else {
                pre._lineBuf += ch;
            }
        }
        pre.textContent = pre._streamBuf + pre._lineBuf;
        // 如果已展开且用户在底部附近,则自动滚动到底部
        if (block.classList.contains('expanded') && isNearBottom) {
            pre.scrollTop = pre.scrollHeight;
        }
    },

    _onToolEnd(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;

        // subagent 的 tool_end: 用 tool_call_id 精确匹配工具块
        if (msg.is_subagent && this._subagentToolId) {
            const subKey = msg.tool_call_id ? `${msg.session_id}:${msg.tool_call_id}` : null;
            const toolBlock = subKey ? this.toolBlocks[subKey] : null;
            if (toolBlock) {
                const success = !(msg.content?.startsWith('[TOOL_ERROR]'));
                const status = toolBlock.querySelector('.tool-header .tool-status');
                if (status) { status.className = `tool-status ${success ? 'done' : 'error'}`; status.textContent = success ? '完成' : '失败'; }
                // 更新 result-preview
                const toolText = toolBlock.querySelector('.tool-header .tool-text');
                if (toolText && msg.content) {
                    let preview = toolText.querySelector('.tool-result-preview');
                    const resultPreview = msg.content.split('\n')[0];
                    if (resultPreview) {
                        if (!preview) { preview = document.createElement('span'); preview.className = 'tool-result-preview'; toolText.appendChild(preview); }
                        preview.textContent = `→ ${resultPreview}`;
                    }
                }
                // 填充 tool-body 内容(只追加一次)
                const toolBody = toolBlock.querySelector('.tool-body');
                if (toolBody && msg.content && !toolBody.querySelector('.tool-result')) {
                    const resultEl = document.createElement('div');
                    resultEl.className = 'tool-result';
                    resultEl.innerHTML = `<div class="tool-result-label">输出</div><pre>${Utils.escapeHtml(msg.content)}</pre>`;
                    toolBody.appendChild(resultEl);
                }
            }
            return;
        }

        // 主 agent 的 subagent_create 结束,清除 subagent 状态
        if (!msg.is_subagent && msg.name === 'subagent_create' && this._subagentToolId) {
            this._clearSubagentState();
        }

        const key = msg.tool_call_id ? `${msg.session_id}:${msg.tool_call_id}` : null;
        const block = key ? this.toolBlocks[key] : null;
        if (!block) return;
        const success = !(msg.content?.startsWith('[TOOL_ERROR]'));
        const header = block.querySelector('.tool-header');
        if (header) {
            const status = header.querySelector('.tool-status');
            if (status) { status.className = `tool-status ${success ? 'done' : 'error'}`; status.textContent = success ? '完成' : '失败'; }
            // 更新 result-preview
            const toolText = header.querySelector('.tool-text');
            if (toolText && msg.content) {
                let preview = toolText.querySelector('.tool-result-preview');
                const resultPreview = msg.content.split('\n')[0];
                if (resultPreview) {
                    if (!preview) { preview = document.createElement('span'); preview.className = 'tool-result-preview'; toolText.appendChild(preview); }
                    preview.textContent = `→ ${resultPreview}`;
                }
            }
        }
        const body = block.querySelector('.tool-body');
        if (body) {
            // 有子智能体内容:保留步骤,仅追加最终结果(清除中间流式输出)
            if (body.querySelector('.subagent-tool')) {
                const streamEl = body.querySelector('.tool-stream-output');
                if (streamEl) streamEl.remove();
                if (msg.content) {
                    const resultEl = document.createElement('div');
                    resultEl.className = 'tool-result';
                    resultEl.innerHTML = `<div class="tool-result-label">输出</div><pre>${Utils.escapeHtml(msg.content)}</pre>`;
                    body.appendChild(resultEl);
                }
            } else {
                body.innerHTML = '';
                if (msg.args && Object.keys(msg.args).length) body.innerHTML += `<div class="tool-args"><div class="tool-args-label">参数</div><pre>${Utils.escapeHtml(this._formatJson(msg.args))}</pre></div>`;
                // send_file 工具: 显示下载图标
                if (msg.name === 'send_file' && msg.content) {
                    const fileHtml = this._renderFileDownload(msg.content);
                    if (fileHtml) {
                        body.innerHTML += `<div class="tool-result"><div class="tool-result-label">输出</div>${fileHtml}</div>`;
                    } else {
                        body.innerHTML += `<div class="tool-result"><div class="tool-result-label">输出</div><pre>${Utils.escapeHtml(msg.content)}</pre></div>`;
                    }
                } else if (msg.name === 'Edit' && msg.content) {
                    body.innerHTML += `<div class="tool-result">${this._renderEditDiff(msg.args, msg.content)}</div>`;
                } else if (msg.content) {
                    body.innerHTML += `<div class="tool-result"><div class="tool-result-label">输出</div><pre>${Utils.escapeHtml(msg.content)}</pre></div>`;
                }
            }
        }
    },

    _onConfigChanged(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;
        fetch(`/api/config?session_id=${msg.session_id}`).then(r => r.json()).then(d => {
            const mel = document.getElementById('status-model');
            if (mel && d.model_name?.length) mel.textContent = d.model_name[0];
            const pel = document.getElementById('status-permission');
            if (pel && d.permission_mode) {
                const map = { auto: 'Auto', manual: 'Manual', 'accept-all': 'Accept All', plan: 'Plan' };
                pel.textContent = map[d.permission_mode] || d.permission_mode;
                pel.className = `perm-mode ${d.permission_mode}`;
            }
            this._renderTodolist(d.todolist);
            // 更新 EX 按钮状态
            const exEl = document.getElementById('status-explain');
            if (exEl) {
                if (d.explain_mode === true) {
                    exEl.className = 'status-explain active';
                    exEl.title = '工具解释模式: 所有工具 (/explain)';
                } else if (Array.isArray(d.explain_mode) && d.explain_mode.length > 0) {
                    exEl.className = 'status-explain partial';
                    exEl.title = `工具解释模式: ${d.explain_mode.join(', ')} (/explain)`;
                } else {
                    exEl.className = 'status-explain';
                    exEl.title = '工具解释模式: 关闭 (/explain)';
                }
            }
        }).catch(() => {});
    },

    _renderTodolist(todo) {
        const area = document.getElementById('todolist-area');
        if (!todo?.items?.length) { if (area) area.style.display = 'none'; return; }
        area.style.display = 'block';
        area.innerHTML = todo.items.map(item => {
            const cls = item.status === 'completed' ? 'completed' : item.status === 'in_progress' ? 'in_progress' : '';
            const ic = item.status === 'completed' ? icon('check') : item.status === 'in_progress' ? icon('play') : icon('circle');
            return `<div class="todolist-item ${cls}" style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:var(--text-sm);color:var(--text-1)"><span style="width:16px;height:16px">${ic}</span> ${Utils.escapeHtml(item.content)}</div>`;
        }).join('');
    },

    _fetchAndRenderTodolist(sid) {
        fetch(`/api/config?session_id=${sid}`).then(r => r.json()).then(d => {
            this._renderTodolist(d.todolist);
            // 更新 EX 按钮状态
            const exEl = document.getElementById('status-explain');
            if (exEl) {
                if (d.explain_mode === true) {
                    exEl.className = 'status-explain active';
                    exEl.title = '工具解释模式: 所有工具 (/explain)';
                } else if (Array.isArray(d.explain_mode) && d.explain_mode.length > 0) {
                    exEl.className = 'status-explain partial';
                    exEl.title = `工具解释模式: ${d.explain_mode.join(', ')} (/explain)`;
                } else {
                    exEl.className = 'status-explain';
                    exEl.title = '工具解释模式: 关闭 (/explain)';
                }
            }
        }).catch(() => { const a = document.getElementById('todolist-area'); if (a) a.style.display = 'none'; });
    },

    /** 解析 send_file 工具返回的文件下载标记,生成 HTML */
    _renderFileDownload(content) {
        const match = content.match(/\[file_download:([a-f0-9]+):(.+?):(\d+)\]/);
        if (!match) return null;
        const fileId = match[1];
        const fileName = match[2];
        const expiresAt = parseInt(match[3]);
        const now = Math.floor(Date.now() / 1000);
        const expired = now > expiresAt;
        const expireDate = new Date(expiresAt * 1000);
        const formattedTime = `${expireDate.getFullYear()}-${String(expireDate.getMonth() + 1).padStart(2, '0')}-${String(expireDate.getDate()).padStart(2, '0')} ${String(expireDate.getHours()).padStart(2, '0')}:${String(expireDate.getMinutes()).padStart(2, '0')}:${String(expireDate.getSeconds()).padStart(2, '0')}`;
        if (expired) {
            return `<div class="file-download expired"><span class="file-download-icon">📄</span><span class="file-download-name">${Utils.escapeHtml(fileName)}</span></div>`;
        }
        return `<div class="file-download" onclick="window.open('/api/files/download?file_id=${fileId}', '_blank')" title="点击下载\n有效期至: ${formattedTime}"><span class="file-download-icon">📄</span><span class="file-download-name">${Utils.escapeHtml(fileName)}</span></div>`;
    },

    _onEnd(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;
        this._resetStreamingState();
        SessionPanel._refreshSessions();
    },

    _onSubagentEnd(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;
        // 完成 subagent 的 thinking 显示
        if (this._subagentThinkingEl) {
            const label = this._subagentThinkingEl.querySelector('.thinking-label');
            if (label) label.textContent = `[${msg.agent_name || this._subagentName || 'subagent'}] 思考完成 (${this._subagentThinkingContent.length}字)`;
            this._subagentThinkingEl = null; this._subagentThinkingContent = '';
        }
        // 清理 subagent 流式状态(但保留 _subagentToolId 以便后续 tool_end 清理)
        this._subagentStreamingEl = null;
        this._subagentStreamingContent = '';
    },
    _onError(msg) { if (!msg || msg.session_id !== this.currentSessionId) return; this._appendSystemMessage(`❌ ${msg.message}`); },
    _onInterrupted(msg) { if (!msg || msg.session_id !== this.currentSessionId) return; this._resetStreamingState(); this._appendSystemMessage(`⏹️ ${msg.message || '已中断'}`); },

    _onShellResult(msg) {
        if (msg.source === 'console' || !msg || msg.session_id !== this.currentSessionId) return;
        const el = this._renderShellResult(msg.command || '', msg.output || '');
        if (el && msg.msg_idx >= 0) el.dataset.msgIdx = msg.msg_idx;
    },

    _appendShellResultFromHistory(text, msgIdx) {
        const lines = text.replace(/^\[system\]\s*\(用户执行Shell命令\)\s*\n?/, '').split('\n');
        let cmd = '', output = '';
        if (lines.length > 0 && lines[0].startsWith('$ ')) { cmd = lines[0].substring(2); output = lines.slice(1).join('\n'); }
        else output = lines.join('\n');
        const el = this._renderShellResult(cmd, output);
        if (el && msgIdx != null) el.dataset.msgIdx = msgIdx;
        return el;
    },

    _renderShellResult(cmd, output) {
        const c = document.getElementById('chat-messages');
        this._saveScrollState();
        const el = document.createElement('div');
        el.className = 'system-message';
        el.dataset.shellMsg = '1'; // 标记:对应后端一条 user 消息,计算删除数量时需计入
        el.innerHTML = `<div style="font-family:var(--font-mono);font-size:var(--text-sm);text-align:left;max-width:900px;margin:0 auto"><div style="color:var(--neon-cyan);margin-bottom:2px">$ ${Utils.escapeHtml(cmd)}</div><pre style="margin:0;white-space:pre-wrap;background:var(--bg-inset);padding:8px 12px;border-radius:var(--r-sm)">${Utils.escapeHtml(output)}</pre></div>`;
        c.appendChild(el);
        this._scrollToBottom();
        return el;
    },

    _onCommandOutput(msg) {
        if (!msg || msg.session_id !== this.currentSessionId) return;
        const c = document.getElementById('chat-messages');
        this._saveScrollState();
        const colors = { info: 'var(--text-2)', ok: 'var(--neon-green)', warn: 'var(--neon-orange)', err: 'var(--neon-pink)' };
        const el = document.createElement('div');
        el.className = 'system-message';
        el.innerHTML = `<pre style="margin:0;white-space:pre-wrap;color:${colors[msg.level] || 'var(--text-2)'};font-family:var(--font-mono);font-size:var(--text-sm)">${Utils.escapeHtml(msg.content || '')}</pre>`;
        c.appendChild(el);
        this._scrollToBottom();
    },

    _onCommandResult(msg) {
        if (!msg || msg.session_id !== this.currentSessionId || !msg.output) return;
        const c = document.getElementById('chat-messages');
        this._saveScrollState();
        const el = document.createElement('div');
        el.className = 'system-message';
        el.innerHTML = `<div style="font-family:var(--font-mono);font-size:var(--text-sm)"><div style="color:var(--text-3);margin-bottom:2px">/${Utils.escapeHtml(msg.command || '')}</div><pre style="margin:0;white-space:pre-wrap">${Utils.escapeHtml(msg.output)}</pre></div>`;
        c.appendChild(el);
        this._scrollToBottom();
    },

    _spinnerTimer: null,
    _spinnerChars: ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'],

    _onSpinner(msg) {
        if (!msg || msg.session_id !== this.currentSessionId) return;
        const area = document.getElementById('spinner-area');
        let line = area.querySelector(`[data-wid="${msg.wait_id}"]`);
        if (!line) {
            line = document.createElement('div');
            line.className = 'spinner-content';
            line.dataset.wid = msg.wait_id;
            line.dataset.frame = '0';
            line.dataset.text = msg.text;
            line.dataset.startTime = Date.now().toString();
            area.appendChild(line);
        }
        line.dataset.text = msg.text;
        this._ensureSpinnerTimer();
    },

    _onSpinnerStop(msg) {
        if (!msg || msg.session_id !== this.currentSessionId) return;
        const area = document.getElementById('spinner-area');
        const line = area.querySelector(`[data-wid="${msg.wait_id}"]`);
        if (line) line.remove();
        if (!area.children.length) this._stopSpinnerTimer();
    },

    _ensureSpinnerTimer() {
        if (this._spinnerTimer) return;
        this._spinnerTimer = setInterval(() => {
            const area = document.getElementById('spinner-area');
            if (!area?.children.length) { this._stopSpinnerTimer(); return; }
            for (const line of area.children) {
                let frame = parseInt(line.dataset.frame || '0');
                const char = this._spinnerChars[frame % this._spinnerChars.length];
                line.dataset.frame = ((frame + 1) % this._spinnerChars.length).toString();
                const elapsed = this._fmtDur(Date.now() - parseInt(line.dataset.startTime || '0'));
                line.innerHTML = `<span class="spinner-frames">${char}</span> ${Utils.escapeHtml(line.dataset.text || '')} <span class="spinner-elapsed">${elapsed}</span>`;
            }
        }, 100);
    },

    _fmtDur(ms) {
        const s = ms / 1000;
        if (s < 1) return `${ms}ms`;
        if (s < 60) return `${s.toFixed(1)}s`;
        if (s < 3600) return `${Math.floor(s / 60)}m${Math.floor(s % 60)}s`;
        return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
    },

    _stopSpinnerTimer() { if (this._spinnerTimer) { clearInterval(this._spinnerTimer); this._spinnerTimer = null; } },

    /** 编辑用户消息:删除该消息及之后的所有消息,将内容放入输入框 */
    async _onEditUserMessage(btn) {
        const msgEl = btn.closest('.message.user');
        if (!msgEl) return;

        const content = msgEl.dataset.rawContent || '';

        const sid = this.currentSessionId;
        if (!sid) return;

        const msgIdx = parseInt(msgEl.dataset.msgIdx, 10);
        if (isNaN(msgIdx)) {
            Utils.showToast('消息索引未知,请刷新页面后重试', 'warn');
            return;
        }

        if (!confirm('将删除此消息及后续所有消息,确认？')) return;

        const source = this._currentView === 'history' ? 'history' : 'messages';

        try {
            const resp = await fetch(`/api/sessions/${sid}/messages`, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ from_idx: msgIdx, source }),
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                Utils.showToast(err.detail || '删除失败', 'error');
                return;
            }

            // 重新加载会话数据并刷新视图,确保前端与后端完全同步
            try {
                const sessResp = await fetch(`/api/sessions/${sid}`);
                if (sessResp.ok) {
                    const data = await sessResp.json();
                    this._historyData = data.history || null;
                    this._compactData = data.messages || null;
                    const toggle = document.getElementById('history-toggle');
                    const hasDiff = this._historyData && this._compactData && this._historyData.length !== this._compactData.length;
                    if (toggle) toggle.style.display = hasDiff ? '' : 'none';
                    this._renderCurrentView();
                }
            } catch (_) { /* 刷新失败不影响删除结果 */ }

            // 将内容放入输入框
            const input = document.getElementById('chat-input');
            if (input) {
                input.value = content;
                input.focus();
                input.style.height = 'auto';
                input.style.height = Math.min(input.scrollHeight, 150) + 'px';
            }
        } catch (e) {
            Utils.showToast('删除失败: ' + e.message, 'error');
        }
    },
};
