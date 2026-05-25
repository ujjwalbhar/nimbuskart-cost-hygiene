#!/usr/bin/env python3
"""
Cost Janitor - Scans AWS resources for wasteful/orphaned resources.
Runs against LocalStack by default. Use --endpoint-url to point elsewhere.

Usage:
  python janitor.py [--dry-run] [--delete] [--days N] [--endpoint-url URL] [--region REGION]

Examples:
  python janitor.py                          # dry-run, default settings
  python janitor.py --dry-run                # explicit dry-run
  python janitor.py --delete                 # delete orphans (skips Protected=true)
  python janitor.py --days 7                 # flag stopped EC2s idle > 7 days
  python janitor.py --endpoint-url http://localhost:4566  # LocalStack
"""

import argparse
import json
import sys
import os
from datetime import datetime, timezone

import boto3
from botocore.config import Config

from constants import (
    EBS_GP3_COST_PER_GB_MONTH,
    DEFAULT_EBS_SIZE_GB,
    STOPPED_EC2_COST_PER_MONTH,
    ELASTIC_IP_IDLE_COST_PER_MONTH,
    MISSING_TAG_COST_PER_MONTH,
    EBS_SNAPSHOT_COST_PER_GB_MONTH,
    STALE_SNAPSHOT_DAYS,
    DEFAULT_SNAPSHOT_SIZE_GB,
    REQUIRED_TAGS,
)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Cost Janitor - find and optionally remove orphaned AWS resources"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Report orphans but do NOT delete anything (this is the safe default).",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        default=False,
        help="Delete orphaned resources. Skips anything tagged Protected=true.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Days a stopped EC2 must be idle before being flagged (default: 14).",
    )
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566"),
        help="AWS/LocalStack endpoint URL (default: http://localhost:4566).",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        help="AWS region (default: us-east-1).",
    )
    parser.add_argument(
        "--output",
        default="report.json",
        help="Path to write the JSON report (default: report.json).",
    )
    parser.add_argument(
        "--output-md",
        default="report.md",
        help="Path to write the Markdown summary (default: report.md).",
    )

    args = parser.parse_args()

    # If neither flag given, default to dry-run (safe behaviour)
    if not args.delete and not args.dry_run:
        args.dry_run = True

    # --delete and --dry-run are mutually exclusive
    if args.delete and args.dry_run:
        parser.error("--delete and --dry-run are mutually exclusive. Pick one.")

    return args


# ---------------------------------------------------------------------------
# AWS helpers
# ---------------------------------------------------------------------------

def make_client(service, endpoint_url, region):
    return boto3.client(
        service,
        endpoint_url=endpoint_url,
        region_name=region,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
        config=Config(retries={"max_attempts": 3}),
    )


def get_account_id(endpoint_url, region):
    try:
        sts = make_client("sts", endpoint_url, region)
        return sts.get_caller_identity()["Account"]
    except Exception:
        return "000000000000"


def days_since(dt):
    """Return number of whole days between a datetime and now (UTC)."""
    if dt is None:
        return 0
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (now - dt).days)


def parse_stop_time(state_transition_reason):
    """
    Extract the stop timestamp from EC2 StateTransitionReason.
    Example value: "User initiated (2026-01-10 08:00:00 GMT)"
    Returns a datetime or None.
    """
    import re
    if not state_transition_reason:
        return None
    match = re.search(r"\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} GMT)\)", state_transition_reason)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S GMT").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def is_protected(tags):
    """Return True if the resource has Protected=true tag."""
    if not tags:
        return False
    for tag in tags:
        if tag.get("Key") == "Protected" and tag.get("Value", "").lower() == "true":
            return True
    return False


def missing_required_tags(tags):
    """Return list of required tag keys that are absent from this resource."""
    if not tags:
        return REQUIRED_TAGS[:]
    tag_keys = {t["Key"] for t in tags}
    return [t for t in REQUIRED_TAGS if t not in tag_keys]


def tags_to_dict(tags):
    """Convert boto3 tag list to a plain dict."""
    if not tags:
        return {}
    return {t["Key"]: t["Value"] for t in tags}


# ---------------------------------------------------------------------------
# Orphan detectors
# ---------------------------------------------------------------------------

def find_orphan_volumes(ec2, delete):
    """EBS volumes in 'available' state (not attached to any instance)."""
    findings = []
    response = ec2.describe_volumes(Filters=[{"Name": "status", "Values": ["available"]}])

    for vol in response.get("Volumes", []):
        vol_id = vol["VolumeId"]
        tags = vol.get("Tags", [])
        size_gb = vol.get("Size", DEFAULT_EBS_SIZE_GB)
        age = days_since(vol.get("CreateTime"))
        cost = round(size_gb * EBS_GP3_COST_PER_GB_MONTH, 2)
        tag_dict = tags_to_dict(tags)
        protected = is_protected(tags)

        findings.append({
            "resource_id": vol_id,
            "resource_type": "ebs_volume",
            "reason": "unattached",
            "age_days": age,
            "estimated_monthly_cost_usd": cost,
            "tags": {
                "Project": tag_dict.get("Project"),
                "Environment": tag_dict.get("Environment"),
                "Owner": tag_dict.get("Owner"),
            },
            "suggested_action": "delete",
            "safe_to_auto_delete": not protected,
        })

        if delete:
            if protected:
                print(f"[SKIP]   Volume {vol_id} tagged Protected=true, skipping.")
            else:
                print(f"[DELETE] Deleting EBS volume {vol_id}")
                ec2.delete_volume(VolumeId=vol_id)

    return findings


def find_stopped_instances(ec2, delete, stopped_days_threshold):
    """
    EC2 instances in 'stopped' state for more than N days.

    Uses StateTransitionReason to extract the actual stop time when available
    (e.g. 'User initiated (2026-01-10 08:00:00 GMT)'). Falls back to LaunchTime
    as a conservative proxy if the stop time cannot be parsed -- this is a known
    limitation in LocalStack which does not populate StateTransitionReason.
    """
    findings = []
    response = ec2.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}]
    )

    for reservation in response.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            inst_id = inst["InstanceId"]
            tags = inst.get("Tags", [])
            tag_dict = tags_to_dict(tags)
            protected = is_protected(tags)

            # Prefer actual stop time; fall back to launch time
            stop_time = parse_stop_time(inst.get("StateTransitionReason", ""))
            reference_time = stop_time or inst.get("LaunchTime")
            age = days_since(reference_time)

            if age < stopped_days_threshold:
                continue

            findings.append({
                "resource_id": inst_id,
                "resource_type": "ec2_instance",
                "reason": f"stopped_over_{stopped_days_threshold}_days",
                "age_days": age,
                "estimated_monthly_cost_usd": STOPPED_EC2_COST_PER_MONTH,
                "tags": {
                    "Project": tag_dict.get("Project"),
                    "Environment": tag_dict.get("Environment"),
                    "Owner": tag_dict.get("Owner"),
                },
                "suggested_action": "review_and_terminate",
                "safe_to_auto_delete": False,
            })

            if delete:
                if protected:
                    print(f"[SKIP]   Instance {inst_id} tagged Protected=true, skipping.")
                else:
                    print(f"[DELETE] Terminating EC2 instance {inst_id}")
                    ec2.terminate_instances(InstanceIds=[inst_id])

    return findings


def find_unused_eips(ec2, delete):
    """Elastic IPs not associated with any instance or network interface."""
    findings = []
    response = ec2.describe_addresses()

    for addr in response.get("Addresses", []):
        if addr.get("AssociationId"):
            continue  # Still associated -- skip

        alloc_id = addr.get("AllocationId", addr.get("PublicIp", "unknown"))
        public_ip = addr.get("PublicIp", "")
        tags = addr.get("Tags", [])
        tag_dict = tags_to_dict(tags)
        protected = is_protected(tags)

        findings.append({
            "resource_id": alloc_id,
            "resource_type": "elastic_ip",
            "reason": "unassociated",
            "age_days": 0,
            "estimated_monthly_cost_usd": ELASTIC_IP_IDLE_COST_PER_MONTH,
            "tags": {
                "Project": tag_dict.get("Project"),
                "Environment": tag_dict.get("Environment"),
                "Owner": tag_dict.get("Owner"),
            },
            "suggested_action": "release",
            "safe_to_auto_delete": not protected,
        })

        if delete:
            if protected:
                print(f"[SKIP]   EIP {alloc_id} tagged Protected=true, skipping.")
            else:
                print(f"[DELETE] Releasing Elastic IP {public_ip} ({alloc_id})")
                ec2.release_address(AllocationId=alloc_id)

    return findings


def find_stale_snapshots(ec2, delete):
    """
    EBS snapshots older than STALE_SNAPSHOT_DAYS whose source volume no longer
    exists. These are safe cleanup candidates as they serve no restore purpose.
    """
    findings = []

    vol_response = ec2.describe_volumes()
    existing_volumes = {v["VolumeId"] for v in vol_response.get("Volumes", [])}

    snap_response = ec2.describe_snapshots(OwnerIds=["self"])

    for snap in snap_response.get("Snapshots", []):
        snap_id = snap["SnapshotId"]
        tags = snap.get("Tags", [])
        tag_dict = tags_to_dict(tags)
        protected = is_protected(tags)
        age = days_since(snap.get("StartTime"))
        size_gb = snap.get("VolumeSize", DEFAULT_SNAPSHOT_SIZE_GB)
        source_vol = snap.get("VolumeId", "")
        cost = round(size_gb * EBS_SNAPSHOT_COST_PER_GB_MONTH, 2)

        if age < STALE_SNAPSHOT_DAYS:
            continue
        if source_vol and source_vol in existing_volumes:
            continue

        findings.append({
            "resource_id": snap_id,
            "resource_type": "ebs_snapshot",
            "reason": "stale_orphaned_snapshot",
            "age_days": age,
            "estimated_monthly_cost_usd": cost,
            "tags": {
                "Project": tag_dict.get("Project"),
                "Environment": tag_dict.get("Environment"),
                "Owner": tag_dict.get("Owner"),
            },
            "suggested_action": "delete",
            "safe_to_auto_delete": not protected,
        })

        if delete:
            if protected:
                print(f"[SKIP]   Snapshot {snap_id} tagged Protected=true, skipping.")
            else:
                print(f"[DELETE] Deleting snapshot {snap_id}")
                ec2.delete_snapshot(SnapshotId=snap_id)

    return findings


def find_missing_tags(ec2):
    """Resources (EC2, EBS, snapshots) missing one or more required tags."""
    findings = []

    # EC2 instances
    response = ec2.describe_instances()
    for reservation in response.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            if inst["State"]["Name"] in ["terminated", "shutting-down"]:
                continue
            tags = inst.get("Tags", [])
            missing = missing_required_tags(tags)
            if not missing:
                continue
            tag_dict = tags_to_dict(tags)
            findings.append({
                "resource_id": inst["InstanceId"],
                "resource_type": "ec2_instance",
                "reason": f"missing_tags:{','.join(missing)}",
                "age_days": days_since(inst.get("LaunchTime")),
                "estimated_monthly_cost_usd": MISSING_TAG_COST_PER_MONTH,
                "tags": {
                    "Project": tag_dict.get("Project"),
                    "Environment": tag_dict.get("Environment"),
                    "Owner": tag_dict.get("Owner"),
                },
                "suggested_action": "add_missing_tags",
                "safe_to_auto_delete": False,
            })

    # EBS volumes
    vol_response = ec2.describe_volumes()
    for vol in vol_response.get("Volumes", []):
        tags = vol.get("Tags", [])
        missing = missing_required_tags(tags)
        if not missing:
            continue
        tag_dict = tags_to_dict(tags)
        findings.append({
            "resource_id": vol["VolumeId"],
            "resource_type": "ebs_volume",
            "reason": f"missing_tags:{','.join(missing)}",
            "age_days": days_since(vol.get("CreateTime")),
            "estimated_monthly_cost_usd": MISSING_TAG_COST_PER_MONTH,
            "tags": {
                "Project": tag_dict.get("Project"),
                "Environment": tag_dict.get("Environment"),
                "Owner": tag_dict.get("Owner"),
            },
            "suggested_action": "add_missing_tags",
            "safe_to_auto_delete": False,
        })

    # Snapshots
    snap_response = ec2.describe_snapshots(OwnerIds=["self"])
    for snap in snap_response.get("Snapshots", []):
        tags = snap.get("Tags", [])
        missing = missing_required_tags(tags)
        if not missing:
            continue
        tag_dict = tags_to_dict(tags)
        findings.append({
            "resource_id": snap["SnapshotId"],
            "resource_type": "ebs_snapshot",
            "reason": f"missing_tags:{','.join(missing)}",
            "age_days": days_since(snap.get("StartTime")),
            "estimated_monthly_cost_usd": MISSING_TAG_COST_PER_MONTH,
            "tags": {
                "Project": tag_dict.get("Project"),
                "Environment": tag_dict.get("Environment"),
                "Owner": tag_dict.get("Owner"),
            },
            "suggested_action": "add_missing_tags",
            "safe_to_auto_delete": False,
        })

    return findings


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------

def build_report(findings, account_id, region):
    total_waste = sum(f["estimated_monthly_cost_usd"] for f in findings)
    return {
        "scan_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "account_id": account_id,
        "region": region,
        "summary": {
            "total_orphans": len(findings),
            "estimated_monthly_waste_usd": round(total_waste, 2),
        },
        "findings": findings,
    }


def write_markdown(report, output_path):
    lines = [
        "# Cost Janitor Report",
        "",
        f"**Scan time:** {report['scan_timestamp']}",
        f"**Account:** {report['account_id']}",
        f"**Region:** {report['region']}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total orphans found | {report['summary']['total_orphans']} |",
        f"| Estimated monthly waste | ${report['summary']['estimated_monthly_waste_usd']:.2f} |",
        "",
    ]

    if report["findings"]:
        lines += [
            "## Findings",
            "",
            "| Resource ID | Type | Reason | Age (days) | Est. Cost/mo | Safe to Auto-Delete |",
            "|-------------|------|--------|------------|--------------|---------------------|",
        ]
        for f in report["findings"]:
            safe = "Yes" if f["safe_to_auto_delete"] else "No"
            lines.append(
                f"| {f['resource_id']} | {f['resource_type']} | {f['reason']} "
                f"| {f['age_days']} | ${f['estimated_monthly_cost_usd']:.2f} | {safe} |"
            )
    else:
        lines.append("> No orphaned resources found. Your cloud is clean!")

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    mode = "DELETE" if args.delete else "DRY-RUN"

    print(f"[INFO] Cost Janitor starting")
    print(f"[INFO] Mode            : {mode}")
    print(f"[INFO] Endpoint        : {args.endpoint_url}")
    print(f"[INFO] Region          : {args.region}")
    print(f"[INFO] Stopped EC2 days: {args.days}")
    print(f"[INFO] Output JSON     : {args.output}")
    print(f"[INFO] Output Markdown : {args.output_md}")
    print()

    ec2 = make_client("ec2", args.endpoint_url, args.region)
    account_id = get_account_id(args.endpoint_url, args.region)

    all_findings = []

    print("[SCAN] Unattached EBS volumes...")
    all_findings.extend(find_orphan_volumes(ec2, args.delete))

    print("[SCAN] Stopped EC2 instances...")
    all_findings.extend(find_stopped_instances(ec2, args.delete, args.days))

    print("[SCAN] Unused Elastic IPs...")
    all_findings.extend(find_unused_eips(ec2, args.delete))

    print("[SCAN] Stale orphaned snapshots...")
    all_findings.extend(find_stale_snapshots(ec2, args.delete))

    print("[SCAN] Resources with missing required tags...")
    all_findings.extend(find_missing_tags(ec2))

    # Deduplicate: a resource may appear in both orphan and missing-tag scans.
    seen = set()
    unique_findings = []
    for f in all_findings:
        key = (f["resource_id"], f["reason"])
        if key not in seen:
            seen.add(key)
            unique_findings.append(f)

    report = build_report(unique_findings, account_id, args.region)

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\n[INFO] JSON report  -> {args.output}")

    write_markdown(report, args.output_md)
    print(f"[INFO] MD summary   -> {args.output_md}")

    n = report["summary"]["total_orphans"]
    waste = report["summary"]["estimated_monthly_waste_usd"]
    print(f"\n[RESULT] {n} orphan(s) found | Estimated waste: ${waste:.2f}/mo")

    if args.dry_run and n > 0:
        print("[WARN] Exiting with code 1 (orphans found in dry-run mode).")
        sys.exit(1)


if __name__ == "__main__":
    main()
