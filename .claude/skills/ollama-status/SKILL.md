---
name: ollama-status
description: Check Ollama model status on Thor (RTX 4080), GPU utilization, loaded models, and Open WebUI health on Stark.
allowed-tools: Bash
---

# Ollama Status — Thor GPU + Open WebUI

## What This Does
Monitors the local LLM inference stack: Ollama on Thor (192.168.50.136) with RTX 4080, and Open WebUI on Stark (192.168.50.204).

## How To Run

### Ollama — List loaded models
```bash
curl -s http://192.168.50.136:11434/api/tags | jq '.models[] | {name: .name, size: .size, modified: .modified_at}'
```

### Ollama — Currently running models
```bash
curl -s http://192.168.50.136:11434/api/ps | jq '.models[] | {name: .name, size: .size, vram: .size_vram, expires: .expires_at}'
```

### Ollama — Service health
```bash
curl -s -o /dev/null -w "%{http_code}" http://192.168.50.136:11434/
```
Expected: `200` with "Ollama is running"

### GPU utilization (via SSH)
```bash
ssh -o ConnectTimeout=5 user@192.168.50.136 "nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total --format=csv,noheader"
```

### GPU processes
```bash
ssh -o ConnectTimeout=5 user@192.168.50.136 "nvidia-smi --query-compute-apps=pid,name,used_memory --format=csv,noheader"
```

### Open WebUI — Health check
```bash
curl -s -o /dev/null -w "%{http_code}" http://192.168.50.204:3000/
```

### Open WebUI — Container status
```bash
ssh -o ConnectTimeout=5 user@192.168.50.204 "docker ps --filter name=open-webui --format '{{.Names}}: {{.Status}}'"
```

### Test inference
```bash
curl -s http://192.168.50.136:11434/api/generate \
  -d '{"model": "qwen3:14b", "prompt": "ping", "stream": false}' | jq '{model: .model, eval_count: .eval_count, eval_duration: .eval_duration}'
```

## Report Format
| Component | Status | Detail |
|-----------|--------|--------|
| Ollama | ✅/❌ | Models loaded, version |
| RTX 4080 | ✅/⚠️ | Temp, VRAM used/total, utilization % |
| Open WebUI | ✅/❌ | HTTP status |
