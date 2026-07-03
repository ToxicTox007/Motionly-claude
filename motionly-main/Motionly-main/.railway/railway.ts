import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { defineRailway, github, preserve, project, redis, service } = require(
  "../node_modules/railway/dist/iac/index.cjs",
);

const source = github("ToxicTox007/Motionly-claude", {
  rootDirectory: "motionly-main/Motionly-main",
});

export default defineRailway(() => {
  const cache = redis("Redis");

  const api = service("motionly-api", {
    source,
    build: {
      builder: "DOCKERFILE",
      buildEnvironment: "V3",
      dockerfilePath: "apps/api/Dockerfile",
    },
    deploy: {
      healthcheckPath: "/health/live",
      healthcheckTimeout: 120,
      numReplicas: 1,
      sleepApplication: false,
    },
    env: {
      APP_ENV: "production",
      PORT: "8000",
      SUPABASE_URL: preserve(),
      SUPABASE_SERVICE_ROLE_KEY: preserve(),
      SUPABASE_JWT_SECRET: preserve(),
      SUPABASE_ANON_KEY: preserve(),
      REDIS_URL: cache.env.REDIS_URL,
      ADMIN_API_TOKEN: preserve(),
      PUBLISH_TOKEN_ENCRYPTION_KEY: preserve(),
      PUBLISH_OAUTH_STATE_SECRET: preserve(),
      METRICS_BEARER_TOKEN: preserve(),
      API_PUBLIC_BASE_URL: preserve(),
      API_CORS_ORIGINS: preserve(),
      UPLOAD_STORAGE_BUCKET: "raw-uploads",
      EXPORT_STORAGE_BUCKET: "exports",
      RENDER_EXECUTE_FFMPEG: "true",
      TRANSCRIPTION_PROVIDER: "groq",
      TRANSCRIPTION_MODEL: "whisper-large-v3-turbo",
      GROQ_API_KEY: preserve(),
      EDIT_PLANNER_PROVIDER: "openrouter",
      EDIT_PLANNER_MODEL: "openrouter/free",
      OPENROUTER_API_KEY: preserve(),
      STOCK_VIDEO_PROVIDER: "pexels_pixabay",
      PEXELS_API_KEY: preserve(),
      PIXABAY_API_KEY: preserve(),
      SENTRY_DSN: preserve(),
      LIGHTNING_USER_ID: preserve(),
      LIGHTNING_API_KEY: preserve(),
      LIGHTNING_TEAMSPACE: preserve(),
      LIGHTNING_VISUAL_DEPLOYMENT: "motionly-visual-worker",
      LIGHTNING_MATTE_DEPLOYMENT: "motionly-matte-worker",
      LIGHTNING_RENDER_DEPLOYMENT: "motionly-gpu-render-worker",
    },
  });

  const workerEnv = {
    APP_ENV: api.env.APP_ENV,
    SUPABASE_URL: api.env.SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY: api.env.SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_JWT_SECRET: api.env.SUPABASE_JWT_SECRET,
    REDIS_URL: cache.env.REDIS_URL,
    ADMIN_API_TOKEN: api.env.ADMIN_API_TOKEN,
    WORKER_API_BASE_URL: api.env.API_PUBLIC_BASE_URL,
    WORKER_API_TOKEN: api.env.ADMIN_API_TOKEN,
    PUBLISH_TOKEN_ENCRYPTION_KEY: api.env.PUBLISH_TOKEN_ENCRYPTION_KEY,
    UPLOAD_STORAGE_BUCKET: api.env.UPLOAD_STORAGE_BUCKET,
    EXPORT_STORAGE_BUCKET: api.env.EXPORT_STORAGE_BUCKET,
    RENDER_EXECUTE_FFMPEG: api.env.RENDER_EXECUTE_FFMPEG,
    TRANSCRIPTION_PROVIDER: api.env.TRANSCRIPTION_PROVIDER,
    TRANSCRIPTION_MODEL: api.env.TRANSCRIPTION_MODEL,
    GROQ_API_KEY: api.env.GROQ_API_KEY,
    EDIT_PLANNER_PROVIDER: api.env.EDIT_PLANNER_PROVIDER,
    EDIT_PLANNER_MODEL: api.env.EDIT_PLANNER_MODEL,
    OPENROUTER_API_KEY: api.env.OPENROUTER_API_KEY,
    STOCK_VIDEO_PROVIDER: api.env.STOCK_VIDEO_PROVIDER,
    PEXELS_API_KEY: api.env.PEXELS_API_KEY,
    PIXABAY_API_KEY: api.env.PIXABAY_API_KEY,
    STEM_SEPARATION_PROVIDER: "demucs",
    DEMUCS_MODEL: "htdemucs",
    SENTRY_DSN: api.env.SENTRY_DSN,
  };

  const pythonWorker = {
    source,
    build: {
      builder: "DOCKERFILE" as const,
      buildEnvironment: "V3" as const,
      dockerfilePath: "apps/worker/Dockerfile.python",
    },
    deploy: {
      numReplicas: 1,
      sleepApplication: false,
    },
  };

  const remotionWorker = {
    source,
    build: {
      builder: "DOCKERFILE" as const,
      buildEnvironment: "V3" as const,
      dockerfilePath: "apps/worker/Dockerfile",
    },
    deploy: {
      numReplicas: 1,
      sleepApplication: false,
    },
  };

  const demucsWorker = {
    source,
    build: {
      builder: "DOCKERFILE" as const,
      buildEnvironment: "V3" as const,
      dockerfilePath: "apps/worker/Dockerfile.python.demucs",
    },
    deploy: {
      numReplicas: 1,
      sleepApplication: false,
    },
  };

  return project("Motionly", {
    resources: [
      cache,
      api,
      service("motionly-provider-worker", {
        ...pythonWorker,
        env: {
          ...workerEnv,
          WORKER_QUEUES: "provider_calls",
        },
      }),
      service("motionly-planning-worker", {
        ...pythonWorker,
        env: {
          ...workerEnv,
          GPU_AUTOSCALING_CRON: "*/2 * * * *",
          WORKER_QUEUES: "planning,stock_search,audio,intelligence,maintenance",
        },
      }),
      service("motionly-publishing-worker", {
        ...pythonWorker,
        env: {
          ...workerEnv,
          WORKER_QUEUES: "publish",
        },
      }),
      service("motionly-cpu-ffmpeg-worker", {
        ...pythonWorker,
        env: {
          ...workerEnv,
          WORKER_QUEUES: "upload_preprocess,render",
        },
      }),
      service("motionly-audio-demucs-worker", {
        ...demucsWorker,
        env: {
          ...workerEnv,
          MODEL_PREFETCH: "true",
          WORKER_QUEUES: "audio_demucs",
        },
      }),
      service("motionly-remotion-worker", {
        ...remotionWorker,
        env: {
          ...workerEnv,
          REMOTION_ENABLED: "true",
          WORKER_QUEUES: "render_remotion",
        },
      }),
    ],
  });
});
