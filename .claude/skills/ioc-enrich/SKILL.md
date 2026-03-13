---
name: ioc-enrich
description: Enrich indicators of compromise (IPs, domains, hashes) against threat intelligence feeds — VirusTotal, AbuseIPDB, Shodan.
allowed-tools: Bash
---

# IOC Enrichment — Threat Intelligence Lookups

## What This Does
Takes an IP, domain, or file hash and queries public threat intelligence APIs to assess risk.

## How To Run

### VirusTotal — IP reputation
```bash
curl -s -H "x-apikey: $VIRUSTOTAL_API_KEY" \
  "https://www.virustotal.com/api/v3/ip_addresses/<IP>" | jq '{
    reputation: .data.attributes.reputation,
    malicious: .data.attributes.last_analysis_stats.malicious,
    country: .data.attributes.country,
    asn: .data.attributes.asn,
    owner: .data.attributes.as_owner
  }'
```

### VirusTotal — Domain reputation
```bash
curl -s -H "x-apikey: $VIRUSTOTAL_API_KEY" \
  "https://www.virustotal.com/api/v3/domains/<DOMAIN>" | jq '{
    reputation: .data.attributes.reputation,
    malicious: .data.attributes.last_analysis_stats.malicious,
    registrar: .data.attributes.registrar,
    creation_date: .data.attributes.creation_date
  }'
```

### VirusTotal — File hash lookup
```bash
curl -s -H "x-apikey: $VIRUSTOTAL_API_KEY" \
  "https://www.virustotal.com/api/v3/files/<HASH>" | jq '{
    malicious: .data.attributes.last_analysis_stats.malicious,
    type: .data.attributes.type_description,
    names: .data.attributes.names[:3]
  }'
```

### AbuseIPDB — IP check
```bash
curl -s -H "Key: $ABUSEIPDB_API_KEY" -H "Accept: application/json" \
  "https://api.abuseipdb.com/api/v2/check?ipAddress=<IP>&maxAgeInDays=90" | jq '{
    abuse_score: .data.abuseConfidenceScore,
    country: .data.countryCode,
    isp: .data.isp,
    total_reports: .data.totalReports
  }'
```

### Shodan — IP info
```bash
curl -s "https://api.shodan.io/shodan/host/<IP>?key=$SHODAN_API_KEY" | jq '{
    ip: .ip_str,
    org: .org,
    os: .os,
    ports: .ports,
    vulns: .vulns
  }'
```

## Report Format
| IOC | Type | VT Score | AbuseIPDB | Shodan Ports | Verdict |
|-----|------|----------|-----------|--------------|---------|
| 1.2.3.4 | IP | 5/90 malicious | 85% abuse | 22,80,443 | HIGH RISK |
