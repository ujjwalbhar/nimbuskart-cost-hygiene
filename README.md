# NimbusKart Cost Hygiene

## Overview

This repo is a practical DevOps assignment submission for Code & Conscience. It solves a real problem: NimbusKart's AWS bill jumped from ~$400/month to ~$2,100/month because of orphaned resources nobody cleaned up. This repo provisions NimbusKart's staging infrastructure with Terraform (targeting LocalStack), runs a Python script called the Cost Janitor that finds wasteful resources, and wires it all together with a GitHub Actions workflow that runs on every pull request.

---

## How to Run Locally

### Prerequisites

- Docker
- Terraform >= 1.7.0
- Python >= 3.10
- pip

### Step 1: Clone the repo

```bash
git clone https://github.com/ujjwalbhar/nimbuskart-cost-hygiene.git
cd nimbuskart-cost-hygiene
```

### Step 2: Start LocalStack

```bash
docker run --rm -d -p 4566:4566 --name localstack localstack/localstack
```

### Step 3: Apply Terraform

```bash
cd terraform

# Option A: using tflocal (recommended)
pip install terraform-local
tflocal init
tflocal apply -auto-approve

# Option B: plain terraform (provider already points at LocalStack)
terraform init
terraform apply -auto-approve

cd ..
```

### Step 4: Run the Cost Janitor

```bash
cd janitor
pip install -r requirements.txt

# Dry-run (default, safe)
python janitor.py --dry-run

# Delete mode (will delete orphans, skips Protected=true resources)
python janitor.py --delete

# Custom stopped-instance threshold (default: 14 days)
python janitor.py --dry-run --days 7
```

Outputs:
- `janitor/report.json` — machine-readable report
- `janitor/report.md` — human-readable Markdown summary

### Step 5: Stop LocalStack

```bash
docker stop localstack
```

---

## Architecture

```
+---------------------------+
|        GitHub PR          |
+-------------+-------------+
              |
              v
+---------------------------+
|   GitHub Actions Workflow  |
|  cost-janitor.yml         |
+--+--------+---------------+
   |        |
   v        v
+------+  +---------------------------+
| Local|  |   LocalStack (Docker)     |
| Stack|  |   AWS APIs (EC2/S3/IAM)   |
+------+  +-------------+-------------+
              |
       +------v------+
       |  Terraform   |
       |  (infra/)    |
       |  - VPC       |
       |  - Subnets   |
       |  - EC2 x2    |
       |  - S3 Logs   |
       |  - EBS orphan|
       +------+-------+
              |
       +------v-----------+
       |  Cost Janitor    |
       |  (janitor.py)    |
       |  - unattached EBS|
       |  - stopped EC2   |
       |  - unused EIPs   |
       |  - missing tags  |
       +------+-----------+
              |
       +------v-----------+
       |  report.json     |
       |  report.md       |
       |  PR Comment      |
       +------------------+
```

---

## Decisions & Deviations

- **SSH CIDR changed from 0.0.0.0/0 to 10.0.0.0/8** — opening SSH to the entire internet is a serious security risk; the variable can be overridden but the safe default is internal-only CIDR.
- **Orphaned EBS volume intentionally created without `Protected=true` tag** — so the Janitor will find and flag it, demonstrating the detection works end-to-end.
- **EC2 instances have `safe_to_auto_delete: false`** — terminating compute is destructive and irreversible; we always require a human in the loop for instance termination even when --delete is used.
- **AMI value is a dummy string** — LocalStack does not validate AMI IDs, so `ami-12345678` is fine for local testing.
- **EIP age_days reported as 0** — the EC2 API does not expose an EIP allocation timestamp in the standard `describe_addresses` response; we set 0 as an honest placeholder rather than guessing.
- **Deduplication on (resource_id, reason) pair** — a volume can appear in both the "unattached" check and the "missing tags" check; we keep both findings since the reasons are different and both are actionable.

---

## Trade-offs

With one more week I would:
- Add multi-account support using `sts:AssumeRole` across an AWS Organization
- Add snapshot orphan detection (snapshots not linked to any AMI or active volume)
- Store scan history in DynamoDB/SQLite so the FinOps team can see trends over time
- Add Slack/email notifications for the daily standalone job
- Write proper unit tests using `moto` to mock the AWS APIs
- Add RDS and ElastiCache idle resource detection

---

## AI Usage Disclosure

I used Perplexity AI (Claude Sonnet) as a coding assistant for this assignment.

- **Where I used it:** Initial scaffolding of the Terraform module structure, the GitHub Actions YAML boilerplate, and the report schema layout.
- **What it got wrong:** The initial GitHub Actions health check for LocalStack used `grep 'initialized'` which doesn't match LocalStack's actual health response format (`running`). I caught this by reading the LocalStack docs and fixed it to `grep running`.
- **What I wrote manually:** The deduplication logic in `main()` and the `is_protected()` / `missing_required_tags()` helper functions — I wanted full control over the safety logic since that's the most critical part of the script.
