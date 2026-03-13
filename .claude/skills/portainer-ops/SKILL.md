---
name: portainer-ops
description: Manage containers on Stark via Portainer API — list stacks, container status, logs, and restart services.
allowed-tools: Bash
---

# Portainer Ops — Stark Container Management

## What This Does
Manages Docker containers on Stark (192.168.50.204) through the Portainer API. Covers stacks, containers, images, and volumes.

## How To Run

### Authenticate and get JWT token
```bash
PORTAINER_TOKEN=$(curl -s -X POST \
  "http://192.168.50.204:9000/api/auth" \
  -H "Content-Type: application/json" \
  -d "{\"username\": \"$PORTAINER_USER\", \"password\": \"$PORTAINER_PASS\"}" | jq -r '.jwt')
```

### List all containers
```bash
curl -s -H "Authorization: Bearer $PORTAINER_TOKEN" \
  "http://192.168.50.204:9000/api/endpoints/1/docker/containers/json?all=true" | jq '.[] | {name: .Names[0], state: .State, status: .Status, image: .Image}'
```

### List stacks
```bash
curl -s -H "Authorization: Bearer $PORTAINER_TOKEN" \
  "http://192.168.50.204:9000/api/stacks" | jq '.[] | {name: .Name, status: .Status, type: .Type}'
```

### Container logs
```bash
curl -s -H "Authorization: Bearer $PORTAINER_TOKEN" \
  "http://192.168.50.204:9000/api/endpoints/1/docker/containers/<CONTAINER_ID>/logs?stdout=true&stderr=true&tail=50"
```

### Restart a container (CONFIRM FIRST)
```bash
# CONFIRM WITH OPERATOR BEFORE RESTARTING
curl -s -X POST -H "Authorization: Bearer $PORTAINER_TOKEN" \
  "http://192.168.50.204:9000/api/endpoints/1/docker/containers/<CONTAINER_ID>/restart"
```

### Image list
```bash
curl -s -H "Authorization: Bearer $PORTAINER_TOKEN" \
  "http://192.168.50.204:9000/api/endpoints/1/docker/images/json" | jq '.[] | {repo: .RepoTags[0], size_mb: (.Size / 1048576 | floor), created: .Created}'
```

### Volume list
```bash
curl -s -H "Authorization: Bearer $PORTAINER_TOKEN" \
  "http://192.168.50.204:9000/api/endpoints/1/docker/volumes" | jq '.Volumes[] | {name: .Name, driver: .Driver, mountpoint: .Mountpoint}'
```

## Services on Stark
NPM (Nginx Proxy Manager), Open WebUI, Mosquitto MQTT, Portainer itself
