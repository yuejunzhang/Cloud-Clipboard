import os
import redis
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# --- 数据库连接 (Vercel KV / Redis) ---
redis_url = os.environ.get("KV_URL")
if redis_url:
    r = redis.from_url(redis_url, ssl_cert_reqs=None)
else:
    r = None

def get_content():
    if r:
        val = r.get("clipboard_content")
        return val.decode('utf-8') if val else ""
    return ""

def set_content(text):
    if r:
        r.set("clipboard_content", text)

# --- 支持图文混排的 Web UI 模板 ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>云共享剪贴板 </title>
    <style>
        :root { --primary: #4f46e5; --bg: #f3f4f6; --card: #ffffff; --text: #1f2937; --border: #d1d5db; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: var(--bg); color: var(--text); margin: 0; padding: 20px; display: flex; justify-content: center; }
        .container { width: 100%; max-width: 800px; }
        h1 { text-align: center; color: var(--primary); margin-bottom: 5px; }
        .subtitle { text-align: center; color: #6b7280; font-size: 14px; margin-bottom: 20px; }
        .card { background: var(--card); border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); padding: 20px; }
        
        /* 富文本编辑器样式 */
        .editor { 
            width: 100%; min-height: 400px; max-height: 600px; overflow-y: auto; 
            padding: 15px; font-size: 16px; line-height: 1.5; 
            border: 2px solid var(--border); border-radius: 8px; 
            box-sizing: border-box; outline: none; transition: border-color 0.2s; 
            word-wrap: break-word;
        }
        .editor:focus { border-color: var(--primary); }
        .editor img { max-width: 100%; height: auto; border-radius: 4px; margin: 8px 0; display: block;}
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
        .warning { color: #ef4444; font-size: 12px; text-align: center; margin-top: 10px; background: #fef2f2; padding: 8px; border-radius: 6px;}
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 云共享剪贴板</h1>
        <p class="subtitle">支持图文混排 · 跨设备实时同步 · 完美写入系统剪贴板</p>
        
        <div class="card">
            <!-- 使用 contenteditable 替代 textarea -->
            <div id="editor" class="editor" contenteditable="true" data-placeholder="在这里输入文字，或直接粘贴图片 (Ctrl+V / Cmd+V)..."></div>
            
            <div class="actions">
                <button class="btn-primary" onclick="copyContent()">📋 复制内容到本地</button>
                <button class="btn-secondary" onclick="saveText()">💾 保存并同步</button>
            </div>
            <p class="warning">⚠️ Vercel 免费版限制单次请求 4MB，请勿粘贴超大高清原图，建议粘贴截图或压缩图。</p>
        </div>
        <p class="status">每 2 秒自动同步一次 · 粘贴图片会自动转码</p>
    </div>

    <div id="toast" class="toast"></div>

    <script>
        let lastContent = "";
        const editor = document.getElementById('editor');

        // 1. 拦截粘贴事件，处理图片转 Base64
        editor.addEventListener('paste', (e) => {
            const items = e.clipboardData.items;
            let hasImage = false;
            
            for (let item of items) {
                if (item.type.startsWith('image/')) {
                    hasImage = true;
                    e.preventDefault(); 
                    const file = item.getAsFile();
                    
                    // 简单压缩/限制大小 (可选，这里直接读取)
                    const reader = new FileReader();
                    reader.onload = (event) => {
                        const img = document.createElement('img');
                        img.src = event.target.result;
                        editor.appendChild(img);
                        editor.appendChild(document.createElement('br')); 
                        saveText(true); // 粘贴后自动保存
                        showToast("🖼️ 图片已插入并同步");
                    };
                    reader.readAsDataURL(file);
                }
            }
        });



        // 3. 保存内容到服务器
        function saveText(isAuto = false) {
            const htmlContent = editor.innerHTML;
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
                    if(!isAuto) showToast("✅ 已保存到云端");
                }
            })
            .catch(err => {
                if (err.message.includes('413')) {
                    showToast("❌ 图片太大，超过 4MB 限制！");
                } else {
                    showToast("❌ 保存失败");
                }
            });
        }

        // 4. 核心：复制内容到系统剪贴板 (支持图片)
        async function copyContent() {
            if (!editor.innerText.trim() && editor.getElementsByTagName('img').length === 0) {
                showToast("⚠️ 内容为空");
                return;
            }

            try {
                const htmlContent = editor.innerHTML;
                const textContent = editor.innerText;

                // 准备基础数据
                const htmlBlob = new Blob([htmlContent], { type: 'text/html' });
                const textBlob = new Blob([textContent], { type: 'text/plain' });
                
                let clipboardData = {
                    'text/html': htmlBlob,
                    'text/plain': textBlob
                };

                // 提取第一张图片，转换为 Blob 写入系统剪贴板 (兼容微信/Word等软件)
                const img = editor.querySelector('img');
                if (img && img.src.startsWith('data:image')) {
                    try {
                        const response = await fetch(img.src);
                        const blob = await response.blob();
                        // 统一使用 image/png 格式，兼容性最好
                        clipboardData['image/png'] = blob; 
                    } catch (e) {
                        console.warn("提取图片 Blob 失败", e);
                    }
                }

                // 使用现代 Clipboard API 写入
                const item = new ClipboardItem(clipboardData);
                await navigator.clipboard.write([item]);
                showToast("✅ 已复制图文到本地剪贴板");

            } catch (err) {
                console.error("复制失败:", err);
                // 降级方案：只复制纯文本
                try {
                    await navigator.clipboard.writeText(editor.innerText);
                    showToast("⚠️ 图片复制受限，已复制纯文本");
                } catch (e) {
                    showToast("❌ 复制失败，请手动 Ctrl+C");
                }
            }
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