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

# --- 稳定支持图文混排的 Web UI 模板 ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>云共享剪贴板 (稳定图文版)</title>
    <style>
        :root { --primary: #4f46e5; --bg: #f3f4f6; --card: #ffffff; --text: #1f2937; --border: #d1d5db; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: var(--bg); color: var(--text); margin: 0; padding: 20px; display: flex; justify-content: center; }
        .container { width: 100%; max-width: 800px; }
        h1 { text-align: center; color: var(--primary); margin-bottom: 5px; }
        .subtitle { text-align: center; color: #6b7280; font-size: 14px; margin-bottom: 20px; }
        .card { background: var(--card); border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); padding: 20px; }
        
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
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 云共享剪贴板</h1>
        <p class="subtitle">支持图文混排 · 自动压缩 · 跨设备稳定同步</p>
        
        <div class="card">
            <div id="editor" class="editor" contenteditable="true" data-placeholder="在这里输入文字，或直接粘贴图片 (Ctrl+V / Cmd+V)..."></div>
            
            <div class="actions">
                <button class="btn-primary" onclick="copyContent()">📋 复制内容到本地</button>
                <button class="btn-secondary" onclick="saveText()">💾 保存并同步</button>
            </div>
        </div>
        <p class="status">每 2 秒自动同步 · 粘贴图片会自动压缩并保存</p>
    </div>

    <div id="toast" class="toast"></div>

    <script>
        let lastContent = "";
        let isSaving = false; // 并发锁，防止保存时被轮询覆盖
        const editor = document.getElementById('editor');

        // --- 工具函数：Base64 转 Blob (用于写入系统剪贴板) ---
        function dataURItoBlob(dataURI) {
            const byteString = atob(dataURI.split(',')[1]);
            const mimeString = dataURI.split(',')[0].split(':')[1].split(';')[0];
            const ab = new ArrayBuffer(byteString.length);
            const ia = new Uint8Array(ab);
            for (let i = 0; i < byteString.length; i++) ia[i] = byteString.charCodeAt(i);
            return new Blob([ab], {type: mimeString});
        }

        // --- 工具函数：图片压缩 (防止超过 Vercel 4MB 限制) ---
        function compressImage(file, maxWidth = 1200, quality = 0.8) {
            return new Promise((resolve) => {
                const reader = new FileReader();
                reader.onload = (e) => {
                    const img = new Image();
                    img.onload = () => {
                        const canvas = document.createElement('canvas');
                        let width = img.width, height = img.height;
                        if (width > maxWidth) {
                            height = (maxWidth / width) * height;
                            width = maxWidth;
                        }
                        canvas.width = width; canvas.height = height;
                        canvas.getContext('2d').drawImage(img, 0, 0, width, height);
                        
                        canvas.toBlob((blob) => {
                            const blobReader = new FileReader();
                            blobReader.onloadend = () => resolve(blobReader.result);
                            blobReader.readAsDataURL(blob);
                        }, file.type || 'image/png', quality);
                    };
                    img.src = e.target.result;
                };
                reader.readAsDataURL(file);
            });
        }

        // 1. 拦截粘贴事件，压缩并插入图片
        editor.addEventListener('paste', async (e) => {
            const items = e.clipboardData.items;
            for (let item of items) {
                if (item.type.startsWith('image/')) {
                    e.preventDefault(); 
                    const file = item.getAsFile();
                    if (!file) continue;
                    
                    showToast("🖼️ 正在压缩并插入图片...");
                    try {
                        const base64 = await compressImage(file);
                        const img = document.createElement('img');
                        img.src = base64;
                        editor.appendChild(img);
                        editor.appendChild(document.createElement('br')); 
                        saveText(true); // 自动保存
                        showToast("✅ 图片已插入并同步");
                    } catch (err) {
                        showToast("❌ 图片处理失败");
                    }
                    break; 
                }
            }
        });

        // 2. 自动轮询同步 (带防覆盖保护)
        function sync() {
            if (isSaving) return; // 正在保存时，跳过同步
            
            fetch('/api/clipboard')
                .then(r => r.json())
                .then(data => {
                    // 核心保护：如果服务器返回空，但本地有内容，大概率是并发导致的旧数据，拒绝覆盖！
                    if (data.text === "" && editor.innerHTML.trim() !== "") return;
                    
                    if (data.text !== lastContent && document.activeElement !== editor) {
                        editor.innerHTML = data.text;
                        lastContent = data.text;
                    }
                })
                .catch(err => console.error("Sync error:", err));
        }
        setInterval(sync, 2000);
        sync(); 

        // 3. 保存内容到服务器 (带并发锁)
        function saveText(isAuto = false) {
            isSaving = true; 
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
            .catch(err => showToast("❌ 保存失败，请检查网络或图片大小"))
            .finally(() => { isSaving = false; }); // 解锁
        }

        // 4. 复制内容到系统剪贴板 (完美支持图片)
        async function copyContent() {
            if (!editor.innerText.trim() && editor.getElementsByTagName('img').length === 0) {
                showToast("⚠️ 内容为空"); return;
            }

            try {
                const htmlBlob = new Blob([editor.innerHTML], { type: 'text/html' });
                const textBlob = new Blob([editor.innerText], { type: 'text/plain' });
                let clipboardData = { 'text/html': htmlBlob, 'text/plain': textBlob };

                // 提取图片并转为 Blob
                const img = editor.querySelector('img');
                if (img && img.src.startsWith('data:image')) {
                    try {
                        const blob = dataURItoBlob(img.src);
                        clipboardData['image/png'] = blob; 
                    } catch (e) { console.warn("提取图片失败", e); }
                }

                const item = new ClipboardItem(clipboardData);
                await navigator.clipboard.write([item]);
                showToast("✅ 已复制图文到本地剪贴板");

            } catch (err) {
                // 降级方案
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