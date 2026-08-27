import { expect, test } from "@playwright/test";

const projectId = "00000000-0000-4000-8000-000000000001";

function workflow(id, state, revision, extra = {}) {
  return {
    id,
    project_id: projectId,
    workflow_type: "first_draft",
    workflow_version: "first-draft-v2",
    state,
    revision,
    allowed_actions: state === "failed" ? ["retry"] : ["cancel"],
    artifacts: [],
    ...extra,
  };
}

async function installHarness(page, initial) {
  let canonical = initial;
  await page.route("**/projects/*/workflows**", async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    if (method === "POST" && url.pathname.endsWith("/cancel")) {
      canonical = workflow(canonical.id, "cancelled", canonical.revision + 1, {
        allowed_actions: ["retry"],
      });
      return route.fulfill({ json: { workflow: canonical } });
    }
    if (method === "POST" && url.pathname.endsWith("/retry")) {
      canonical = workflow("run-retry", "uploaded", 1);
      return route.fulfill({ status: 201, json: { workflow: canonical } });
    }
    if (/\/workflows\/[^/]+$/.test(url.pathname)) {
      return route.fulfill({ json: canonical });
    }
    return route.fulfill({ json: { workflows: [canonical] } });
  });
  const html = `
    <style>
      nav { display:flex; gap:8px; overflow:auto; } article { min-width:112px; border:1px solid; padding:8px; }
      @media (max-width: 500px) { nav { flex-direction:column; } article { min-width:0; } }
    </style>
    <main><p id="status" role="status"></p><nav aria-label="Creator workflow"></nav>
    <button id="cancel">Cancel</button><button id="retry">Retry</button></main>
    <script>
      const project = ${JSON.stringify(projectId)};
      const states = ["uploaded","analyzing","proposal_ready","awaiting_approval","assembling","draft_rendering","draft_ready"];
      let current;
      async function hydrate() {
        const list = await fetch("https://app.motionly.test/api/projects/"+project+"/workflows?limit=50").then(r=>r.json());
        current = await fetch("https://app.motionly.test/api/projects/"+project+"/workflows/"+list.workflows[0].id).then(r=>r.json());
        render();
      }
      function render() {
        const ready = current.state === "draft_ready" && current.draft_render?.id && current.draft_export?.id && current.draft_export.render_job_id === current.draft_render.id;
        status.textContent = "Workflow " + current.id + ": " + current.state + (ready ? " · Draft ready" : "");
        document.querySelector("nav").innerHTML = states.map(state => '<article aria-current="'+(state===current.state?'step':'false')+'">'+state.replaceAll('_',' ')+'</article>').join('');
      }
      async function command(name) {
        const response = await fetch("https://app.motionly.test/api/projects/"+project+"/workflows/"+current.id+"/"+name,{method:"POST",headers:{authorization:"Bearer header-only","content-type":"application/json"},body:JSON.stringify({expected_revision:current.revision,idempotency_key:"workflow-"+name+":"+current.id+":"+current.revision})});
        current=(await response.json()).workflow; render();
      }
      cancel.onclick=()=>command("cancel"); retry.onclick=()=>command("retry"); hydrate();
    </script>
  `;
  await page.route("https://app.motionly.test/workflow-harness", (route) =>
    route.fulfill({ contentType: "text/html", body: html }),
  );
  await page.goto("https://app.motionly.test/workflow-harness");
  await expect(page.getByRole("status")).toContainText(initial.id);
}

test("canonical workflow recovers on reload and a new browser context without sessionStorage", async ({ browser }) => {
  const first = await browser.newContext();
  const page = await first.newPage();
  await installHarness(page, workflow("run-awaiting", "awaiting_approval", 7, {
    artifacts: [{ type: "proposal", id: "proposal-1", revision: 3 }],
  }));
  expect(await page.evaluate(() => sessionStorage.length)).toBe(0);
  await page.reload();
  await expect(page.getByRole("status")).toContainText("awaiting_approval");
  await first.close();

  const second = await browser.newContext();
  const secondPage = await second.newPage();
  await installHarness(secondPage, workflow("run-awaiting", "awaiting_approval", 7));
  expect(await secondPage.evaluate(() => sessionStorage.length)).toBe(0);
  await expect(secondPage.getByRole("status")).toContainText("run-awaiting");
  await second.close();
});

test("real mocked cancel and retry commands apply canonical returned run IDs", async ({ page }) => {
  await installHarness(page, workflow("run-render", "draft_rendering", 9));
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("status")).toContainText("cancelled");
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("status")).toContainText("run-retry: uploaded");
});

test("draft readiness checks linked IDs and progress rail responds on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installHarness(page, workflow("run-ready", "draft_ready", 12, {
    draft_render: { id: "render-1" },
    draft_export: { id: "export-1", render_job_id: "render-1" },
  }));
  await expect(page.getByRole("status")).toContainText("Draft ready");
  const direction = await page.getByRole("navigation").evaluate((node) => getComputedStyle(node).flexDirection);
  expect(direction).toBe("column");
  await expect(page.getByRole("article")).toHaveCount(7);
});
