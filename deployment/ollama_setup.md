# Ollama Setup & Deployment Guide (Oracle Cloud Always Free VM)

This document outlines the step-by-step instructions for deploying and running Ollama with the `qwen2.5:7b` model on an **Oracle Cloud Always Free VM.Standard.A1.Flex** instance (4 OCPU ARM, 24GB RAM, Ubuntu/Debian Linux).

---

## 1. Install Ollama on Oracle Cloud VM

Run the official Linux installation script:
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

---

## 2. Pull the Qwen 2.5 7B Model

Run the pull command once after installation:
```bash
ollama pull qwen2.5:7b
```

To verify the model is pulled and ready:
```bash
ollama list
```
You should see `qwen2.5:7b` listed.

---

## 3. Configure Systemd Service

Verify if Ollama is running as a background systemd service:
```bash
systemctl status ollama
```

If the service is not already created automatically by the installer, create it manually:
```bash
sudo nano /etc/systemd/system/ollama.service
```

Paste the following systemd unit configuration:
```ini
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
ExecStart=/usr/local/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
Environment="OLLAMA_HOST=0.0.0.0:11434"

[Install]
WantedBy=multi-user.target
```

---

## 4. Enable and Start Ollama Service

Reload systemd daemon and enable auto-start on boot:
```bash
sudo systemctl daemon-reload
sudo systemctl enable ollama
sudo systemctl start ollama
```

Verify service health:
```bash
curl http://localhost:11434/api/tags
```
Response will return JSON containing `"models": [...]` with `qwen2.5:7b`.

---

## 5. GetHired Integration Verification

In `config.json`, verify that the Ollama configuration points to your local host:
```json
"ollama": {
  "enabled": true,
  "model": "qwen2.5:7b",
  "base_url": "http://localhost:11434",
  "timeout_seconds": 25,
  "fallback_if_unavailable": true
}
```

When API key quotas are exhausted, GetHired automatically routes scoring requests to Tier 4 (`ollama_local_qwen2.5`) with high-medium confidence.
