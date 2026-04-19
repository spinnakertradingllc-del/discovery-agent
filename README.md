# CleanPro Discovery Agent
AI-powered outbound discovery call agent for food production & dairy factory cleaning chemicals.
Powered by Groq (free) + Telnyx + Railway.

---

## Setup Guide (15 minutes)

### Step 1 — Get your free Groq API key
1. Go to console.groq.com
2. Sign up with GitHub (free, no credit card)
3. Click API Keys → Create API Key → copy it

### Step 2 — Upload to GitHub
1. Go to github.com → New repository → name it `discovery-agent`
2. Click "uploading an existing file"
3. Drag and drop ALL files from this folder → Commit changes

### Step 3 — Deploy on Railway
1. Go to railway.app → sign in with GitHub
2. New Project → Deploy from GitHub repo → select `discovery-agent`
3. Railway builds automatically (takes ~2 minutes)
4. Click your service → Settings → copy the public domain:
   Example: `https://discovery-agent-production.up.railway.app`

### Step 4 — Set environment variables in Railway
Go to your project → Variables tab → add these one by one:

| Variable              | Value                                      |
|-----------------------|--------------------------------------------|
| GROQ_API_KEY          | gsk_... (from console.groq.com)           |
| TELNYX_API_KEY        | KEY_... (from Telnyx portal → Auth)       |
| TELNYX_FROM_NUMBER    | Your Telnyx phone number e.g. +1234567890 |
| TELNYX_CONNECTION_ID  | From Telnyx → Voice API app → Details     |
| WEBHOOK_BASE_URL      | Your Railway domain (Step 3)              |

### Step 5 — Add webhook URL to Telnyx
In Telnyx portal → Programmable Voice → your app → Webhook URL:
```
https://your-app.railway.app/webhook
```
Save → Done ✅

---

## Making an outbound call
Send a POST request to your Railway URL:
```
POST https://your-app.railway.app/outbound
Content-Type: application/json

{ "to": "+254XXXXXXXXX" }
```

## Viewing captured leads
```
GET https://your-app.railway.app/leads
```

## Checking server health
```
GET https://your-app.railway.app/health
```
This shows whether your API keys are configured correctly.

---

## Files
| File            | Purpose                                      |
|-----------------|----------------------------------------------|
| main.py         | Full webhook server + AI agent logic         |
| requirements.txt| Python dependencies                          |
| railway.toml    | Railway deployment config                    |
| .env.example    | Environment variable template (don't commit) |
| .gitignore      | Keeps secrets out of GitHub                  |
