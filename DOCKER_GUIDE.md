# 🐳 AICoder Full Docker Guide

If you want to run `aicoder` without installing Python packages on your machine, or if you want to run powerful local LLMs (like `qwen2.5-coder` or other advanced models) entirely offline, you can use our built-in Docker environment!

The repository contains a `Dockerfile` and a `docker-compose.yml` which will spin up two connected containers:
1. **Ollama**: To host your local AI models (running heavily sandboxed).
2. **AICoder**: The autonomous agent that talks to Ollama and edits your code.

---

## 🛠️ Step 1: Install Docker
Make sure you have [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running on your Windows, Mac, or Linux machine.

---

## 🚀 Step 2: Spin Up the Stack
Open your terminal in the `ai-code-py` folder, and start the services in the background:
```bash
docker-compose up -d
```
Docker will now build the `aicoder` image (this takes ~1 minute) and pull the official Ollama image.

---

## 🧠 Step 3: Download a Local Model
Ollama starts completely empty. You need to pull an LLM for the agent to use. 

The user community highly recommends `qwen2.5-coder:7b` (or `32b` if you have 24GB of RAM/VRAM) as it is the most capable local coding model.

Tell the `ollama` container to download the model (it's ~4GB):
```bash
docker-compose exec ollama ollama pull qwen2.5-coder:7b
```
*(Wait until it says "success"!)*

---

## 💻 Step 4: Run the AICoder Agent!
Now you can launch the interactive AICoder shell completely inside Docker!

Because we mapped the current folder to `/workspace` inside Docker via the `docker-compose.yml`, any file the Docker agent writes will instantly appear on your Windows hard drive.

Run:
```bash
docker-compose run --rm aicoder --model ollama
```

You are now in the Interactive Shell! Try typing:
> `audit this whole project for security flaws`

---

## ⚙️ Advanced: Running Single Commands
If you don't want the interactive shell, you can run one-off tasks:

```bash
docker-compose run --rm aicoder fix "add a rate limiter to the API" --model ollama
```

## 🧹 Cleaning Up
When you're done coding for the day, you can stop the background Ollama container to free up your computer's memory:
```bash
docker-compose down
```
*(Note: Your downloaded models are safely stored in a persistent Docker volume, so you won't need to re-download them tomorrow!)*
