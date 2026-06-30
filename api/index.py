import os
import redis
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# --- 数据库连接 (Vercel KV / Redis) ---
# Vercel 绑定 KV 后会自动注入 KV_URL 环境变量
redis_url = os.environ.get("KV_URL")
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
            width: 100%; min-height: 400px; max-height: 600px; overflow-y: auto; 
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
        
        .toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: #333; color: white; padding: 10px 20px; border-radius: 20px; opacity: 0; transition: opacity 0.3s; pointer-events: none; z-index: 100;}
        .toast.show { opacity: 1; }
        .status { text-align: center; font-size: 12px; color: #9ca3af; margin-top: 15px; }
        .warning { color: #ef4444; font-size: 12px; text-align: center; margin-top: 10px;}
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 云共享剪贴板</h1>
        <p class="subtitle">可编辑内容分享到云端，或将云端内容复制到本地。</p>
        
        <div class="card">
            <!-- 使用 contenteditable 替代 textarea -->
            <div id="editor" class="editor" contenteditable="true" data-placeholder="在这里输入文字，或直接粘贴图片 (Ctrl+V)..."></div>
            
            <div class="actions">
                <button class="btn-primary" onclick="copyContent()">📋 复制内容到本地</button>
                <button class="btn-secondary" onclick="saveText()">📝 分享内容到云端</button>
            </div>
            <p class="warning">⚠️ 受限于浏览器安全策略，若图片无法直接写入本地剪贴板，请右键图片复制或长按保存。</p>
        </div>
        <p class="status">每 2 秒自动同步一次 · 粘贴图片会自动上传</p>
    </div>

    <div id="toast" class="toast"></div>

    <script>
        let lastContent = "";
        const editor = document.getElementById('editor');

        // 1. 拦截粘贴事件，处理图片
        editor.addEventListener('paste', (e) => {
            const items = e.clipboardData.items;
            let hasImage = false;
            
            for (let item of items) {
                if (item.type.startsWith('image/')) {
                    hasImage = true;
                    e.preventDefault(); // 阻止默认粘贴行为
                    
                    const file = item.getAsFile();
                    const reader = new FileReader();
                    
                    reader.onload = (event) => {
                        // 将图片转为 Base64 并插入编辑器
                        const img = document.createElement('img');
                        img.src = event.target.result;
                        editor.appendChild(img);
                        editor.appendChild(document.createElement('br')); // 换行
                        
                        // 粘贴图片后自动保存
                        saveText(true); 
                        showToast("🖼️ 图片已插入");
                    };
                    reader.readAsDataURL(file);
                }
            }
            // 如果是纯文本，不阻止默认行为，让它正常粘贴
        });

        // 2. 自动轮询同步 (每2秒)
        function sync() {
            fetch('/api/clipboard')
                .then(r => r.json())
                .then(data => {
                    // 如果服务器内容变了，且当前编辑器没有获得焦点
                    if (data.text !== lastContent && document.activeElement !== editor) {
                        editor.innerHTML = data.text;
                        lastContent = data.text;
                    }
                })
                .catch(err => console.error("Sync error:", err));
        }
        setInterval(sync, 2000);
        sync(); 

        // 3. 保存内容到服务器
        function saveText(isAuto = false) {
            const htmlContent = editor.innerHTML;
            
            // 简单优化：如果内容只是 <br> 或空，视为空字符串
            const textToSave = htmlContent.replace(/^(<br\s*\/?>|\s)+|(<br\s*\/?>|\s)+$/g, '') === '' ? "" : htmlContent;

            fetch('/api/clipboard', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({text: textToSave})
            })
            .then(r => r.json())
            .then(data => {
                if(data.status === 'success') {
                    lastContent = textToSave; 
                    if(!isAuto) showToast("✅ 已分享到云端");
                }
            });
        }

        // 4. 复制内容
        function copyContent() {
            if (!editor.innerText.trim() && editor.getElementsByTagName('img').length === 0) {
                showToast("⚠️ 内容为空");
                return;
            }

            // 尝试使用现代 API 复制富文本 (包含图片)
            // 注意：在局域网 HTTP 下，这通常会失败，会走 catch 逻辑
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

        // 回退复制方案 (只能复制纯文本)
        function fallbackCopy() {
            const textArea = document.createElement("textarea");
            textArea.value = editor.innerText; // 只复制纯文本部分
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
            setTimeout(() => toast.classList.remove('show'), 2000);
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