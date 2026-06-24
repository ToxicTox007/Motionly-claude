---
name: motionly-remediation
description: Use this skill when executing the Motionly remediation plan safely.
---

# Skill: Motionly Remediation Plan Execution

## Purpose
This skill provides the mandatory guidelines, constraints, safety checks, and step-by-step workflow for executing the **Motionly Production Hardening & Antigravity Execution Plan** (defined in [MOTIONLY_ANTIGRAVITY_REMEDIATION_PLAN.md](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/MOTIONLY_ANTIGRAVITY_REMEDIATION_PLAN.md)) without causing regressions, introducing security holes, or making unapproved architectural or product assumptions.

---

## Mandatory Workflow
For every remediation task:
1. **Research & Verify:** Deploy one or more read-only investigator subagents to search the codebase and locate target functions/files. Confirm the audit findings are still true on current code.
2. **Formulate Plan & Get Approval:** Write the findings and proposed edits to the user in the *Standard Response Format Before Editing*.
3. **Execute:** The Implementer agent performs the smallest possible change.
4. **Test & Verify:** Run manual/automated tests and capture the exact outputs/evidence.
5. **Log:** Append the details to [REMEDIATION_LOG.md](file:///workspaces/Motionly-claude/motionly-main/Motionly-main/REMEDIATION_LOG.md).
6. **Review:** The Reviewer agent checks for scope creep, missing proof, and violations before final commit.

---

## Hard Rules
1. **Never edit files before verifying:** Always view the target file, confirm lines match, and document them first.
2. **No invented infrastructure:** Never add new third-party providers, services, databases, autoscalers, or servers unless they are already configured in the repo.
3. **Do not execute tasks in parallel:** Perform execution sequentially. Do not move onto Phase X.Y+1 until Phase X.Y is verified and logged.
4. **No silent product decisions:** Stop and ask the human operator whenever a product option or parameter (like budgets, retention periods, canonical source choice) is encountered.
5. **No drive-by refactoring:** Keep changes focused strictly on the remediation task. Avoid reformatting code or changing unrelated files.
6. **No "Production-Ready" claims:** Under no circumstances should the system be claimed as production-ready until all checklist items of Phase 13 have passed.

---

## Decision Gates (Stop & Ask Human)
You must stop and request explicit instructions when encountering:
- **Canonical Migration Source:** supabase/migrations vs infra/migrations vs resetting history.
- **Background Removal Path:** Option A (real segmentation model integration) vs Option B (disable/relabel features).
- **GPU Deployment Configuration:** Concurrency semaphore sizes, memory limits, and container split options.
- **Provider Fallback Options:** Adding paid fallback transcription APIs or external ML APIs.
- **Cost Quota Thresholds:** Setting max Runway/OpenRouter billing caps per workspace or per user.
- **Retention Purging Periods:** Deciding expiration timelines for video uploads, exports, visual cache, and logs.

---

## Subagent Policy
1. **Investigator/Inspector Subagents:** Must be spawned as **read-only** (i.e. no edit or execute-command write capabilities). Their purpose is code/symbol/config inspection.
2. **Implementer Subagent:** May write to files and run verification commands, but only inside the verified workspace paths and following strict approval.
3. **Reviewer Subagent:** Must be **read-only** (i.e. no edit writes). It inspects git diffs, rules compliance, and log formats.
4. **Communication:** Never assume status; require investigators to output file paths, line numbers, and exact configuration states, or explicitly say "not found".

---

## Required REMEDIATION_LOG.md Format
Each completed task must be appended to `REMEDIATION_LOG.md` using this exact markdown template:

```markdown
## Phase X / Task X.Y — <title>

- Date: <YYYY-MM-DD>
- Executor: <Agent Name / Role>
- Files touched:
  - [filename](file:///absolute/path/to/file#Lstart-Lend)
- Audit finding resolved: <Reference to findings in audit docs>
- What changed: <2-3 sentence summary of actual code diff>
- Verification performed: <Command run or test run>
- Verification output / evidence:
  ```
  <Paste exact command terminal output or test results here>
  ```
- Remaining caveats: <Any known limitations, performance impacts, or future work>
```

---

## Standard Response Format Before Editing
Before modifying any files, print a response following this structure:

### 🔍 Verification Report
- **Target File:** `[filename](file:///absolute/path/to/file)`
- **Target Function/Block:** Line `X` to `Y`
- **Current Behavior:** Describe current code/findings.
- **Audit Claim Status:** Verified True / False (Explain if different)

### 🛠️ Proposed Change
- **Intended Modification:** Describe the exact lines of code to add, change, or remove.
- **Safety Strategy:** Explain how regressions or security risks are mitigated.
- **Verification Plan:** The exact test command or manual check to run.

---

## Standard Response Format After Editing
After editing and testing the file, print a response following this structure:

### ✅ Implementation Summary
- **Files Modified:** `[filename](file:///absolute/path/to/file)`
- **Change Details:** Short description of the edits.
- **Test Command:** `npm run test ...` / `pytest ...`
- **Result:** Success / Failure (Include output snippet)
- **Remediation Log Entry Status:** Written to `REMEDIATION_LOG.md`
- **Ready for Reviewer Check:** Yes / No
