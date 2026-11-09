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
    _subagentToolId: null,        // 最近一个 subagent_create 的 tool_call_id(缺 creator 时的兜底)
    _subagentName: "",            // 当前 subagent 名称(兜底显示用)
    _subagentStates: {},          // creatorToolCallId -> 该子代理的流式/思考状态

    // agent 视图切换(主 Agent + 各 subagent 独立时间线)
    _agentViews: {},              // key('main' | 'session:creatorToolCallId') -> 视图对象
    _activeAgentKey: 'main',      // 当前显示视图

    init() {
        document.getElementById('history-toggle')?.addEventListener('click', () => {
            this._switchView(this._currentView === 'history' ? 'compact' : 'history');
        });
        // 状态栏 agent 切换(主 Agent / 各 subagent 菜单)
        const agEl = document.getElementById('status-agents');
        if (agEl) agEl.onclick = (e) => { e.stopPropagation(); this._showAgentMenu(agEl); };
        document.addEventListener('click', () => this._hideAgentMenu());
        // 初始化主视图,保证视图状态中 'main' 始终有对应对象
        this._agentViews['main'] = this._createAgentView('main', '主 Agent');
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
        WS.on('shell_running', msg => this._onShellRunning(msg));
        WS.on('shell_result', msg => this._onShellResult(msg));
        WS.on('command_output', msg => this._onCommandOutput(msg));
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
        // 加载代际: 只有最新一次加载才允许关遮罩。用 sessionId 判断不可靠 —
        // 会话删除/新建会把 currentSessionId 置空或改指新会话, 在途加载的 finally
        // 会误判"已过期"而跳过 hideLoading, 遮罩永久卡死(overlay 无引用计数)。
        const gen = this._loadGen = (this._loadGen || 0) + 1;
        this.currentSessionId = sessionId;
        this.toolBlocks = {};
        this._resetAgentViews();
        this._historyData = null;
        this._compactData = null;
        try {
            Utils.showLoading('加载历史消息...');
            const resp = await fetch(`/api/sessions/${sessionId}`);
            if (!resp.ok) {
                if (this.currentSessionId !== sessionId) return;
                const err = await resp.json().catch(() => ({ detail: resp.statusText }));
                this.clear(); this._appendSystemMessage(`加载失败: ${err.detail || resp.statusText}`); return;
            }
            const data = await resp.json();
            // 竞态守卫: 快速切换会话时,过期响应不得渲染
            if (this.currentSessionId !== sessionId) return;
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
            if (this.currentSessionId !== sessionId) return;
            this.clear(); this._appendSystemMessage(`加载失败: ${e.message}`);
        } finally {
            // 有更新的 loadHistory 在途时不能关掉它仍在用的遮罩; 会话删除/新建后
            // currentSessionId 被置空但 _loadGen 未变, 此时由本次加载负责关遮罩
            if (this._loadGen === gen) Utils.hideLoading();
        }
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
        // 视图整体重建, 丢弃旧 DOM 关联的流式/子代理状态, 否则残留的 streamingEl/thinkingEl
        // 指向已移除节点, 后续 _onText 等会把增量写进"看不见的元素", 界面停止更新
        this._resetStreamingState();
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
                // 用户消息不显示时间戳
            } else if (role === 'assistant') {
                const el = this._appendAssistantMessage('');
                el.closest('.message').dataset.msgIdx = msgIdx;
                const body = el.querySelector('.markdown-body');
                // 后端 reasoning_content 为空时会填单个空格(Anthropic 协议要求非空),回放时不渲染空思考块
                if (msg.reasoning_content && msg.reasoning_content.trim()) this._appendThinkingBlock(el, msg.reasoning_content, true, body);
                if (body && msg.content) {
                    body.innerHTML = Utils.renderMarkdown(msg.content);
                    if (typeof A2UI !== 'undefined') A2UI.enhance(body); // 回放路径: a2ui 代码块 → 声明式组件(仅主 agent)
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
                this._appendUsageInfo(el, msg.usage?.input_tokens, msg.usage?.output_tokens, msg.model_name, msg.usage?.cached_tokens, msg.usage?.cache_write_tokens, msg.created_at, msg.finish_reason);
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

    _appendSystemMessage(content, container) {
        if (container) this._removeWelcomeScreen(); // 子代理视图无欢迎页, 仅主视图需要清理
        const c = container || document.getElementById('chat-messages');
        this._saveScrollState(c);
        const el = document.createElement('div');
        el.className = 'system-message';
        el.innerHTML = `<div class="msg-content" style="background:transparent;border:none;padding:4px 12px;display:inline-block;font-size:var(--text-sm);color:var(--text-3)">${Utils.escapeHtml(content)}</div>`;
        c.appendChild(el);
        this._scrollToBottom(c);
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

    _appendUserMessage(content, container) {
        const c = container || document.getElementById('chat-messages');
        this._saveScrollState(c);
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
        this._scrollToBottom(c);
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
        html += '</div>';
        el.innerHTML = html;
        this._appendImageGrid(el.querySelector('.msg-body'), imageUrls);
        c.appendChild(el);
        Utils.addCopyButtons(el);
        this._scrollToBottom();
        return el;
    },

    /** 把图片网格以 DOM API 挂到 parent 下。url 不拼进 HTML 字符串, 防止逃逸属性注入脚本 */
    _appendImageGrid(parent, imageUrls) {
        const grid = document.createElement('div');
        grid.className = 'image-grid';
        (imageUrls || []).forEach(url => {
            const img = document.createElement('img');
            img.src = url;
            img.onclick = () => this._showLightbox(url);
            grid.appendChild(img);
        });
        parent.appendChild(grid);
    },

    /** 追加带多媒体附件的用户消息 */
    _appendUserMessageWithMedia(content, images, videos, audio, files, container) {
        const c = container || document.getElementById('chat-messages');
        this._saveScrollState(c);
        const el = document.createElement('div');
        el.className = 'message user';
        el.dataset.rawContent = content || '';
        let html = `<div class="msg-avatar user">${icon('send')}</div><div class="msg-body">`;
        if (content) html += `<div class="msg-content"><button class="msg-delete-btn" onclick="Chat._onEditUserMessage(this)" title="删除并重新编辑">${icon('close')}</button><div class="msg-text-aligner"><div class="markdown-body">${Utils.renderMarkdown(content)}</div></div></div>`;

        html += '</div>';
        el.innerHTML = html;
        const bodyEl = el.querySelector('.msg-body');

        // 顺序保持与原实现一致: 图片 → 视频 → 音频 → 文件
        // 图片网格
        if (images.length > 0) this._appendImageGrid(bodyEl, images);

        // 视频
        videos.forEach(url => {
            const wrap = document.createElement('div');
            wrap.className = 'media-attachment';
            const video = document.createElement('video');
            video.controls = true;
            video.preload = 'metadata';
            video.style.cssText = 'max-width:300px;max-height:200px;border-radius:var(--r-md)';
            const vsource = document.createElement('source');
            vsource.src = url;
            video.appendChild(vsource);
            wrap.appendChild(video);
            bodyEl.appendChild(wrap);
        });

        // 音频
        audio.forEach(a => {
            const wrap = document.createElement('div');
            wrap.className = 'media-attachment';
            const audioEl = document.createElement('audio');
            audioEl.controls = true;
            audioEl.preload = 'metadata';
            const asource = document.createElement('source');
            const fmt = String(a.format || '').replace(/[^a-zA-Z0-9]/g, '');
            asource.src = `data:audio/${fmt};base64,${a.data}`;
            audioEl.appendChild(asource);
            wrap.appendChild(audioEl);
            bodyEl.appendChild(wrap);
        });

        // 文件附件
        if (files.length > 0) {
            const fileWrap = document.createElement('div');
            fileWrap.className = 'file-attachments';
            files.forEach(name => {
                const item = document.createElement('div');
                item.className = 'file-attachment-item';
                item.innerHTML = Icons.file || '📄';
                const label = document.createElement('span');
                label.textContent = name;
                item.appendChild(label);
                fileWrap.appendChild(item);
            });
            bodyEl.appendChild(fileWrap);
        }

        c.appendChild(el);
        Utils.addCopyButtons(el);
        this._scrollToBottom(c);
        return el;
    },

    _appendAssistantMessage(content, container) {
        const c = container || document.getElementById('chat-messages');
        this._saveScrollState(c);
        const el = document.createElement('div');
        el.className = 'message assistant';
        const msgContent = document.createElement('div');
        msgContent.className = 'msg-content';
        const body = document.createElement('div');
        body.className = 'markdown-body';
        if (content) { body.innerHTML = Utils.renderMarkdown(content); if (typeof A2UI !== 'undefined') A2UI.enhance(body); Utils.addCopyButtons(body); }
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
        this._scrollToBottom(c);
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
        // subagent_create: 提供"查看运行过程"的显眼入口, 点击切换到对应子代理独立视图
        const subViewKey = toolCallId ? `${this.currentSessionId || ''}:${toolCallId}` : '';
        if (name === 'subagent_create' && subViewKey && this._agentViews[subViewKey]) {
            headerHtml += `<button class="tool-view-subagent" title="查看 ${Utils.escapeHtml(this._agentViews[subViewKey].name || '子代理')} 的运行过程" data-view-key="${Utils.escapeHtml(subViewKey)}">${icon('play')} 查看过程</button>`;
        }

        const header = document.createElement('div');
        header.className = 'tool-header';
        header.innerHTML = headerHtml;
        header.onclick = (e) => {
            if (e.target.closest('.tool-view-subagent')) return; // 查看按钮不触发展开/收起
            block.classList.toggle('expanded');
        };
        header.querySelector('.tool-view-subagent')?.addEventListener('click', (e) => {
            e.stopPropagation();
            this._switchAgentView(e.currentTarget.dataset.viewKey);
        });

        const body = document.createElement('div');
        body.className = 'tool-body';
        if (args && args !== '{}') {
            body.innerHTML += `<div class="tool-args"><div class="tool-args-label">参数</div><pre>${Utils.escapeHtml(this._formatJson(args))}</pre></div>`;
        }
        if (name === 'Edit' && content) {
            body.innerHTML += `<div class="tool-result">${this._renderEditDiff(args, content)}</div>`;
        } else if (name === 'send_file' && content) {
            // send_file: 文本文件渲染 Markdown,其他文件显示下载图标
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
        // 工具块也是可见内容: 若父消息的复制按钮还是隐藏态(空消息创建), 恢复显示
        if (parentEl && parentEl.classList && parentEl.classList.contains('msg-content')) {
            const btn = parentEl.querySelector('.msg-copy-btn');
            if (btn && btn.style.display === 'none') btn.style.display = '';
        }
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
        const escOld = Utils.escapeHtml(oldText);
        const escNew = Utils.escapeHtml(newText);
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

    _appendUsageInfo(parentEl, inTokens, outTokens, modelName, cachedTokens, cacheWriteTokens, createdAt, finishReason) {
        if (!inTokens && !outTokens && !modelName && !cachedTokens && !cacheWriteTokens && !createdAt && !finishReason) return;
        if (inTokens || outTokens || modelName || cachedTokens || cacheWriteTokens || finishReason) {
            const el = document.createElement('div');
            el.className = 'msg-tokens';
            const parts = [];
            if (modelName) parts.push(modelName);
            if (inTokens || outTokens) parts.push(`${this._fmtTk(inTokens)}→${this._fmtTk(outTokens)}`);
            // 缓存字段仅 OpenRouter 等提供商返回;缺失或为 0 时不展示,保持原显示
            if (cachedTokens > 0) parts.push(`缓存 ${this._fmtTk(cachedTokens)}`);
            if (cacheWriteTokens > 0) parts.push(`写 ${this._fmtTk(cacheWriteTokens)}`);
            // 停止原因,便于观察模型是正常结束还是被截断
            if (finishReason) parts.push(`finish:${finishReason}`);
            el.textContent = parts.join(' · ');
            parentEl.appendChild(el);
        }
        if (createdAt) {
            const ts = document.createElement('span');
            ts.className = 'msg-time';
            ts.textContent = this._fmtTime(createdAt);
            ts.title = (typeof createdAt === 'string' ? new Date(createdAt) : new Date(createdAt * 1000)).toLocaleString('zh-CN', { hour12: false });
            parentEl.appendChild(ts);
        }
    },

    _fmtTk(n) { return !n ? '0' : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`; },

    _fmtTime(ts) {
        if (!ts) return '';
        const d = typeof ts === 'string' ? new Date(ts) : new Date(ts * 1000);
        return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    },

    // 累计一条消息的缓存统计(实时路径用 in_tokens/cached_tokens,回放路径用 usage.*)


_showLightbox(url) {
        const lb = document.createElement('div');
        lb.className = 'lightbox';
        const img = document.createElement('img');
        img.src = url;
        lb.appendChild(img);
        lb.onclick = () => lb.remove();
        document.body.appendChild(lb);
    },

    // ============================================================
    //  流式输出
    // ============================================================

    _resetStreamingState() {
        // 结束/切会话时若还停在思考中, 先收尾(空内容移除, 否则改写标题), 避免残留"思考中..."
        this._finalizeThinking(this);
        this.streamingEl = null; this.streamingContent = ''; this.streamingBody = null;
        this.thinkingEl = null; this.thinkingContent = '';
        this._clearSubagentState();
        this._cleanShellPlaceholders();
    },

    /** 移除 shell 执行占位行: 传 sessionId 只清该会话的(事件级清理); 不传清全部(视图重建/切会话) */
    _cleanShellPlaceholders(sessionId) {
        document.querySelectorAll('.shell-running-placeholder').forEach(el => {
            if (sessionId === undefined || el.dataset.session === sessionId) el.remove();
        });
    },

    _clearSubagentState() {
        this._subagentToolId = null;
        this._subagentName = '';
        this._subagentStates = {};
    },

    // ============================================================
    //  agent 视图管理(主 Agent + 各 subagent 独立时间线)
    // ============================================================

    /** 计算 subagent 事件归属的视图 key('main' 之外的子代理视图)。
     *  解析逻辑与 _resolveSubagentBlockKey 一致: 优先后端 creator id, 缺失退化兜底。 */
    _agentKeyForMsg(msg) {
        if (!msg || !msg.is_subagent) return 'main';
        let creatorId = msg.creator_tool_call_id || null;
        if (!creatorId) {
            if (!this._subagentToolId) {
                const lastRunning = this._findLastRunningTool();
                if (lastRunning) {
                    this._subagentToolId = lastRunning.toolCallId;
                    this._subagentName = msg.agent_name || '';
                }
            }
            creatorId = this._subagentToolId;
        }
        if (!creatorId) return null; // 无法定位 → 不渲染(防止事件漂流进主视图)
        return `${msg.session_id}:${creatorId}`;
    },

    /** 获取(或创建)某个 subagent 的独立视图。name 用于展示与标签。 */
    _ensureAgentView(key, name) {
        if (!key || key === 'main') return this._agentViews['main'] || (this._agentViews['main'] = this._createAgentView('main', '主 Agent'));
        if (this._agentViews[key]) {
            if (name) this._agentViews[key].name = name;
            return this._agentViews[key];
        }
        const view = this._createAgentView(key, name || this._subagentName || '子代理');
        this._updateAgentStatusBar();
        return view;
    },

    /** 创建单个 agent 视图(容器 + 标签 + 该视图自己的流式状态)。
     *  主视图(key='main')直接复用 #chat-messages,不创建额外容器。
     *  status: 'running' | 'completed' | 'failed' | 'cancelled'(终态由 subagent_end 设置) */
    _createAgentView(key, name) {
        if (key === 'main') {
            return {
                key, name: '主 Agent',
                el: document.getElementById('chat-messages'),
                streamingEl: null, streamingContent: '', streamingBody: null,
                thinkingEl: null, thinkingContent: '',
                done: false, status: 'running',
            };
        }
        const mainArea = document.getElementById('main-area');
        const c = document.getElementById('chat-messages');
        // 独立容器与主聊天区同构(同一套 .chat-messages 样式),插在主容器之前
        const el = document.createElement('div');
        el.className = 'chat-messages agent-chat';
        el.id = `agent-chat-${key.replace(/[^a-zA-Z0-9_-]/g, '_')}`;
        el.style.display = 'none'; // 默认显示主 agent
        mainArea.insertBefore(el, c);
        const view = {
            key, name, el,
            streamingEl: null, streamingContent: '', streamingBody: null,
            thinkingEl: null, thinkingContent: '',
            done: false, status: 'running',
        };
        this._agentViews[key] = view;
        return view;
    },

    /** 更新状态栏 agent 切换项: 有子代理(含运行中的异步子代理)时显示, 否则隐藏 */
    _updateAgentStatusBar() {
        const el = document.getElementById('status-agents');
        if (!el) return;
        const subKeys = Object.keys(this._agentViews).filter(k => k !== 'main');
        if (!subKeys.length) { el.style.display = 'none'; return; }
        el.style.display = '';
        const labelEl = el.querySelector('.status-agents-label');
        const cur = this._agentViews[this._activeAgentKey];
        if (labelEl) labelEl.textContent = cur?.name || '主 Agent';
        // 有运行中的子代理时标题提示
        const running = subKeys.some(k => this._agentViews[k] && !this._agentViews[k].done);
        el.title = running ? '子代理运行中, 点击切换查看' : '点击切换查看 Agent / 子代理';
    },

    /** 切换当前显示的 agent 视图 */
    _switchAgentView(key) {
        if (!this._agentViews[key] && key !== 'main') {
            this._ensureAgentView(key);
        }
        this._activeAgentKey = key;
        const mainEl = document.getElementById('chat-messages');
        // 主容器恢复默认(移除内联 display, 由 .chat-messages 样式决定), 子代理容器显隐切换
        if (mainEl) mainEl.style.display = '';
        for (const [k, v] of Object.entries(this._agentViews)) {
            if (k === 'main') continue; // 主容器就是 #chat-messages
            if (!v?.el) continue;
            v.el.style.display = (k === key) ? '' : 'none';
        }
        if (key !== 'main') {
            // 隐藏主容器, 显示目标子代理视图
            if (mainEl) mainEl.style.display = 'none';
        }
        this._updateAgentStatusBar();
        this._forceScrollToBottom();
    },

    /** 标记某个 agent 视图结束(subagent_end / 主 end) */
    _markAgentDone(key) {
        const v = key === 'main' ? this._agentViews['main'] : this._agentViews[key];
        if (v) v.done = true;
        this._updateAgentStatusBar();
    },

    /** 标记某个 agent 视图恢复运行(子代理重新被唤醒跑新一轮时调用)。
     *  幂等: 仅当上一轮已收尾(done=true)时重置, 避免每个流式 chunk 都触发 UI 更新。
     *  closed 状态不重置 —— 被 subagent_close 关闭的任务无法被重新唤醒。
     *  否则上轮结束时 done=true/status=completed 会残留, 菜单一直显示"完成"
     *  而不会回到"运行中"。 */
    _markAgentActive(key) {
        const v = key === 'main' ? this._agentViews['main'] : this._agentViews[key];
        if (!v || !v.done || v.status === 'closed') return;
        v.done = false;
        v.status = 'running';
        this._updateAgentStatusBar();
    },

    /** 弹出 agent 切换菜单(status-permission 同款 context-menu) */
    _showAgentMenu(anchorEl) {
        this._hideAgentMenu();
        const menu = document.createElement('div');
        menu.id = 'agent-switch-menu';
        menu.className = 'context-menu';
        const rect = anchorEl.getBoundingClientRect();
        menu.style.cssText = `position:fixed;bottom:${window.innerHeight - rect.top + 4}px;left:${rect.left}px;z-index:1000;min-width:170px`;
        const keys = ['main', ...Object.keys(this._agentViews).filter(k => k !== 'main')];
        keys.forEach(key => {
            const v = this._agentViews[key];
            const name = key === 'main' ? '主 Agent' : (v?.name || '子代理');
            const item = document.createElement('div');
            item.className = 'context-menu-item' + (key === this._activeAgentKey ? ' active' : '');
            // 运行中的子代理: 名字旁加跳动圆点已经结束: 右侧标注状态徽标
            const running = key !== 'main' && v && !v.done;
            if (running) {
                item.innerHTML = `<span class="agent-tab-dot"></span><span>${Utils.escapeHtml(name)}</span><span style="margin-left:auto;color:var(--text-3);font-size:11px">运行中</span>`;
            } else if (key !== 'main' && v) {
                const badge = this._subagentStatusBadge(v.status);
                item.innerHTML = `<span class="ctx-icon"></span><span>${Utils.escapeHtml(name)}</span><span style="margin-left:6px">${badge}</span>${key === this._activeAgentKey ? '<span style="margin-left:auto;color:var(--accent-bright)">✓</span>' : ''}`;
            } else {
                item.innerHTML = `<span class="ctx-icon"></span><span>${Utils.escapeHtml(name)}</span>${key === this._activeAgentKey ? '<span style="margin-left:auto;color:var(--accent-bright)">✓</span>' : ''}`;
            }
            item.onclick = () => { this._switchAgentView(key); this._hideAgentMenu(); };
            menu.appendChild(item);
        });
        document.body.appendChild(menu);
        setTimeout(() => document.addEventListener('click', this._hideAgentMenu, { once: true }), 0);
    },

    /** 生成子代理状态徽标。主状态三态: 运行中 / ✓ 完成 / ⏻ 已关闭。
     *  失败/取消归入"完成"主状态, 用子标注区分细节(不引入第五种主状态)。 */
    _subagentStatusBadge(status) {
        if (status === 'closed') return '<span class="agent-status-badge closed" title="已被 subagent_close 关闭">⏻ 已关闭</span>';
        if (status === 'failed') return '<span class="agent-status-badge completed fail-mark" title="执行失败">✓ 完成(失败)</span>';
        if (status === 'cancelled') return '<span class="agent-status-badge completed cancel-mark" title="已取消">✓ 完成(取消)</span>';
        return '<span class="agent-status-badge completed" title="已完成">✓ 完成</span>';
    },

    _hideAgentMenu() {
        const m = document.getElementById('agent-switch-menu');
        if (m) m.remove();
    },

    /** 重置 agent 视图(会话切换/清空时),移除所有独立容器与标签 */
    _resetAgentViews() {
        Object.keys(this._agentViews).forEach(k => {
            if (k === 'main') return;
            this._agentViews[k].el?.remove();
            delete this._agentViews[k];
        });
        this._agentViews['main'] = {
            key: 'main', name: '主 Agent',
            el: document.getElementById('chat-messages'),
            streamingEl: null, streamingContent: '', streamingBody: null,
            thinkingEl: null, thinkingContent: '',
            done: false,
        };
        this._activeAgentKey = 'main';
        const c = this._agentViews['main'].el;
        if (c) c.style.display = '';
        this._updateAgentStatusBar();
    },

    /** 获取(或创建)某个创建者工具块对应的子代理流式状态。
     *  同步子代理(嵌套显示)复用此状态渲染 thinking/流式文本到创建它的工具块内。 */
    _subagentState(key) {
        return this._subagentStates[key] || (this._subagentStates[key] = {
            name: '', streamingEl: null, streamingContent: '',
            thinkingEl: null, thinkingContent: '',
        });
    },

    /** 解析同步子代理事件应挂载的父工具块 key(session_id:creator_tool_call_id)。
     *  优先后端精确携带的 creator id; 缺失时退化为旧的"最近 running 块"兜底。
     *  仅同步子代理使用(异步子代理走 _agentKeyForMsg → 独立视图)。 */
    _resolveSubagentBlockKey(msg) {
        let creatorId = msg.creator_tool_call_id || null;
        if (!creatorId) {
            if (!this._subagentToolId) {
                const lastRunning = this._findLastRunningTool();
                if (lastRunning) {
                    this._subagentToolId = lastRunning.toolCallId;
                    this._subagentName = msg.agent_name || '';
                }
            }
            creatorId = this._subagentToolId;
        }
        if (!creatorId) return null;
        const key = `${msg.session_id}:${creatorId}`;
        return this.toolBlocks[key] ? key : null;
    },

    /** 按父工具块 key 获取 subagent 的 tool-block body 元素(同步子代理嵌套渲染用) */
    _getSubagentToolBody(key) {
        if (!key) return null;
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
    _activeChatContainer() {
        // 返回当前应当滚动的聊天容器: 子代理视图激活时滚动其容器, 否则滚动主容器
        if (this._activeAgentKey !== 'main') {
            const v = this._agentViews[this._activeAgentKey];
            if (v?.el) return v.el;
        }
        return document.getElementById('chat-messages');
    },
    /* 记录滚动状态。target 显式指定时基于该容器,否则基于当前激活容器。
     * subagent 事件在后台(非激活视图)渲染时必须传入其 view.el ——
     * 否则会读取主/激活容器的滚动位置,把可见视图的滚动条拉走。 */
    _saveScrollState(target) {
        const c = target || this._activeChatContainer();
        if (!c) return;
        const threshold = Math.max(100, c.clientHeight * 0.2);
        this._wasAtBottom = c.scrollHeight - c.scrollTop - c.clientHeight < threshold;
    },
    _scrollToBottom(target) {
        if (!this._wasAtBottom) return;
        const c = target || this._activeChatContainer();
        if (!c) return;
        requestAnimationFrame(() => { c.scrollTop = c.scrollHeight; });
    },
    _forceScrollToBottom(target) {
        const c = target || this._activeChatContainer();
        if (!c) return;
        requestAnimationFrame(() => { c.scrollTop = c.scrollHeight; });
    },

    // ============================================================
    //  WS 事件处理器
    // ============================================================

    _onSessionCreated(msg) {
        // notify_only: 其他页面创建的会话,只刷新侧栏列表(由 SessionPanel 处理),不劫持本页面的当前会话
        if (msg.notify_only) return;
        this.currentSessionId = msg.session_id;
        SessionPanel.activeSessionId = msg.session_id;
        this._resetStreamingState();
        this._resetAgentViews();
        if (msg.root_dir) SessionPanel.activeProjectDir = msg.root_dir;
        WS.send({ type: 'set_active', session_id: msg.session_id });
        SessionPanel._updateStatusBar(SessionPanel.activeProjectDir, msg.session_id, true);
        SessionPanel._refreshSessions();
        // 检查是否有缓存的权限请求需要显示
        Permission.onSessionSwitched(msg.session_id);
    },

    _onUser(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;
        // subagent 的 user 消息: 同步走老办法(不显示, 与旧行为一致), 异步渲染到其独立视图
        if (msg.is_subagent) {
            if (!msg.is_async) return;
            const key = this._agentKeyForMsg(msg);
            const view = key ? this._ensureAgentView(key, msg.agent_name) : null;
            if (!view) return;
            if (msg.agent_name) view.name = msg.agent_name;
            // 子代理重新唤醒时收到新用户消息(任务提示): 标记回到"运行中"
            this._markAgentActive(key);
            let el = null;
            if (Array.isArray(msg.content)) {
                const text = this._extractText(msg.content);
                const images = this._extractImages(msg.content);
                const videos = this._extractVideos(msg.content);
                const audio = this._extractAudio(msg.content);
                const files = this._extractFiles(msg.content);
                if (text.startsWith('[system]')) {
                    if (text.includes('(用户执行Shell命令)')) return;
                    if (images.length > 0) { el = this._appendSystemMessageWithImages(text, images, view.el); return; }
                    el = this._appendSystemMessage(text, view.el);
                    return;
                }
                // 清除 [附件: ...] 后缀,只保留纯文本
                const cleanText = text.replace(/\n?\[附件: [^\]]+\]/g, '').trim();
                if (images.length > 0 || videos.length > 0 || audio.length > 0 || files.length > 0) {
                    el = this._appendUserMessageWithMedia(cleanText, images, videos, audio, files, view.el);
                } else {
                    el = this._appendUserMessage(cleanText || '(附件消息)', view.el);
                }
            } else if (typeof msg.content === 'string') {
                const text = msg.content;
                if (text.startsWith('[system]')) {
                    if (text.includes('(用户执行Shell命令)')) return;
                    el = this._appendSystemMessage(text, view.el);
                } else {
                    el = this._appendUserMessage(text, view.el);
                }
            }
            // subagent 视图是实时过程展示(不映射主会话历史), 消息无删除/编辑意义, 移除删除按钮
            if (el) el.querySelector('.msg-delete-btn')?.remove();
            return;
        }

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
        // 用户消息不显示时间戳
    },

    /** 追加带图片的系统消息 */
    _appendSystemMessageWithImages(content, imageUrls, container) {
        const c = container || document.getElementById('chat-messages');
        this._saveScrollState(c);
        const el = document.createElement('div');
        el.className = 'system-message';
        if (content) {
            const text = document.createElement('div');
            text.style.cssText = 'font-size:var(--text-sm);color:var(--text-3);margin-bottom:4px';
            text.textContent = content;
            el.appendChild(text);
        }
        this._appendImageGrid(el, imageUrls);
        c.appendChild(el);
        this._scrollToBottom(c);
        return el;
    },

    _onThinkingStart(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;

        // subagent: 同步走老办法(嵌套在创建工具块内), 异步走独立视图
        if (msg.is_subagent) {
            if (!msg.is_async) {
                const key = this._resolveSubagentBlockKey(msg);
                if (!key) return;
                const body = this._getSubagentToolBody(key);
                const st = this._subagentState(key);
                if (msg.agent_name) st.name = msg.agent_name;
                // 新思考阶段开始时,重置流式内容容器(与主 agent 的 _appendAssistantMessage 行为一致)
                st.streamingEl = null;
                st.streamingContent = '';
                if (body && !st.thinkingEl) {
                    const block = document.createElement('div');
                    block.className = 'thinking-block';
                    const agentLabel = msg.agent_name || st.name || 'subagent';
                    block.innerHTML = `<div class="thinking-header">${icon('brain')} <span class="thinking-label">[${Utils.escapeHtml(agentLabel)}] 思考中...</span></div><div class="thinking-content"></div>`;
                    block.querySelector('.thinking-header').onclick = () => block.classList.toggle('expanded');
                    body.appendChild(block);
                    st.thinkingEl = block;
                    st.thinkingContent = '';
                    this._scrollToBottom();
                }
                return;
            }
            const key = this._agentKeyForMsg(msg);
            if (!key) return;
            const view = this._ensureAgentView(key, msg.agent_name);
            if (msg.agent_name) view.name = msg.agent_name;
            // 新一轮开始: 重置为"运行中"(幂等, 上轮结束态才真正触发)
            this._markAgentActive(key);
            // 新思考阶段开始: 与主 agent 一致, 先建一条助手消息承载本轮回复
            if (!view.streamingEl) {
                view.streamingEl = this._appendAssistantMessage('', view.el);
                view.streamingBody = view.streamingEl.querySelector('.markdown-body');
                view.streamingContent = '';
            }
            if (!view.thinkingEl) {
                const block = document.createElement('div');
                block.className = 'thinking-block';
                const agentLabel = msg.agent_name || view.name || '子代理';
                block.innerHTML = `<div class="thinking-header">${icon('brain')} <span class="thinking-label">[${Utils.escapeHtml(agentLabel)}] 思考中...</span></div><div class="thinking-content"></div>`;
                block.querySelector('.thinking-header').onclick = () => block.classList.toggle('expanded');
                view.streamingEl.insertBefore(block, view.streamingEl.firstChild);
                view.thinkingEl = block;
                view.thinkingContent = '';
                this._scrollToBottom(view.el);
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

        // subagent: 同步走老办法(嵌套), 异步走独立视图
        if (msg.is_subagent) {
            if (!msg.is_async) {
                const key = this._resolveSubagentBlockKey(msg);
                const st = key ? this._subagentStates[key] : null;
                if (!st || !st.thinkingEl) return;
                this._saveScrollState();
                st.thinkingContent += msg.content;
                const content = st.thinkingEl.querySelector('.thinking-content');
                if (content) content.textContent = st.thinkingContent;
                const label = st.thinkingEl.querySelector('.thinking-label');
                const agentLabel = msg.agent_name || st.name || 'subagent';
                if (label) label.textContent = `[${agentLabel}] 思考中... (${st.thinkingContent.length}字)`;
                this._scrollToBottom();
                return;
            }
            const key = this._agentKeyForMsg(msg);
            const view = key ? this._ensureAgentView(key, msg.agent_name) : null;
            if (!view || !view.thinkingEl) return;
            this._saveScrollState(view.el);
            view.thinkingContent += msg.content;
            const content = view.thinkingEl.querySelector('.thinking-content');
            if (content) content.textContent = view.thinkingContent;
            const label = view.thinkingEl.querySelector('.thinking-label');
            const agentLabel = msg.agent_name || view.name || '子代理';
            if (label) label.textContent = `[${agentLabel}] 思考中... (${view.thinkingContent.length}字)`;
            this._scrollToBottom(view.el);
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

    /** 结束一个思考块: 有内容则改写标题为"思考完成 (N字)", 无内容则整块移除。
     *  非推理模型每轮仍会收到 ThinkingStartEvent 创建空块, 若不移除会显示"思考完成 (0字)",
     *  而回放路径(_appendThinkingBlock)对空 reasoning_content 会跳过, 造成刷新前后不一致。
     *  用 trim 判定与回放路径对齐: Anthropic 协议会为空 reasoning 填单个空格, 不 trim 会保留成"1字"。 */
    _finalizeThinking(st, agentName) {
        if (!st || !st.thinkingEl) return;
        if (!st.thinkingContent.trim()) {
            st.thinkingEl.remove();
        } else {
            const label = st.thinkingEl.querySelector('.thinking-label');
            const prefix = agentName ? `[${agentName}] ` : '';
            if (label) label.textContent = `${prefix}思考完成 (${st.thinkingContent.length}字)`;
        }
        st.thinkingEl = null;
        st.thinkingContent = '';
    },

    _onText(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;

        // subagent: 同步走老办法(嵌套在创建工具块内), 异步走独立视图
        if (msg.is_subagent) {
            if (!msg.is_async) {
                const key = this._resolveSubagentBlockKey(msg);
                if (!key) return;
                const body = this._getSubagentToolBody(key);
                const st = this._subagentState(key);
                if (msg.agent_name) st.name = msg.agent_name;
                if (body) {
                    // 完成 thinking 显示
                    this._finalizeThinking(st, msg.agent_name || st.name || 'subagent');
                    // 创建或更新流式内容区域
                    if (!st.streamingEl) {
                        const el = document.createElement('div');
                        el.className = 'subagent-streaming markdown-body';
                        body.appendChild(el);
                        st.streamingEl = el;
                        st.streamingContent = '';
                    }
                    st.streamingContent += msg.content;
                    st.streamingEl.innerHTML = Utils.renderMarkdown(st.streamingContent);
                    // A2UI 仅主 agent 可用:子代理的 a2ui 代码块保持代码块显示
                    Utils.addCopyButtons(st.streamingEl);
                    this._scrollToBottom();
                }
                return;
            }
            const key = this._agentKeyForMsg(msg);
            if (!key) return;
            const view = this._ensureAgentView(key, msg.agent_name);
            if (msg.agent_name) view.name = msg.agent_name;
            // 新一轮文本: 若上轮已结束, 标记回到"运行中"
            this._markAgentActive(key);
            // 完成 thinking 显示
            this._finalizeThinking(view, msg.agent_name || view.name || '子代理');
            // 创建或更新助手消息
            if (!view.streamingEl) {
                view.streamingEl = this._appendAssistantMessage('', view.el);
                view.streamingBody = view.streamingEl.querySelector('.markdown-body');
                view.streamingContent = '';
            }
            if (!view.streamingBody) {
                view.streamingBody = document.createElement('div');
                view.streamingBody.className = 'markdown-body';
                view.streamingEl.appendChild(view.streamingBody);
            }
            view.streamingContent += msg.content;
            view.streamingBody.innerHTML = Utils.renderMarkdown(view.streamingContent);
            // A2UI 仅主 agent 可用:子代理的 a2ui 代码块保持代码块显示
            Utils.addCopyButtons(view.streamingBody);
            const btn = view.streamingBody.parentElement?.querySelector('.msg-copy-btn');
            if (btn && btn.style.display === 'none' && view.streamingContent.trim()) btn.style.display = '';
            this._scrollToBottom(view.el);
            return;
        }

        this._saveScrollState();
        this._finalizeThinking(this);
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
        if (typeof A2UI !== 'undefined') A2UI.enhance(this.streamingBody); // 主 agent 流式: a2ui 代码块 → 声明式组件
        Utils.addCopyButtons(this.streamingBody);
        const btn = this.streamingBody.parentElement?.querySelector('.msg-copy-btn');
        if (btn && btn.style.display === 'none' && this.streamingContent.trim()) btn.style.display = '';
        this._scrollToBottom();
    },

    _onAssistant(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;
        // subagent: 同步走老办法(不做额外收束, 内容已在嵌套工具块内流式呈现), 异步收束独立视图
        if (msg.is_subagent) {
            if (!msg.is_async) return;
            const key = this._agentKeyForMsg(msg);
            const view = key ? this._ensureAgentView(key, msg.agent_name) : null;
            if (!view) return;
            if (msg.agent_name) view.name = msg.agent_name;
            // 新一轮 assistant: 若上轮已结束, 标记回到"运行中"(下轮 subagent_end 再收尾)
            this._markAgentActive(key);
            this._finalizeThinking(view, msg.agent_name || view.name || '子代理');
            if (!view.streamingEl && (msg.content || msg.tool_calls?.length)) {
                view.streamingEl = this._appendAssistantMessage('', view.el);
                view.streamingBody = view.streamingEl.querySelector('.markdown-body');
                view.streamingContent = '';
            }
            if (view.streamingBody && msg.content) {
                view.streamingBody.innerHTML = Utils.renderMarkdown(msg.content);
                Utils.addCopyButtons(view.streamingBody);
                const btn = view.streamingBody.parentElement?.querySelector('.msg-copy-btn');
                if (btn && btn.style.display === 'none' && msg.content.trim()) btn.style.display = '';
            }
            if (msg.tool_calls?.length && view.streamingEl) {
                msg.tool_calls.forEach(tc => {
                    const name = tc.function?.name || tc.name || 'tool';
                    const args = tc.function?.arguments || tc.arguments || '{}';
                    this._appendToolBlock(view.streamingEl, name, args, null, null, tc.id || '');
                });
            }
            if (view.streamingEl) this._appendUsageInfo(view.streamingEl, msg.in_tokens, msg.out_tokens, msg.model_name, msg.cached_tokens, msg.cache_write_tokens, msg.created_at, msg.finish_reason);
            view.streamingEl = null; view.streamingContent = ''; view.streamingBody = null;
            return;
        }
        // 会话级缓存统计(实时路径;subagent 不回放入主会话历史,故此处跳过以保持一致)
        this._finalizeThinking(this);
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
            if (typeof A2UI !== 'undefined') A2UI.enhance(this.streamingBody); // assistant 最终消息: a2ui 代码块 → 声明式组件
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
        if (this.streamingEl) this._appendUsageInfo(this.streamingEl, msg.in_tokens, msg.out_tokens, msg.model_name, msg.cached_tokens, msg.cache_write_tokens, msg.created_at, msg.finish_reason);
        this.streamingEl = null; this.streamingContent = ''; this.streamingBody = null;
    },

    _onToolStart(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;

        // 仅为 subagent_create 做两件事: 1) 从 args.name 初始化子代理显示名;
        // 2) 维护单槽兜底 _subagentToolId(仅供缺 creator_tool_call_id 的事件使用)。
        // 仅异步子代理(wait=false)提前创建独立视图; 同步子代理保持老办法(嵌套在工具块内)。
        if (!msg.is_subagent && msg.name === 'subagent_create') {
            let agentName = '';
            let isWait = true;
            try {
                const args = typeof msg.args === 'string' ? JSON.parse(msg.args) : msg.args;
                agentName = args?.name || '';
                isWait = args?.wait !== false;
            } catch (_) {}
            this._subagentToolId = msg.tool_call_id || null;
            this._subagentName = agentName;
            if (msg.tool_call_id) {
                const vkey = `${msg.session_id}:${msg.tool_call_id}`;
                this._subagentState(vkey).name = agentName;
                if (!isWait) this._ensureAgentView(vkey, agentName);
            }
        }

        // subagent: 同步走老办法(嵌套在创建工具块内), 异步走独立视图
        if (msg.is_subagent) {
            if (!msg.is_async) {
                const parentKey = this._resolveSubagentBlockKey(msg);
                if (!parentKey) return;
                const st = this._subagentState(parentKey);
                if (msg.agent_name) st.name = msg.agent_name;
                // 完成本轮 thinking 显示(与主 agent 的 tool_start 行为一致)
                this._finalizeThinking(st, msg.agent_name || st.name || 'subagent');
                const body = this._getSubagentToolBody(parentKey);
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
                    const agentLabel = msg.agent_name || st.name || 'subagent';
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
            const viewKey = this._agentKeyForMsg(msg);
            if (!viewKey) return;
            const view = this._ensureAgentView(viewKey, msg.agent_name);
            if (msg.agent_name) view.name = msg.agent_name;
            // 新一轮工具调用: 若上轮已结束, 标记回到"运行中"
            this._markAgentActive(viewKey);
            // 完成本轮 thinking 显示(与主 agent 的 tool_start 行为一致)
            this._finalizeThinking(view, msg.agent_name || view.name || '子代理');
            // 关键: 先查 toolBlocks — _onAssistant 已按 tool_calls 渲染过该工具块时,
            // 只能更新状态, 不能重复创建(否则同一 tool_call_id 出现两块: 一块卡死"执行中")
            const subKey = msg.tool_call_id ? `${msg.session_id}:${msg.tool_call_id}` : null;
            const existingBlock = subKey ? this.toolBlocks[subKey] : null;
            if (existingBlock) {
                const status = existingBlock.querySelector('.tool-header .tool-status');
                if (status) { status.className = 'tool-status running'; status.textContent = '执行中'; }
                // 更新 explain(从 AssistantEvent 创建时没有,tool_start 才带过来, 与主 agent 一致)
                if (msg.explain && !existingBlock.querySelector('.tool-explain')) {
                    const toolText = existingBlock.querySelector('.tool-text');
                    if (toolText) {
                        const explainEl = document.createElement('span');
                        explainEl.className = 'tool-explain';
                        explainEl.textContent = msg.explain;
                        const toolName = toolText.querySelector('.tool-name');
                        if (toolName) toolText.insertBefore(explainEl, toolName);
                        else toolText.appendChild(explainEl);
                    }
                }
                return;
            }
            // 工具块挂到该视图当前助手消息下(与主 agent 相同结构);缺消息时先建一条
            if (!view.streamingEl) {
                view.streamingEl = this._appendAssistantMessage('', view.el);
                view.streamingBody = view.streamingEl.querySelector('.markdown-body');
                view.streamingContent = '';
            }
            const parent = view.streamingEl || view.el;
            // 注意: content 传 null — 若传 '执行中...' 会在 tool-body 预置一个伪结果块,
            // _onToolEnd 的 subagent 分支按"只追加一次"判断会被其挡住, 真实结果无法填充
            this._appendToolBlock(parent, msg.name, msg.args, null, null, msg.tool_call_id, msg.explain);
            this._scrollToBottom(view.el);
            return;
        }

        this._finalizeThinking(this);
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
        // 控制台风格: 普通文本追加, \r 覆盖当前行, \n 换行
        if (pre._streamBuf == null) { pre._streamBuf = ''; pre._lineBuf = ''; }
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

        // 仅同步 subagent(嵌套在创建工具块内)走此分支;
        // 异步 subagent 的工具块与主 agent 同构(独立视图中), 直接复用下方主逻辑,
        // 自动获得 send_file/Edit diff/上下文刷新等完整能力, 避免两份逻辑漂移。
        if (msg.is_subagent && !msg.is_async) {
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
        // 异步 subagent 工具结果已进入模型上下文,由下方主逻辑统一刷新上下文用量

        // 主 agent 工具结束: 清除本工具块的子代理状态桶(覆盖 investigate/ask_advisor/kg_extract 等所有创建子代理的工具)
        if (!msg.is_subagent && msg.tool_call_id) {
            delete this._subagentStates[`${msg.session_id}:${msg.tool_call_id}`];
            // 单槽兜底指针可能是任意工具(经 _findLastRunningTool 赋值), 按 id 匹配清除而非限定工具名
            if (this._subagentToolId === msg.tool_call_id) {
                this._subagentToolId = null;
                this._subagentName = '';
            }
            // 注意: 不在这里移除 _agentViews 的子代理视图 — 同步子代理在工具内联等待完成时
            // 会在 tool_end 到达前标记 done, 此时删除会让用户刚切换到一半就看不到过程;
            // 视图统一在会话切换(_resetAgentViews)时清理, 运行中与完成后均可随时回看。
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
                // send_file 工具: 文本文件渲染 Markdown,其他文件显示下载图标
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
        // 工具结果已进入模型上下文,刷新上下文用量
        SessionPanel._fetchContextUsage(this.currentSessionId);
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

    /** 解析 send_file 工具返回的标记:
     *  [show_doc:...] → 文本文件,异步拉取内容并渲染为 Markdown
     *  [file_download:...] → 其他文件,显示下载图标
     */
    _renderFileDownload(content) {
        // 文本文件预览标记
        const docMatch = content.match(/^\[show_doc:([a-f0-9]+):(.+?):(\d+)\]/);
        if (docMatch) return this._renderShowDoc(docMatch[1], docMatch[2], parseInt(docMatch[3]));

        // 普通下载标记
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

    /** 渲染文本文件预览(fileId, fileName, expiresAt),异步拉取内容并渲染为 Markdown */
    _renderShowDoc(fileId, fileName, expiresAt) {
        const now = Math.floor(Date.now() / 1000);
        const expired = now > expiresAt;
        const uid = `showdoc-${fileId}-${Date.now()}`;

        let html = `<div class="show-doc" id="${uid}">`;
        html += `<div class="show-doc-header">📄 ${Utils.escapeHtml(fileName)}${expired ? '<span class="show-doc-expired">(链接已过期)</span>' : ''}</div>`;
        if (expired) {
            html += `<div class="show-doc-body"><div class="show-doc-error">链接已过期,无法显示内容</div></div>`;
        } else {
            html += `<div class="show-doc-body"><div class="show-doc-loading">加载中...</div></div>`;
        }
        html += `</div>`;

        if (!expired) {
            const token = localStorage.getItem('uniclaw_token');
            const headers = token ? { 'Authorization': 'Bearer ' + token } : {};
            fetch(`/api/files/download?file_id=${fileId}`, { headers })
                .then(r => { if (!r.ok) throw new Error('load failed'); return r.text(); })
                .then(text => {
                    const body = document.getElementById(uid)?.querySelector('.show-doc-body');
                    if (!body) return;
                    const rendered = Utils.renderMarkdown(text || '');
                    body.innerHTML = rendered || '<div class="show-doc-error">(空文件)</div>';
                    Utils.addCopyButtons(body);
                })
                .catch(() => {
                    const body = document.getElementById(uid)?.querySelector('.show-doc-body');
                    if (body) body.innerHTML = '<div class="show-doc-error">内容加载失败</div>';
                });
        }

        return html;
    },

    _onEnd(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;
        this._resetStreamingState();
        SessionPanel._refreshSessions();
        SessionPanel._fetchContextUsage(this.currentSessionId);
    },

    _onSubagentEnd(msg) {
        if (!msg || !this.currentSessionId || msg.session_id !== this.currentSessionId) return;
        // 同步子代理: 老办法, 收尾嵌套在创建工具块内的流式状态
        if (!msg.is_async) {
            const key = this._resolveSubagentBlockKey(msg);
            const st = key ? this._subagentStates[key] : null;
            if (!st) return;
            this._finalizeThinking(st, msg.agent_name || st.name || 'subagent');
            st.streamingEl = null;
            st.streamingContent = '';
            return;
        }
        // 异步子代理: 收尾独立视图流式状态(保留视图与标签,供用户回看;会话切换时才清理)
        const key = this._agentKeyForMsg(msg);
        const view = key ? this._agentViews[key] : null;
        if (!view) return;
        this._finalizeThinking(view, msg.agent_name || view.name || '子代理');
        view.streamingEl = null;
        view.streamingContent = '';
        view.streamingBody = null;
        // 记录终态: 被 subagent_close 主动关闭 → "closed", 否则用后端携带的终态
        // (默认 completed 兼容旧事件缺该字段)
        view.status = msg.closed ? 'closed' : (msg.status || 'completed');
        this._markAgentDone(key);
    },
    // 协议错误(如缺 root_dir/session_id)不带 session_id,需放行才能显示
    _onError(msg) { if (!msg || (msg.session_id && msg.session_id !== this.currentSessionId)) return; this._appendSystemMessage(`❌ ${msg.message}`); },
    _onInterrupted(msg) {
        if (!msg || (msg.session_id && msg.session_id !== this.currentSessionId)) return;
        // subagent 被中断: 同步走老办法(嵌套收尾), 异步渲染到独立视图
        if (msg.is_subagent) {
            if (!msg.is_async) {
                const key = this._resolveSubagentBlockKey(msg);
                const st = key ? this._subagentStates[key] : null;
                if (st) {
                    this._finalizeThinking(st, msg.agent_name || st.name || 'subagent');
                    st.streamingEl = null;
                    st.streamingContent = '';
                }
                return;
            }
            const key = this._agentKeyForMsg(msg);
            const view = key ? this._agentViews[key] : null;
            if (view) {
                this._finalizeThinking(view, msg.agent_name || view.name || '子代理');
                this._appendSystemMessage(`⏹️ ${msg.message || '已中断'}`, view.el);
                view.streamingEl = null; view.streamingContent = ''; view.streamingBody = null;
                view.status = 'cancelled';
                this._markAgentDone(key);
            }
            return;
        }
        this._resetStreamingState(); this._appendSystemMessage(`⏹️ ${msg.message || '已中断'}`);
    },

    // agent 运行期间用户 !cmd:后端先发 shell_running 再发 shell_result,
    // 执行期插入占位行,结果到达后原位替换,避免长时间命令看似界面卡死
    _onShellRunning(msg) {
        if (!msg || msg.source === 'console' || msg.session_id !== this.currentSessionId) return;
        const c = document.getElementById('chat-messages');
        this._saveScrollState();
        // 只清理本会话的占位行: _broadcast 是跨会话的, 别的会话可能有多个并行占位行, 不能误删
        this._cleanShellPlaceholders(msg.session_id);
        this._removeWelcomeScreen();
        const el = document.createElement('div');
        el.className = 'system-message shell-running-placeholder';
        el.dataset.session = msg.session_id || '';
        el.appendChild(this._shellCmdLine(msg.command || '', true));
        c.appendChild(el);
        this._scrollToBottom();
    },

    _onShellResult(msg) {
        if (!msg || !msg.session_id) return;
        // 只移除同一会话的占位行: _broadcast 跨会话, 其他会话正在执行的占位行不能被误删。
        // console 源不会创建占位行(_onShellRunning 已守卫), 无需在此清理
        this._cleanShellPlaceholders(msg.session_id);
        if (msg.source === 'console' || msg.session_id !== this.currentSessionId) return;
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

    /** shell 命令行元素(占位行与结果行共用), textContent 赋值天然免疫注入 */
    _shellCmdLine(cmd, running) {
        const row = document.createElement('div');
        row.style.cssText = 'font-family:var(--font-mono);font-size:var(--text-sm);text-align:left;max-width:900px;margin:0 auto';
        const line = document.createElement('div');
        line.style.cssText = 'color:var(--neon-cyan);margin-bottom:2px';
        line.textContent = `$ ${cmd}`;
        if (running) {
            line.style.color = 'var(--text-3)';
            const spin = document.createElement('span');
            spin.style.color = 'var(--neon-cyan)';
            spin.textContent = ' ⚙ 执行中…';
            line.appendChild(spin);
        }
        row.appendChild(line);
        return row;
    },

    _renderShellResult(cmd, output) {
        const c = document.getElementById('chat-messages');
        this._saveScrollState();
        this._removeWelcomeScreen();
        const el = document.createElement('div');
        el.className = 'system-message';
        el.dataset.shellMsg = '1'; // 标记:对应后端一条 user 消息,计算删除数量时需计入
        el.appendChild(this._shellCmdLine(cmd, false));
        const pre = document.createElement('pre');
        pre.style.cssText = 'margin:0;white-space:pre-wrap;background:var(--bg-inset);padding:8px 12px;border-radius:var(--r-sm);text-align:left;max-width:900px;margin:0 auto';
        pre.textContent = output;
        el.appendChild(pre);
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
            line.dataset.session = msg.session_id || '';
            line.dataset.frame = '0';
            line.dataset.text = msg.text;
            line.dataset.startTime = Date.now().toString();
            area.appendChild(line);
        }
        line.dataset.text = msg.text;
        this._ensureSpinnerTimer();
    },

    _onSpinnerStop(msg) {
        if (!msg) return;
        const area = document.getElementById('spinner-area');
        // spinner 事件是跨会话广播的, 不能按当前会话守卫(否则其他会话的 stop 被吞, 行永久残留)。
        // wait_id 全局唯一可直接定位; 同时顺带清理不属于当前会话的遗留行(那些行只可能是切会话残留)
        const current = this.currentSessionId;
        area.querySelectorAll('.spinner-content').forEach(el => {
            if (el.dataset.wid === msg.wait_id || el.dataset.session !== current) el.remove();
        });
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

            // 删除消息后刷新上下文用量
            SessionPanel._fetchContextUsage(sid);

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
