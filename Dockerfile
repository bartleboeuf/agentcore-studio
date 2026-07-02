ARG arch=arm64
FROM --platform=linux/${arch} public.ecr.aws/docker/library/python:3.13-slim
WORKDIR /app
# agentcore CLI + uv（运行时调用 agentcore deploy/invoke 需要）；strands-agents 让本地 Playground 能真实运行 entry 的工具循环；playwright 用于 Browser 工具 connect_over_cdp（仅 pip 包，无需本地 chromium）
# agentcore CLI + uv (required for agentcore deploy/invoke at runtime); strands-agents enables local Playground to run entry tool loops; playwright for Browser tool connect_over_cdp (pip pkg only, no local chromium needed)
RUN pip install --no-cache-dir uv
RUN uv venv && uv pip install --no-cache-dir bedrock-agentcore-starter-toolkit strands-agents playwright anthropic
RUN apt-get update && apt-get install -y --no-install-recommends zip curl unzip && \
    curl -s "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o /tmp/awscliv2.zip && \
    unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install && rm -rf /tmp/aws /tmp/awscliv2.zip && \
    rm -rf /var/lib/apt/lists/*
# Node 20 + @aws/agentcore preview CLI（harness 模式需要；用 npx 调用，不全局装以免与 Python 版 agentcore 命名冲突，构建时预热缓存）
# Node 20 + @aws/agentcore preview CLI (required for harness mode; call using npx; do not install globally to avoid naming conflicts with the Python version of agentcore; preheat the cache during build)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x -o /tmp/nodesetup.sh && bash /tmp/nodesetup.sh && \
    apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/* /tmp/nodesetup.sh && \
    (npx -y @aws/agentcore@preview --version >/dev/null 2>&1 || true)
COPY index.html compare.html server.py deep_generate.py ./
ENV HOST=0.0.0.0 PORT=8080 AGENTCORE_SUPPRESS_RECOMMENDATION=1
EXPOSE 8080
CMD ["uv", "run", "server.py"]