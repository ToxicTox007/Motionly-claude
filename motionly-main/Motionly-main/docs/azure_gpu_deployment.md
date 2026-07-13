# Deploying Motionly on Azure with GPU Workers

This guide outlines how to deploy the Motionly project on Microsoft Azure, with a specific focus on running the GPU-enabled workers (`gpu-inference-worker`, `matte-worker`, and `gpu-ffmpeg-worker`).

---

## Architecture Overview

Motionly is built as a modular, containerized application using a queue-based architecture (Redis + BullMQ) to distribute tasks. The workload is split into:
1. **CPU Workloads**: Next.js frontend (`web`), FastAPI/Node API (`api`), and standard CPU queue workers (`cpu-ffmpeg-worker`, `provider-worker`, `planning-worker`, `publishing-worker`).
2. **GPU Workloads**:
   - `gpu-inference-worker`: PyTorch-based ML tasks (`visual_analysis`, `transcription`).
   - `matte-worker`: PyTorch-based green-screen matting (`matte_extraction`).
   - `gpu-ffmpeg-worker`: GPU-accelerated video rendering via NVIDIA NVENC (`render_gpu`).

### GPU Worker Base Config
In the [docker-compose.gpu.yml](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/infra/docker-compose.gpu.yml) (defined under `infra/docker-compose.gpu.yml`), GPU services utilize `gpus: all` and mount:
- A shared media volume (for input/output video assets).
- A model cache volume (for caching HuggingFace/PyTorch weights).

---

## Azure VM Size Recommendations (N-Series)

Azure's GPU-enabled virtual machines are the **N-series**. Choose your size based on VRAM requirements, cost, and availability:

| VM Size | GPU Model | VRAM | Best For | Expected Cost |
| :--- | :--- | :--- | :--- | :--- |
| **Standard_NC4as_T4_v3** | 1x NVIDIA Tesla T4 | 16 GB | Cost-effective inference & transcription, standard rendering. | Low |
| **Standard_NC8as_T4_v3** | 1x NVIDIA Tesla T4 | 16 GB | Same as above with more CPU cores (8 vCPUs) for data pipelines. | Medium-Low |
| **Standard_NC6s_v3** | 1x NVIDIA Tesla V100 | 16 GB | Faster ML inference and rendering. | Medium |
| **Standard_ND96asr_v4** | 8x NVIDIA A100 | 80 GB | Large-scale production clusters with high-concurrency needs. | Very High |

> [!TIP]
> For most workloads, **NCas_T4_v3** series VMs provide the best balance of cost and performance for running transcription, visual analysis, and NVENC rendering.

---

## Deployment Option 1: Azure Container Apps (ACA)
*Recommended for serverless workloads with auto-scaling to zero.*

Azure Container Apps supports GPU-enabled workload profiles, allowing you to run workers that only scale up when jobs are in the queue.

### Step 1: Create Container Registry & Resources
Create your Resource Group and Azure Container Registry (ACR) to host the Docker images.

```bash
# Create Resource Group
az group create --name motionly-prod-rg --location eastus

# Create Container Registry
az acr create --resource-group motionly-prod-rg --name motionlyregistry --sku Premium
```

### Step 2: Build and Push Docker Images
Authenticate to ACR and push the worker and API images:

```bash
# Log in to registry
az acr login --name motionlyregistry

# Build and push GPU Worker
docker build -t motionlyregistry.azurecr.io/motionly-gpu-worker:latest -f apps/worker/Dockerfile.gpu .
docker push motionlyregistry.azurecr.io/motionly-gpu-worker:latest

# Build and push Python/FFmpeg Worker (for GPU FFMPEG worker)
docker build -t motionlyregistry.azurecr.io/motionly-python-worker:latest -f apps/worker/Dockerfile.python .
docker push motionlyregistry.azurecr.io/motionly-python-worker:latest
```

### Step 3: Set up ACA Environment with GPU Workload Profile
Create an ACA Environment supporting GPU workload profiles:

```bash
az containerapp env create \
  --name motionly-env \
  --resource-group motionly-prod-rg \
  --location eastus \
  --enable-workload-profiles
```
*Note: Make sure to select a region that supports GPU workload profiles (e.g., `eastus`, `westus3`). Ensure your subscription has GPU quota.*

### Step 4: Deploy GPU Workers with Scaling Rules
Deploy the Container App specifying the GPU profile and a KEDA Redis scaler. The KEDA scaler monitors the Redis queue size to spin up GPU instances only when tasks exist.

Example CLI deployment command:
```bash
az containerapp create \
  --name gpu-inference-worker \
  --resource-group motionly-prod-rg \
  --environment motionly-env \
  --image motionlyregistry.azurecr.io/motionly-gpu-worker:latest \
  --workload-profile-name "GPU" \
  --cpu 4 --memory 16Gi \
  --min-replicas 0 \
  --max-replicas 4 \
  --env-vars \
    REDIS_URL="redis://:<password>@<redis-host>:6379/0" \
    WORKER_QUEUES="visual_analysis,transcription" \
    WORKER_CONCURRENCY="1" \
    MODEL_PREFETCH="true" \
  --scale-rule-name redis-queue-scaler \
  --scale-rule-type redis \
  --scale-rule-metadata \
    address="<redis-host>:6379" \
    password="<redis-password>" \
    queueName="visual_analysis" \
    queueLength="3"
```

---

## Deployment Option 2: Azure Kubernetes Service (AKS)
*Recommended for enterprise multi-node production topologies with dedicated compute.*

AKS gives you fine-grained control over scheduling, storage classes (using Azure Files for shared media volumes), and cluster orchestration.

### Step 1: Create AKS Cluster with GPU Node Pool
1. Create a basic AKS cluster with a standard CPU system pool:
   ```bash
   az aks create \
     --resource-group motionly-prod-rg \
     --name motionly-cluster \
     --node-count 2 \
     --generate-ssh-keys
   ```

2. Add a GPU-enabled node pool (using N-series VMs) to the cluster:
   ```bash
   az aks nodepool add \
     --resource-group motionly-prod-rg \
     --cluster-name motionly-cluster \
     --name gpunodepool \
     --node-count 1 \
     --node-vm-size Standard_NC4as_T4_v3 \
     --labels accelerator=nvidia-t4
   ```

### Step 2: Install NVIDIA GPU Operator
The NVIDIA GPU Operator automates the installation of CUDA drivers and the Kubernetes device plugin.

```bash
# Add Helm Repository
helm repo add nvidia https://helm.github.io/gpu-operator
helm repo update

# Install Operator
helm install gpu-operator nvidia/gpu-operator \
  --namespace gpu-operator \
  --create-namespace
```

### Step 3: Provision Shared Storage (Azure Files)
Since multiple workers read and write to `/var/data/motionly` (defined as `motionly-media`), you must use a ReadWriteMany (RWX) storage class. Azure Files (premium SSD performance tier recommended) is ideal.

Create a Kubernetes StorageClass:
```yaml
# azure-files-sc.yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: motionly-shared-storage
provisioner: file.csi.azure.com
allowVolumeExpansion: true
mountOptions:
  - dir_mode=0777
  - file_mode=0777
  - uid=1000
  - gid=1000
  - mfsymlinks
  - cache=strict
  - nosharesock
parameters:
  skuName: Premium_LRS
```

Create a PersistentVolumeClaim:
```yaml
# motionly-media-pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: motionly-media-pvc
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: motionly-shared-storage
  resources:
    requests:
      storage: 100Gi
```

### Step 4: Deploy GPU Worker Pods
In your deployment specification, reference the GPU node selector, tolerations, and request GPU limits:

```yaml
# gpu-inference-worker-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gpu-inference-worker
spec:
  replicas: 1
  selector:
    matchLabels:
      app: gpu-inference-worker
  template:
    metadata:
      labels:
        app: gpu-inference-worker
    spec:
      tolerations:
      - key: "sku"
        operator: "Equal"
        value: "gpu"
        effect: "NoSchedule"
      nodeSelector:
        accelerator: nvidia-t4
      containers:
      - name: gpu-worker
        image: motionlyregistry.azurecr.io/motionly-gpu-worker:latest
        resources:
          limits:
            nvidia.com/gpu: 1  # Requests 1 physical GPU
            memory: 12Gi
            cpu: "4"
          requests:
            nvidia.com/gpu: 1
            memory: 8Gi
            cpu: "2"
        env:
        - name: REDIS_URL
          value: "redis://:YOUR_PASSWORD@redis-service:6379/0"
        - name: WORKER_QUEUES
          value: "visual_analysis,transcription"
        - name: WORKER_CONCURRENCY
          value: "1"
        volumeMounts:
        - name: media-storage
          mountPath: /var/data/motionly
      volumes:
      - name: media-storage
        persistentVolumeClaim:
          claimName: motionly-media-pvc
```

---

## Deployment Option 3: Azure Virtual Machine with Docker Compose
*Recommended for staging, evaluation, or single-host deployments.*

This matches the existing [docker-compose.gpu.yml](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/infra/docker-compose.gpu.yml) structure closely.

### Step 1: Provision GPU VM on Azure
Launch an N-series virtual machine (e.g. `Standard_NC4as_T4_v3` running Ubuntu Server 22.04 LTS).

```bash
az vm create \
  --resource-group motionly-prod-rg \
  --name motionly-gpu-vm \
  --image Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest \
  --size Standard_NC4as_T4_v3 \
  --admin-username azureuser \
  --generate-ssh-keys
```

### Step 2: Install CUDA Drivers & NVIDIA Container Toolkit
Log into the VM via SSH and install the official NVIDIA drivers along with the Container Toolkit (which lets Docker interface with host GPUs):

```bash
# Update package index and install build dependencies
sudo apt-get update
sudo apt-get install -y build-essential ubuntu-drivers-common
sudo ubuntu-drivers install

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Install NVIDIA Container Toolkit
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# Configure Docker to run with NVIDIA Runtime
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### Step 3: Verify GPU Passthrough to Docker
Run a test container to verify that the GPU is accessible inside Docker:

```bash
sudo docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```
If successful, you should see the `nvidia-smi` GPU statistics table.

### Step 4: Configure and Run the Topology
1. Clone your repository onto the VM.
2. Build the secrets directory under `/infra/secrets/` as requested in [infra/secrets/README.md](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/infra/secrets/README.md).
3. Create `.env.production` in the root folder.
4. Launch the application:
   ```bash
   sudo docker compose --env-file .env.production -f infra/docker-compose.gpu.yml up -d
   ```

---

## Production Best Practices for Azure GPU Workloads

### 1. Scaling down to zero (Serverless)
GPUs in Azure are billed by the second (in ACA) or by the hour (VMs/AKS). To optimize costs:
- Use **KEDA** on ACA or AKS to scale your GPU nodes/workers to `0` when there are no jobs in Redis.
- If using AKS, configure the cluster autoscaler to scale down the GPU node pool to 0 instances when all pods terminate.

### 2. Monitoring GPU Metrics
Motionly exports prometheus metrics at `GET /metrics`. You can configure:
- **Azure Monitor for Prometheus**: Scrape worker metrics and track GPU utilization.
- Install the **NVIDIA DCGM Exporter** on AKS to get raw GPU metrics (temperature, VRAM usage, engine clocks) visualized in Azure Managed Grafana.

### 3. Local Model Cache
Since workers prefetch HuggingFace/Torch models, mount a fast persistent volume (like Azure Premium SSD) to `/var/cache/motionly` (mapped as `motionly-model-cache`). This prevents the worker from re-downloading gigabytes of model weights (Whisper, Demucs, matting models) on every startup or scale-up.
