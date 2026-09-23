"""First-run database connection screen, then load the app in the same window."""
import os
import socket
import threading
import time

PAGE='''<!doctype html><html lang="zh"><meta charset="utf-8"><title>PaperPilot · 数据库连接</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f3f6f7;color:#253b45;font:14px "Segoe UI","Microsoft YaHei",sans-serif;display:grid;place-items:center;min-height:100vh}.box{width:min(540px,90vw);background:white;border:1px solid #dce5e7;padding:36px;border-radius:20px}small{letter-spacing:2px;color:#176c5c}h1{font-size:26px;margin:15px 0}p{color:#687d87;font-size:12px;line-height:1.8}label{display:grid;grid-template-columns:90px 1fr;align-items:center;gap:12px;margin:16px 0;font-size:12px}input{width:100%;padding:11px;background:#f2f5f6;border:1px solid #dce5e7;border-radius:8px;font:inherit}button{background:#176c5c;color:white;border:0;border-radius:8px;padding:12px;width:100%;font:inherit;cursor:pointer}button:disabled{opacity:.5}#result{color:#ae4c42;min-height:20px}</style>
<main class="box"><small>PAPERPILOT / CONNECTION</small><h1>连接你的 MySQL 数据库</h1><p>论文记录、会话与任务状态保存在 MySQL，PDF 和报告仍留在工作区。请先创建 PaperPilot 专用数据库和账号；不要填写管理员账号。连接失败不会回退到 SQLite。</p>
<form id="form"><label>主机<input id="host" value="127.0.0.1" required></label><label>端口<input id="port" value="3306" type="number" min="1" max="65535" required></label><label>数据库<input id="database" value="ai_reader" required></label><label>用户名<input id="user" value="reader" required></label><label>密码<input id="password" type="password" autocomplete="off"></label><p>Windows 上密码使用当前用户的 DPAPI 加密保存。MySQL 服务需要保持运行。</p><p id="result" role="status"></p><button id="connect">连接并打开工作区</button></form></main>
<script>document.getElementById('form').onsubmit=async e=>{e.preventDefault();const button=document.getElementById('connect');button.disabled=true;document.getElementById('result').textContent='正在连接…';try{const result=await window.pywebview.api.connect({MYSQL_HOST:document.getElementById('host').value,MYSQL_PORT:document.getElementById('port').value,MYSQL_DATABASE:document.getElementById('database').value,MYSQL_USER:document.getElementById('user').value,MYSQL_PASSWORD:document.getElementById('password').value});if(result.error)document.getElementById('result').textContent=result.error;}catch(e){document.getElementById('result').textContent='连接失败，请检查服务和账号。';}finally{button.disabled=false;}};</script></html>'''

def run_setup(workspace):
    import webview
    from src.runtime.database_config import save_database_config
    class Setup:
        started=False
        server=None
        thread=None
        listener=None
        lock=threading.Lock()
        def connect(self,values):
            with self.lock:
                if self.started:return {'error':'工作区已连接'}
                allowed={'MYSQL_HOST','MYSQL_PORT','MYSQL_DATABASE','MYSQL_USER','MYSQL_PASSWORD'}
                if not isinstance(values,dict) or set(values)!=allowed:return {'error':'连接字段不完整'}
                previous={key:os.environ.get(key) for key in allowed}
                try:
                    os.environ.update({key:str(value) for key,value in values.items()})
                    from src.knowledge.mysql_store import MySQLStore
                    probe=MySQLStore();probe.close()
                    from src.app.factory import build_app
                    app,_=build_app(workspace)
                    import uvicorn
                    listener=socket.socket();listener.bind(('127.0.0.1',0));self.listener=listener
                    port=listener.getsockname()[1]
                    self.server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port))
                    self.thread=threading.Thread(target=lambda:self.server.run(sockets=[listener]),daemon=True)
                    self.thread.start()
                    for _ in range(100):
                        if self.server.started:break
                        if not self.thread.is_alive():raise RuntimeError('后台启动失败，请检查任务内核与数据库占用')
                        time.sleep(.1)
                    if not self.server.started:raise RuntimeError('后台启动超时')
                    save_database_config(workspace,values)
                    self.started=True
                    window.load_url(f'http://127.0.0.1:{port}/')
                    return {'ok':True}
                except Exception as exc:
                    self._stop()
                    for key,value in previous.items():
                        if value is None:os.environ.pop(key,None)
                        else:os.environ[key]=value
                    return {'error':str(exc) if isinstance(exc,(RuntimeError,ValueError)) else '连接失败，请检查数据库权限和日志'}
        def _stop(self):
            if self.server:self.server.should_exit=True
            if self.thread:self.thread.join(timeout=15)
            if self.listener:self.listener.close()
    setup=Setup()
    window=webview.create_window('PaperPilot',html=PAGE,js_api=setup,width=1200,height=800,min_size=(800,600))
    try:webview.start(storage_path=str(workspace/'.webview'),private_mode=False)
    finally:setup._stop()
