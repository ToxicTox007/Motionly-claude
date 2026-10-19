import { bullConnection, queueFor } from "./queue-common.mjs";
import { Job } from "bullmq";

async function run() {
  const connection = bullConnection();
  const jobId =
    "visual--a46e4e42-668b-4710-93fb-48a040c890c6--9b8f0756-f375-4504-965d-cf61913ccbeb--cb9372df-c99e-4fb0-8376-bdc91f3ff48c";

  console.log("Connecting to Redis...");
  const queue = queueFor("visual_analysis", connection);
  const job = await Job.fromId(queue, jobId);
  if (job) {
    console.log("Found job! Current state:", await job.getState());

    // Reset data attributes and retry
    const updatedData = {
      ...job.data,
      status: "queued",
      enqueued_at: new Date().toISOString(),
    };
    await job.updateData(updatedData);
    await job.retry();

    console.log("Job retried successfully! New state:", await job.getState());
  } else {
    console.log("Job not found in queue!");
  }

  await queue.close();
  await connection.quit();
  console.log("Done!");
  process.exit(0);
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
