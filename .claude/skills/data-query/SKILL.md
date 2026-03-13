---
name: data-query
description: Query Clickhouse analytics and LiteLLM usage stats on Cortex. Use for data analysis, model routing status, and usage metrics.
allowed-tools: Bash
---

# Data Query — Clickhouse + LiteLLM

## What This Does
Queries Clickhouse (analytics/event data) and LiteLLM (model proxy stats) running on Cortex (192.168.50.106).

## Clickhouse Queries

### Run a query via HTTP interface
```bash
curl -s "http://192.168.50.106:8123/" \
  --data-urlencode "query=<SQL_QUERY>" \
  -u "$CLICKHOUSE_USER:$CLICKHOUSE_PASS"
```

### List databases
```bash
curl -s "http://192.168.50.106:8123/" \
  --data-urlencode "query=SHOW DATABASES" \
  -u "$CLICKHOUSE_USER:$CLICKHOUSE_PASS"
```

### List tables in a database
```bash
curl -s "http://192.168.50.106:8123/" \
  --data-urlencode "query=SHOW TABLES FROM <DATABASE>" \
  -u "$CLICKHOUSE_USER:$CLICKHOUSE_PASS"
```

### Describe a table
```bash
curl -s "http://192.168.50.106:8123/" \
  --data-urlencode "query=DESCRIBE TABLE <DATABASE>.<TABLE>" \
  -u "$CLICKHOUSE_USER:$CLICKHOUSE_PASS"
```

### Query with JSON output
```bash
curl -s "http://192.168.50.106:8123/" \
  --data-urlencode "query=<SQL_QUERY> FORMAT JSON" \
  -u "$CLICKHOUSE_USER:$CLICKHOUSE_PASS" | jq '.data'
```

## LiteLLM Queries

### Health check
```bash
curl -s http://192.168.50.106:4000/health | jq '.'
```

### List available models
```bash
curl -s -H "Authorization: Bearer $LITELLM_API_KEY" \
  http://192.168.50.106:4000/v1/models | jq '.data[] | {id: .id, owned_by: .owned_by}'
```

### Usage stats
```bash
curl -s -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  "http://192.168.50.106:4000/global/spend/logs?start_date=$(date -d '24 hours ago' +%Y-%m-%d)&end_date=$(date +%Y-%m-%d)" | jq '.'
```

### Model routing info
```bash
curl -s -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  http://192.168.50.106:4000/model/info | jq '.data[] | {model: .model_name, provider: .litellm_params.model}'
```

### Test a model
```bash
curl -s -X POST -H "Authorization: Bearer $LITELLM_API_KEY" \
  -H "Content-Type: application/json" \
  http://192.168.50.106:4000/v1/chat/completions \
  -d '{"model": "<MODEL>", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5}'
```

## Report Format
| Component | Status | Detail |
|-----------|--------|--------|
| Clickhouse | ✅/❌ | Databases, table count |
| LiteLLM | ✅/❌ | Models available, 24h spend |
