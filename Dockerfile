FROM public.ecr.aws/docker/library/python:3.12-slim
# agentcore CLI + uv (required for agentcore deploy/invoke at runtime); strands-agents enables local Playground to run entry tool loops; playwright for Browser tool connect_over_cdp (pip pkg only, no local chromium needed)
RUN pip install --no-cache-dir uv bedrock-agentcore-starter-toolkit strands-agents playwright anthropic
RUN apt-get update && apt-get install -y --no-install-recommends zip curl unzip && \
    curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip && \
    unzip -q /tmp/awscliv2.zip -d /tmp && /tmp/aws/install && rm -rf /tmp/aws /tmp/awscliv2.zip && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY index.html compare.html server.py i18n.py i18n.js ./
ENV HOST=0.0.0.0 PORT=8080 AGENTCORE_SUPPRESS_RECOMMENDATION=1 STUDIO_LANG=en
EXPOSE 8080
CMD ["python3", "server.py"]

