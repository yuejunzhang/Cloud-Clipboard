import os
import redis
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# --- 数据库连接 (Vercel KV / Redis) ---
# Vercel 绑定 KV 后会自动注入 KV_URL 环境变量
redis_url =  os.environ.get("KV_URL")
if redis_url:
    # ssl_cert_reqs=None 用于避免 Serverless 环境下的 SSL 证书报错
    r = redis.from_url(redis_url, ssl_cert_reqs=None)
else:
    r = None
    print("⚠️ 警告: 未找到 KV_URL，数据将仅保存在内存中（重启/冷启动会丢失）")

# 内存回退变量 (仅用于本地测试)
memory_content = ""

def get_content():
    global memory_content
    if r:
        val = r.get("clipboard_content")
        return val.decode('utf-8') if val else ""
    return memory_content

def set_content(text):
    global memory_content
    if r:
        r.set("clipboard_content", text)
    else:
        memory_content = text

# --- 稳定支持图文混排的 Web UI 模板 ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>云共享剪贴板</title>
    <style>
        :root { --primary: #4f46e5; --bg: #f3f4f6; --card: #ffffff; --text: #1f2937; --border: #d1d5db; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: var(--bg); color: var(--text); margin: 0; padding: 20px; display: flex; justify-content: center; }
        .container { width: 100%; max-width: 800px; }
        h1 { text-align: center; color: var(--primary); margin-bottom: 5px; }
        .subtitle { text-align: center; color: #6b7280; font-size: 14px; margin-bottom: 20px; }
        .card { background: var(--card); border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); padding: 20px; }
        
        /* 替换 textarea 为 contenteditable div */
        .editor { 
            width: 100%; min-height: 300px; max-height: 500px; overflow-y: auto; 
            padding: 15px; font-size: 16px; line-height: 1.5; 
            border: 2px solid var(--border); border-radius: 8px; 
            box-sizing: border-box; outline: none; transition: border-color 0.2s; 
            word-wrap: break-word;
        }
        .editor:focus { border-color: var(--primary); }
        .editor img { max-width: 100%; height: auto; border-radius: 4px; margin: 8px 0; display: block; cursor: pointer;}
        .editor:empty:before { content: attr(data-placeholder); color: #9ca3af; pointer-events: none; }

        .actions { display: flex; gap: 10px; margin-top: 15px; flex-wrap: wrap; }
        button { flex: 1; padding: 12px; font-size: 16px; font-weight: 600; border: none; border-radius: 8px; cursor: pointer; transition: all 0.2s; min-width: 120px; }
        .btn-primary { background-color: var(--primary); color: white; }
        .btn-primary:hover { background-color: #4338ca; }
        .btn-secondary { background-color: #10b981; color: white; }
        .btn-secondary:hover { background-color: #059669; }
        
        .toast { position: fixed; bottom: 300px; left: 50%; transform: translateX(-50%); background: #333; color: white; padding: 10px 20px; border-radius: 20px; opacity: 0; transition: opacity 0.3s; pointer-events: none; z-index: 100;}
        .toast.show { opacity: 1; }
        .status { text-align: center; font-size: 12px; color: #9ca3af; margin-top: 15px; }
        .warning { color: #ef4444; font-size: 12px; text-align: center; margin-top: 10px;}
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 云共享剪贴板</h1>
        
        <div class="card">
            <!-- 使用 contenteditable 替代 textarea -->
            <div id="editor" class="editor" contenteditable="true" data-placeholder="在这里输入文字，或直接粘贴图片 (Ctrl+V)...可分享内容到云端，或将云端内容复制到本地，以便于跨设备分享内容。(云内容会在几分钟后失效)"></div>
            
            <div class="actions">
   
                <button class="btn-primary" onclick="copyContent()">📋 复制内容到本地</button>
                <button class="btn-secondary" onclick="saveText()">📝 分享内容到云端</button>
            </div>
        </div>
        <p class="status">每 2 秒自动同步一次 · 粘贴图片会自动上传</p>
    </div>

    <div id="toast" class="toast"></div>

    <script>
        let lastContent = "";
        let isUserTyping = false; // 核心状态：标记用户是否正在输入
        const editor = document.getElementById('editor');

        // --- 状态监听：当用户开始输入时，标记为正在编辑 ---
        editor.addEventListener('input', () => {
            isUserTyping = true;
        });

        // 1. 拦截粘贴事件，处理图片
        editor.addEventListener('paste', (e) => {
            const items = e.clipboardData.items;
            for (let item of items) {
                if (item.type.startsWith('image/')) {
                    e.preventDefault(); 
                    const file = item.getAsFile();
                    if (!file) continue;
                    
                    const reader = new FileReader();
                    reader.onload = (event) => {
                        const img = document.createElement('img');
                        img.src = event.target.result;
                        editor.appendChild(img);
                        editor.appendChild(document.createElement('br')); 
                        
                        // 粘贴图片后自动保存
                        saveText(true); 
                        showToast("🖼️ 图片已插入");
                    };
                    reader.readAsDataURL(file);
                    break; 
                }
            }
        });

        // 2. 自动轮询同步 (智能防覆盖逻辑)
        let isSyncing = false; // 防止重复请求
        function sync() {
            fetch('/api/clipboard')
                .then(r => r.json())
                .then(data => {
                    // 如果服务器内容没变，直接返回
                    if (data.text === lastContent) return;
isSyncing = true;
                    const isFocused = document.activeElement === editor;

                    // 核心逻辑：
                    // 如果没有焦点，或者虽然有焦点但用户还没开始打字 (isUserTyping === false) -> 安全刷新
                    if (!isFocused || !isUserTyping) {
                        editor.innerHTML = data.text;
                        lastContent = data.text;
                        // 刷新后重置状态
                        isUserTyping = false; 
                        isSyncing = false;
                    } else {
                        // 正在打字中 -> 拒绝刷新，保护用户输入，并给出提示
                        showToast("🔔 收到新内容，正在保护您的编辑...");
                        isSyncing = false;
                    }
                })
                .catch(err => console.error("Sync error:", err));
        }
    showToast("正在加载内容...");
    sync(); 

setInterval(function() {
    // 在这里定义要执行的代码
   if(!isSyncing) sync(); 
}, 2000);


        // 3. 保存内容到服务器 (优化了空值判断)
        function saveText(isAuto = false) {
            // 智能判断是否为空：如果没有纯文本，且没有图片，则视为空字符串
            // 彻底抛弃容易出错的正则表达式和 
            const hasText = editor.innerText.trim().length > 0;
            const hasImage = editor.getElementsByTagName('img').length > 0;
                        const htmlContent = editor.innerHTML;
                        let textToSave = htmlContent.replace(/^(<br\s*\/?>|\s)+|(<br\s*\/?>|\s)+$/g, '') === '' ? "" : htmlContent;
textToSave += '<br>';

            fetch('/api/clipboard', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({text: textToSave})
            })
            .then(r => r.json())
            .then(data => {
                if(data.status === 'success') {
                    lastContent = textToSave; 
                    isUserTyping = false; // 关键：保存成功后，重置输入状态
                    if(!isAuto) showToast("✅ 已分享到云端");
                }
            });
        }

        // 4. 复制内容到系统剪贴板
        function copyContent() {
            if (!editor.innerText.trim() && editor.getElementsByTagName('img').length === 0) {
                showToast("⚠️ 内容为空");
                return;
            }

            try {
                const htmlBlob = new Blob([editor.innerHTML], { type: 'text/html' });
                const textBlob = new Blob([editor.innerText], { type: 'text/plain' });
                const clipboardItem = new ClipboardItem({
                    'text/html': htmlBlob,
                    'text/plain': textBlob
                });
                
                navigator.clipboard.write([clipboardItem]).then(() => {
                    showToast("✅ 已复制富文本到剪贴板");
                }).catch(() => {
                    fallbackCopy();
                });
            } catch (err) {
                fallbackCopy();
            }
        }

        // 回退复制方案
        function fallbackCopy() {
            const textArea = document.createElement("textarea");
            textArea.value = editor.innerText; 
            document.body.appendChild(textArea);
            textArea.select();
            try {
                document.execCommand('copy');
                showToast("✅ 已复制文本 (图片请右键另存)");
            } catch (err) {
                showToast("❌ 复制失败");
            }
            document.body.removeChild(textArea);
        }

        function showToast(msg) {
            const toast = document.getElementById('toast');
            toast.innerText = msg;
            toast.classList.add('show');
            setTimeout(() => toast.classList.remove('show'), 2500);
        }
    </script>
</body>
</html>
"""

# --- Flask 路由 ---
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/clipboard', methods=['GET'])
def get_clipboard():
    return jsonify({"text": get_content()})

@app.route('/api/clipboard', methods=['POST'])
def set_clipboard():
    data = request.json
    if data and 'text' in data:
        set_content(data['text'])
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 400