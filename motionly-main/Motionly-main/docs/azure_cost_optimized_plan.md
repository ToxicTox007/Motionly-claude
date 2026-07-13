# Cost-Optimized Azure GPU Testing Plan ($200 Credits)

Since you already have the core infrastructure running elsewhere (API, Redis, and Database on Railway; Frontend on Vercel), **you do not need to redeploy the entire stack on Azure.** 

Instead, you can deploy **only the remaining GPU/CPU workers** on Azure, and connect them directly to your existing Railway Redis instance. This hybrid approach is the most efficient way to test correctness without burning your $200 credits.

---

## 1. The Cost Challenge
A standard Azure N-series GPU VM (e.g., `Standard_NC4as_T4_v3` with 1x NVIDIA T4 GPU) costs about **$0.53/hour** (~$380/month if left running 24/7). If you leave it running continuously, your $200 credit will disappear in **~15 days**.

### The Solution: "Start-on-Demand" single VM
By deploying all remaining workers on a single GPU VM using Docker Compose and turning the VM **OFF** when you are not actively testing, you only pay for active development time. 
* Running 10 hours a week for testing: **~$5.30 / week**. Your $200 credits will last for months!

---

## 2. Hybrid Architecture (How it Works)

```mermaid
graph TD
    Vercel[Vercel: next.js Frontend] --> RailwayAPI[Railway: API]
    RailwayAPI --> RailwayRedis[(Railway: Redis Queue)]
    
    subgraph Azure GPU VM (Staged)
        direction TB
        GPU-Inference[gpu-inference-worker] --> RailwayRedis
        Matte[matte-worker] --> RailwayRedis
        GPU-FFmpeg[gpu-ffmpeg-worker] --> RailwayRedis
        CPU-FFmpeg[cpu-ffmpeg-worker] --> RailwayRedis
        Demucs[demucs-worker] --> RailwayRedis
        Remotion[remotion-worker] --> RailwayRedis
    end
    
    GPU-Inference --> Supabase[(Supabase DB)]
```

The Azure workers will pull jobs directly from your Railway Redis queue, process them using the Azure VM's GPU/CPU, and write the output back to Supabase / your media storage.

---

## 3. Step-by-Step Testing & Staging Plan

### Step 1: Provision the VM with Auto-Shutdown
To guarantee you don't accidentally leave the VM running over the weekend, enable **Auto-Shutdown** during VM creation.

Run this Azure CLI command to create the VM:
```bash
az vm create \
  --resource-group motionly-prod-rg \
  --name motionly-gpu-test-vm \
  --image Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest \
  --size Standard_NC4as_T4_v3 \
  --admin-username azureuser \
  --generate-ssh-keys

# Configure Auto-Shutdown at 7:00 PM daily (timezone: UTC)
az vm auto-shutdown \
  --resource-group motionly-prod-rg \
  --name motionly-gpu-test-vm \
  --time 1900 \
  --email hraj7435@gmail.com
```

### Step 2: Install NVIDIA Docker Drivers (SSH into VM)
Once the VM is ready, SSH into it and run the setup to enable GPU passthrough to Docker:
```bash
sudo apt-get update && sudo apt-get install -y build-essential ubuntu-drivers-common
sudo ubuntu-drivers install

# Install Docker & Container Toolkit
curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### Step 3: Run Only the Missing Workers
Instead of running Postgres, Redis, and the API on Azure, you can define a minimized `docker-compose.test.yml` that only starts the workers.

Create a `docker-compose.test.yml` on the VM:
```yaml
version: '3.8'

x-media-environment: &media-environment
  REDIS_URL: "redis://:${REDIS_PASSWORD}@${REDIS_HOST}:${REDIS_PORT}/0"
  SUPABASE_URL: "https://frgueaxalqjwsolxltng.supabase.co"
  SUPABASE_SERVICE_ROLE_KEY: "${SUPABASE_SERVICE_ROLE_KEY}"
  SUPABASE_JWT_SECRET: "${SUPABASE_JWT_SECRET}"
  APP_ENV: production
  PROCESS_ROLE: worker
  MEDIA_WORK_DIR: /var/data/motionly

services:
  gpu-inference-worker:
    image: motionlyregistry.azurecr.io/motionly-gpu-worker:latest
    gpus: all
    environment:
      <<: *media-environment
      WORKER_QUEUES: visual_analysis,transcription
      WORKER_CONCURRENCY: "1"
    volumes:
      - motionly-media:/var/data/motionly

  gpu-ffmpeg-worker:
    image: motionlyregistry.azurecr.io/motionly-python-worker:latest
    gpus: all
    environment:
      <<: *media-environment
      WORKER_QUEUES: render_gpu
      WORKER_CONCURRENCY: "1"
      FFMPEG_GPU_ENABLED: "true"
      FFMPEG_GPU_ENCODER: h264_nvenc
    volumes:
      - motionly-media:/var/data/motionly

  cpu-ffmpeg-worker:
    image: motionlyregistry.azurecr.io/motionly-python-worker:latest
    environment:
      <<: *media-environment
      WORKER_QUEUES: upload_preprocess,render
      WORKER_CONCURRENCY: "2"
    volumes:
      - motionly-media:/var/data/motionly

  remotion-worker:
    image: motionlyregistry.azurecr.io/motionly-remotion-worker:latest
    environment:
      <<: *media-environment
      WORKER_QUEUES: render_remotion
      REMOTION_ENABLED: "true"
      WORKER_CONCURRENCY: "1"
    volumes:
      - motionly-media:/var/data/motionly

volumes:
  motionly-media:
```

### Step 4: Run & Test
Provide the environment variables (fetching the `REDIS_PASSWORD` and host details from your active Railway configuration) and spin up the containers:
```bash
docker compose -f docker-compose.test.yml up -d
```
Verify the logs:
```bash
docker compose -f docker-compose.test.yml logs -f
```

---

## 4. Easy CLI Start/Stop Commands
Save these scripts on your local machine (or in your workspace) to turn the Azure VM on/off with a single click before/after you test:

### Start VM (when you begin testing)
```bash
az vm start --resource-group motionly-prod-rg --name motionly-gpu-test-vm
```

### Stop VM (when you are done)
> [!IMPORTANT]
> You must use `deallocate` instead of `stop` to release the compute hardware. If you only "stop" it, Azure still bills you for the reserved compute resources!
```bash
az vm deallocate --resource-group motionly-prod-rg --name motionly-gpu-test-vm
```
