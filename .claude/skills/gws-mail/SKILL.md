---
name: gws-mail
description: Gmail management via Google Workspace CLI. Use this skill whenever the user asks about email, inbox, unread messages, sending email, searching email, replying to a message, checking mail, reading a specific email, or managing Gmail labels. Trigger even for casual requests like "any emails from X?", "what's in my inbox?", "shoot a message to...", or "did I get a reply?". Covers reading, searching, sending, and organizing email for bh@hodgespot.com.
allowed-tools: Bash
---

# Gmail Management

Account: `bh@hodgespot.com`
CLI: `gws` — full path: `/c/Users/brian/AppData/Roaming/npm/gws`
JSON parsing: `/c/Python314/python.exe` (no jq available)
Note: When calling gws from Python subprocess on Windows, use the full path above. From bash, `gws` works directly.

---

## List Unread / Recent Emails

```bash
gws gmail users messages list \
  --params '{"userId":"me","q":"is:unread","maxResults":10}' \
  --format json 2>/dev/null
```

This returns IDs only. Fetch subjects/senders by getting each message:

```bash
gws gmail users messages get \
  --params '{"userId":"me","id":"MESSAGE_ID","format":"metadata","metadataHeaders":["From","Subject","Date"]}' \
  --format json 2>/dev/null
```

**Efficient batch subject listing** — use Python to loop:
```bash
/c/Python314/python.exe - << 'EOF'
import subprocess, json

result = subprocess.run(
    ['gws', 'gmail', 'users', 'messages', 'list',
     '--params', '{"userId":"me","q":"is:unread","maxResults":10}',
     '--format', 'json'],
    capture_output=True, text=True
)
msgs = json.loads(result.stdout).get('messages', [])

for m in msgs:
    detail = subprocess.run(
        ['gws', 'gmail', 'users', 'messages', 'get',
         '--params', f'{{"userId":"me","id":"{m["id"]}","format":"metadata","metadataHeaders":["From","Subject","Date"]}}',
         '--format', 'json'],
        capture_output=True, text=True
    )
    data = json.loads(detail.stdout)
    headers = {h['name']: h['value'] for h in data.get('payload', {}).get('headers', [])}
    print(f"ID: {m['id']}")
    print(f"  From: {headers.get('From','?')}")
    print(f"  Subject: {headers.get('Subject','?')}")
    print(f"  Date: {headers.get('Date','?')}")
    print()
EOF
```

---

## Search Inbox

Gmail query syntax — pass as the `q` parameter:

| Goal | Query |
|------|-------|
| From someone | `from:name@example.com` |
| Subject contains | `subject:keyword` |
| Date range | `after:2026/03/01 before:2026/03/23` |
| Has attachment | `has:attachment` |
| Unread only | `is:unread` |
| In label | `label:LABEL_NAME` |
| Combine | `from:boss is:unread subject:invoice` |

```bash
gws gmail users messages list \
  --params '{"userId":"me","q":"SEARCH_QUERY","maxResults":20}' \
  --format json 2>/dev/null
```

---

## Read Full Email Body

```bash
/c/Python314/python.exe - << 'EOF'
import subprocess, json, base64

msg_id = "MESSAGE_ID"

result = subprocess.run(
    ['gws', 'gmail', 'users', 'messages', 'get',
     '--params', f'{{"userId":"me","id":"{msg_id}","format":"full"}}',
     '--format', 'json'],
    capture_output=True, text=True
)
data = json.loads(result.stdout)

# Extract headers
headers = {h['name']: h['value'] for h in data.get('payload', {}).get('headers', [])}
print(f"From: {headers.get('From')}")
print(f"Subject: {headers.get('Subject')}")
print(f"Date: {headers.get('Date')}")
print("---")

# Extract body — walk parts recursively
def get_body(payload):
    if payload.get('body', {}).get('data'):
        raw = payload['body']['data']
        return base64.urlsafe_b64decode(raw + '==').decode('utf-8', errors='replace')
    for part in payload.get('parts', []):
        if part.get('mimeType') == 'text/plain':
            raw = part.get('body', {}).get('data', '')
            if raw:
                return base64.urlsafe_b64decode(raw + '==').decode('utf-8', errors='replace')
    # Fallback: any part with data
    for part in payload.get('parts', []):
        result = get_body(part)
        if result:
            return result
    return "(no text body)"

print(get_body(data.get('payload', {})))
EOF
```

---

## Send Email

Build RFC 822 message, base64url-encode it, send via API:

```bash
/c/Python314/python.exe - << 'EOF'
import subprocess, json, base64
from email.mime.text import MIMEText

to = "RECIPIENT@example.com"
subject = "SUBJECT"
body = "EMAIL BODY"
sender = "bh@hodgespot.com"

msg = MIMEText(body)
msg['to'] = to
msg['from'] = sender
msg['subject'] = subject

raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')

result = subprocess.run(
    ['gws', 'gmail', 'users', 'messages', 'send',
     '--params', '{"userId":"me"}',
     '--json', json.dumps({"raw": raw}),
     '--format', 'json'],
    capture_output=True, text=True
)
print(result.stdout or result.stderr)
EOF
```

---

## Mark as Read

```bash
gws gmail users messages modify \
  --params '{"userId":"me","id":"MESSAGE_ID"}' \
  --json '{"removeLabelIds":["UNREAD"]}' \
  --format json 2>/dev/null
```

---

## List Labels

```bash
gws gmail users labels list \
  --params '{"userId":"me"}' \
  --format json 2>/dev/null
```

---

## Output Format

When listing emails, present as a table:

| # | From | Subject | Date |
|---|------|---------|------|

When reading a single email, show: From / Subject / Date / body text (truncated at ~500 chars unless user asks for full).

When sending, confirm: ✅ Sent to [recipient] — "[subject]"
