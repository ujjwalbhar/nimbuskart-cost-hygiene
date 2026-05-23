# DESIGN.md — Production Hardening & Multi-Cloud Scale

## Multi-Cloud Reality

To add GCP (and later Azure) without rewriting the core, the Janitor needs a clear boundary between **cloud-specific** code and **shared** logic.

The structure I'd use:

```
janitor/
  core/
    models.py        # Finding dataclass, report schema, shared constants
    report.py        # build_report(), write_markdown() — cloud-agnostic
  providers/
    aws/
      scanner.py     # find_orphan_volumes(), find_stopped_instances(), etc.
      client.py      # boto3 session factory
    gcp/
      scanner.py     # google-cloud-compute equivalents
      client.py      # google.cloud auth
    azure/
      scanner.py     # azure-mgmt-compute equivalents
      client.py      # azure identity
  janitor.py         # CLI entrypoint: loads providers based on --cloud flag
```

The CLI would accept `--cloud aws` (default), `--cloud gcp`, or `--cloud all`. Each provider's `scanner.py` implements the same interface: a `scan()` function that returns a list of `Finding` objects. The core logic (deduplication, cost estimation, report writing) never changes.

---

## Permissions

**Dry-run mode** only needs read permissions. Here is the minimal IAM policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CostJanitorReadOnly",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeVolumes",
        "ec2:DescribeInstances",
        "ec2:DescribeAddresses",
        "ec2:DescribeSnapshots",
        "ec2:DescribeTags",
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    }
  ]
}
```

**Delete mode** adds:
- `ec2:DeleteVolume`
- `ec2:TerminateInstances`
- `ec2:ReleaseAddress`

Critically, delete-mode should run under a **separate IAM role** that requires MFA or is only assumable from a specific CI/CD pipeline ARN — not the same role used for dry-run.

---

## Safety Net

**Failure mode 1 — Deleting an EBS volume still used by an AMI**

A volume in `available` state can still be referenced by a registered AMI snapshot chain. Deleting it silently corrupts the AMI and any future launch from it will fail. The guardrail: before deleting a volume, call `ec2.describe_snapshots(Filters=[{"Name": "volume-id", "Values": [vol_id]}])` and skip the volume if any snapshots reference it.

**Failure mode 2 — Terminating a stopped EC2 that is scheduled to restart**

A dev might stop an instance on Friday evening planning to restart it Monday. The Janitor would flag it after 14 days and auto-terminate it over the weekend if delete mode is on. The guardrail: never auto-terminate EC2 instances — always set `safe_to_auto_delete: false` for them and require a human approval step (e.g., a GitHub Actions manual approval gate or a Slack approval bot) before termination.

---

## Observability

| Metric | Source | Alert threshold |
|--------|--------|-----------------|
| `janitor.orphans_found` | Janitor script → CloudWatch custom metric | Alert if > 10 orphans in a single scan |
| `janitor.estimated_waste_usd` | Janitor script → CloudWatch custom metric | Alert if > $200/month estimated waste |
| `janitor.scan_duration_seconds` | Janitor script → CloudWatch custom metric | Alert if > 300s (scan is hanging or API throttled) |
| `janitor.scan_errors` | Janitor script → CloudWatch Logs | Alert on any ERROR log line |
| `janitor.deletes_performed` | Janitor script → CloudWatch custom metric | Alert if > 0 in dry-run mode (should never happen) |

All metrics published via `boto3` `cloudwatch.put_metric_data()` at the end of each scan run. The FinOps dashboard in CloudWatch would show a time-series of `estimated_waste_usd` so the team can see whether cleanups are actually reducing the bill over time.

---

## What I Did Not Build

I left out multi-account support (scanning across AWS Organizations using IAM role assumption), snapshot orphan detection (old snapshots not linked to any volume or AMI), RDS idle instance detection, and a proper database/history store for tracking whether a resource has been flagged before. I also did not build a Slack or email notification channel for the report — the PR comment covers CI, but a standalone daily job would need a notification step. These are all real next steps, but they would have taken the scope beyond what fits in a 6-10 hour assignment. The architecture in the Multi-Cloud section above is designed so that adding these does not require rethinking what's already here.
