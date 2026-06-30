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

# --- Web UI 模板 (与之前相同，此处省略以节省篇幅，请复制你之前的 HTML_TEMPLATE) ---
# 注意：因为部署到了 Vercel (HTTPS)，你可以把 JS 里的 fallbackCopy 删掉，
# 直接使用 navigator.clipboard.writeText()，它会完美生效！
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>云共享剪贴板 (Vercel版)</title>
    <style>
        body { font-family: sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; text-align: center; }
        textarea { width: 100%; height: 300px; padding: 10px; font-size: 16px; }
        button { padding: 10px 20px; margin: 10px 5px; cursor: pointer; font-size: 16px; }
    </style>
</head>
<body>
    <h1>📋 云端共享剪贴板</h1>
    <p>已部署至 Vercel · 支持 HTTPS · 完美支持图片复制</p>
    <textarea id="content" placeholder="输入内容..."></textarea>
    <br>
    <button onclick="saveText()">💾 保存并同步</button>
    <button onclick="copyText()">📋 复制到本地</button>

    <script>
        // 1. 自动同步
        function sync() {
            fetch('/api/clipboard').then(r => r.json()).then(data => {
                if (document.activeElement !== document.getElementById('content')) {
                    document.getElementById('content').value = data.text;
                }
            });
        }
        setInterval(sync, 2000);
        sync();

        // 2. 保存
        function saveText() {
            const text = document.getElementById('content').value;
            fetch('/api/clipboard', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({text: text})
            }).then(() => alert("✅ 已保存到云端"));
        }

        // 3. 复制 (HTTPS 环境下完美运行)
        function copyText() {
            const text = document.getElementById('content').value;
            navigator.clipboard.writeText(text).then(() => {
                alert("✅ 已复制到本地剪贴板");
            }).catch(() => alert("❌ 复制失败"));
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

# 注意：删除了 app.run()，Vercel 会自动接管 WSGI 入口