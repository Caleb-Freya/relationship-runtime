#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transparent-proxy — 会抄送的中转站（relationship-runtime 的通用上下文入口）

她的前端 ──POST /v1/chat/completions──▶ 本代理 ──原样转发──▶ 她原来的中转站/官方API
                                          │
                                          └─(后台线程)抄送 user 消息 ──▶ runtime /api/event

- 对上游完全透明：路径/头/体原样转发，流式(SSE)逐块透传，不改一个字
- 只抄送 role=user 的最后一条消息文本(前300字)，与 claude-code-hook 对齐
- 抄送永不阻塞、永不破坏对话：runtime 挂了对话照常
- 零依赖：纯 Python 标准库，任何机器 python3 proxy.py 就能跑
- 兼容 OpenAI(/chat/completions) 与 Anthropic(/v1/messages) 两种消息格式

配置：环境变量 或 同目录 proxy-config.json（环境变量优先）
  UPSTREAM_BASE   必填，上游地址，如 https://api.你的中转站.com
  RR_URL          runtime 地址，默认 http://127.0.0.1:18200
  RR_TOKEN        runtime 的 Bearer token
  PROXY_PORT      监听端口，默认 8100
  PROXY_BIND      监听地址，默认 127.0.0.1（对外请走隧道/反代）
"""
import json
import os
import ssl
import threading
import time
import urllib.request
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))


def load_cfg():
    cfg = {}
    p = os.path.join(HERE, "proxy-config.json")
    if os.path.exists(p):
        try:
            cfg = json.load(open(p))
        except Exception:
            pass
    def pick(env, key, default=None):
        return os.environ.get(env) or cfg.get(key) or default
    return {
        "upstream": (pick("UPSTREAM_BASE", "upstream_base") or "").rstrip("/"),
        "rr_url": (pick("RR_URL", "runtime_url", "http://127.0.0.1:18200")).rstrip("/"),
        "rr_token": pick("RR_TOKEN", "runtime_token", ""),
        "port": int(pick("PROXY_PORT", "port", 8100)),
        "bind": pick("PROXY_BIND", "bind", "127.0.0.1"),
    }


CFG = load_cfg()

# 逐跳头不转发（由每一跳自己管理）
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
       "te", "trailers", "transfer-encoding", "upgrade", "host",
       "content-length", "accept-encoding"}


def extract_user_text(body: bytes) -> str:
    """从 OpenAI/Anthropic 风格请求体里挖出最后一条 user 消息的文本。挖不到返回空。"""
    try:
        data = json.loads(body.decode("utf-8", "replace"))
        msgs = data.get("messages") or []
        for m in reversed(msgs):
            if not isinstance(m, dict) or m.get("role") != "user":
                continue
            c = m.get("content")
            if isinstance(c, str):
                return c.strip()
            if isinstance(c, list):  # Anthropic/OpenAI 多模态: [{type:text,text:...},...]
                parts = [b.get("text", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "text"]
                return "\n".join(p for p in parts if p).strip()
        return ""
    except Exception:
        return ""


def tap_to_runtime(text: str):
    """后台抄送。失败静默——绝不影响对话。"""
    if not text or not CFG["rr_token"]:
        return
    ev = {
        "event_id": f"proxy-{int(time.time()*1000)}",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "source": "transparent-proxy", "actor": "user", "type": "user_message",
        "context_window_id": "proxy",
        "payload": {"text": text[:300]},
    }
    try:
        req = urllib.request.Request(
            CFG["rr_url"] + "/api/event", data=json.dumps(ev).encode(),
            headers={"Authorization": f"Bearer {CFG['rr_token']}",
                     "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "rr-proxy/1.0"

    def log_message(self, *a):
        pass  # 不记对话内容

    def _forward(self, method: str):
        if not CFG["upstream"]:
            self.send_error(502, "UPSTREAM_BASE not configured")
            return
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n) if n else b""

        # 抄送（只抄聊天补全类请求的 user 消息，后台线程，不等结果）
        if method == "POST" and ("/chat/completions" in self.path
                                 or "/messages" in self.path):
            text = extract_user_text(body)
            if text:
                threading.Thread(target=tap_to_runtime, args=(text,),
                                 daemon=True).start()

        # 原样转发到上游
        u = urlsplit(CFG["upstream"])
        Conn = http.client.HTTPSConnection if u.scheme == "https" \
            else http.client.HTTPConnection
        try:
            conn = Conn(u.netloc, timeout=600,
                        context=ssl.create_default_context()
                        if u.scheme == "https" else None) \
                if u.scheme == "https" else Conn(u.netloc, timeout=600)
            fwd_headers = {k: v for k, v in self.headers.items()
                           if k.lower() not in HOP}
            fwd_headers["Host"] = u.netloc
            conn.request(method, (u.path or "") + self.path, body=body or None,
                         headers=fwd_headers)
            resp = conn.getresponse()
        except Exception as e:
            self.send_error(502, f"upstream unreachable: {e.__class__.__name__}")
            return

        # 回传：流式逐块透传。用 Connection: close 收尾，避免重新分帧。
        try:
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in HOP:
                    continue
                self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()          # SSE 的关键：每块即刻推给前端
        except (BrokenPipeError, ConnectionResetError):
            pass                            # 前端提前挂断，正常现象
        finally:
            try:
                conn.close()
            except Exception:
                pass
            self.close_connection = True

    def do_POST(self):
        self._forward("POST")

    def do_GET(self):
        if self.path == "/rr-proxy/health":   # 代理自己的健康检查，不进上游
            body = json.dumps({"ok": True, "upstream": bool(CFG["upstream"]),
                               "tap": bool(CFG["rr_token"])}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._forward("GET")

    def do_DELETE(self):
        self._forward("DELETE")

    def do_PUT(self):
        self._forward("PUT")


if __name__ == "__main__":
    print(f"rr transparent-proxy on http://{CFG['bind']}:{CFG['port']}"
          f"  →  upstream: {CFG['upstream'] or '(未配置!)'}"
          f"  |  tap: {'on' if CFG['rr_token'] else 'off(缺RR_TOKEN)'}")
    ThreadingHTTPServer((CFG["bind"], CFG["port"]), Proxy).serve_forever()
