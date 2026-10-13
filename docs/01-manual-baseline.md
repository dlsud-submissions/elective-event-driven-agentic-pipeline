# 01 — Manual Baseline (Current State)

> **Assumption note.** The lab PDF introduces the manual process with "perform the following steps by hand" but the step list itself did not survive in the text version of the handout. The steps below are the standard manual production release for a .NET/Python microservice with a SQL database and match the three agent roles the brief requires (CI/CD quality control → deploy + migration → SRE monitoring). Adjust the table if your handout lists different steps.

## 1. Manual process, step by step

| # | Manual step | Performed by | Typical duration | Variable handed to next step |
|---|---|---|---|---|
| M1 | Receive "merge done" message in chat | Release engineer | minutes–hours (waits for a human to notice) | commit hash (copy/pasted) |
| M2 | Pull branch, run test suite locally or in CI UI | Engineer | 5–20 min | test result (read by eye) |
| M3 | Scroll the console log and decide "pass/fail" | Engineer | 2–10 min | verdict (verbal / chat) |
| M4 | Build dependencies and container image, tag it | Engineer | 5–15 min | image tag (typed by hand) |
| M5 | Read EF/SQL migration, judge risk | Senior engineer | 5–30 min | go/no-go (verbal) |
| M6 | Run migration against production DB | DBA / engineer | 1–10 min | migration id |
| M7 | Deploy container to production | Engineer | 2–10 min | deployed image tag |
| M8 | Watch dashboards / tail logs for errors | On-call SRE | 15–60 min of attention | error observations |
| M9 | If bad: redeploy old image, revert migration by hand | On-call SRE | 10–45 min under pressure | rollback target (remembered, not recorded) |

## 2. Where human intervention causes delay

- **M1 / M3 – notification and interpretation lag.** Nothing starts until a person notices the merge, and nothing proceeds until a person has read a raw log.
- **M5 – approval is informal.** Reviews happen in chat; there is no durable record of *who* approved *which* migration.
- **M8 – attention-based monitoring.** Detection depends on someone watching a graph; error spikes after hours are found late.

## 3. Where variables are passed between systems (and can be wrong)

| Handoff | Mechanism today | Failure mode |
|---|---|---|
| CI → engineer (commit hash) | Copy/paste from UI/chat | Wrong or stale hash deployed |
| Test run → verdict | Human reads console | Missed failure in long logs; "flaky, ignore" bias |
| Build → deploy (image tag) | Typed into a command | Typo deploys `latest` or a wrong tag |
| Deploy → rollback (previous tag) | Remembered / looked up under pressure | Rollback to the wrong version |
| Migration → rollback | Separate manual DOWN script | Schema and app version drift apart |

## 4. Where errors typically happen

1. **Skipped or misread tests** (M2–M3) because of time pressure.
2. **Migration run before the app is verified** (M5–M6), with no tested path back.
3. **No explicit rollback target recorded** (M7→M9).
4. **Late detection** of 5xx spikes or latency regressions (M8).
5. **No audit trail**: the only record is chat history.

## 5. Baseline diagram

```text
 merge ──► [M1 human notices] ──► [M2 run tests] ──► [M3 human reads log]
                                                         │ pass?
                                                         ▼
 [M4 build image] ──► [M5 human reviews migration] ──► [M6 run migration]
                                                         ▼
                 [M7 deploy] ──► [M8 human watches graphs] ──► [M9 manual rollback]
 ▲ every arrow is a human copy/paste or a verbal handoff; no shared state, no audit log
```
