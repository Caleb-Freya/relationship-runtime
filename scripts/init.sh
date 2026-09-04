#!/usr/bin/env bash
# 首次初始化：从示例生成 config.yaml（写入随机 token）与 persona.yaml。
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f config.yaml ]; then
  echo "config.yaml 已存在，跳过（如需重置请手动删除）"
else
  TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
  sed "s|# token 由 scripts/init.sh 首次启动自动生成，不要提交进 git|token: \"$TOKEN\"|" \
    config.example.yaml > config.yaml
  echo "已生成 config.yaml"
  echo "MCP 地址: http://<host>:18200/mcp"
  echo "Token:    $TOKEN"
  echo "把以上两项配到你的 AI 客户端即可。"
  # 默认 runtime.mode: live（与 config.example.yaml / 代码默认值一致）：
  # 配好 providers.notification 的推送渠道就会真发；想先纯观察改 shadow。
  echo "默认 live 模式：配置好推送渠道（bark/ntfy）后消息会真的推到手机；"
  echo "想先观察不真发，把 config.yaml 里 runtime.mode 改成 shadow 即可。"
fi

if [ -f persona.yaml ]; then
  echo "persona.yaml 已存在，跳过"
else
  # docker-compose.yml 会把 persona.yaml 挂进容器；文件不存在时 Docker
  # 会把宿主机这个路径自动建成一个空目录（而不是报错），人设就悄悄读不到，
  # 所以这里必须保证它在 docker compose up 之前就已经落地成一个真文件。
  cp persona.example.yaml persona.yaml
  echo "已生成 persona.yaml（可按需编辑人设/推送称呼）"
fi
