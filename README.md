# llm4jobs v2 — Job Radar

Pulls new-grad / ≤2-year jobs straight from companies' public job-board APIs and emails a digest.
Runs free on GitHub Actions (laptop can be off), or locally with Docker as a fallback.
v1 (`../llm4jobs`) is untouched and independent.

## How it works
```
fetch (49 sources, ~30s) → skip seen / older than 3 days → title filters (senior, intern, role)
 → fetch description (Workday/SmartRecruiters only) → US-only → years (regex, then gpt-4o-mini
   only if no "N years" found) → dedupe across sources → email digest + out/jobs.json
```

| Source type | Examples | Notes |
|---|---|---|
| Greenhouse | Stripe, Anthropic, Pinterest, Cloudflare, DoorDash | official public API |
| Ashby | OpenAI, Snowflake, Notion, Ramp, Perplexity | official public API |
| Lever | Palantir | official public API |
| Workday | NVIDIA, Visa, KLA, Salesforce | unofficial JSON behind their sites |
| SmartRecruiters | ServiceNow | official public API |
| Amazon | Amazon | unofficial JSON behind amazon.jobs |
| SimplifyJobs | Google, Meta, Apple, Microsoft + hundreds | community new-grad list |

Not available via API (still covered by v1): Goldman Sachs, Lenovo, Bayview.
No third-party Python packages: standard library only.

## Run locally
```bash
python -m jobradar --dry-run     # never emails; writes out/digest-latest.html + .csv
python -m jobradar               # emails only if EMAIL_ENABLED=true in .env
```
First run ("bootstrap") looks at the last 3 days; later runs only report jobs not seen before.

## Configure
Everything is in `config/sources.toml`: add/remove companies, years cap, role keywords,
excluded keywords, internships, LLM on/off and its per-run call cap.
To find a company's board: `python tools/probe_ats.py` (edit the CANDIDATES dict).

## GitHub Actions (laptop off)
1. Repo: https://github.com/Arsh624/llm4jobs-v2 (public → unlimited free Actions minutes, runs every 15 min;
   secrets stay hidden even on a public repo).
2. Repo → Settings → Secrets and variables → Actions → add secrets:
   `OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM, EMAIL_TO`
3. Add a repository **variable** `EMAIL_ENABLED` = `true` when you want emails (default off).
4. Actions tab → job-radar → "Run workflow" to test. It then runs every 15 min.

Each run commits `state/state.json` (job ids already seen — no secrets) so the next run skips them.
If the repo is ever made private, change the cron to `*/30` to stay within the 2,000 free min/month.

## Docker fallback (if not using Actions)
```bash
docker compose up -d --build     # project "llm4jobs-v2", separate from v1
```

## Cost
APIs $0 · GitHub Actions $0 · OpenAI: regex handles most jobs, LLM only for the rest, capped at
60 calls/run (~$0.0002 each) → typically well under $1/month.
