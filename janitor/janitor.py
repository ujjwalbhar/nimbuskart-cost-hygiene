#!/usr/bin/env python3
"""
Cost Janitor - Scans AWS resources for wasteful/orphaned resources.
Runs against LocalStack by default. Use --endpoint-url to point elsewhere.

Usage:
  python janitor.py [--dry-run] [--delete] [--days N] [--endpoint-url URL] [--region REGION]
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
    REQUIRED_TAGS,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Cost Janitor - find orphaned AWS resources")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Report orphans but do not delete (default)",
    )
    mode.add_argument(
        "--delete",
        action="store_true",
        default=False,
        help="Delete orphaned resources (skips anything tagged Protected=true)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Number of days a stopped EC2 instance must be idle before flagging (default: 14)",
    )
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566"),
        help="AWS endpoint URL (default: http://localhost:4566 or AWS_ENDPOINT_URL env var)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        help="AWS region (default: us-east-1)",
    )
    parser.add_argument(
        "--output",
        default="report.json",
        help="Path to write the JSON report (default: report.json)",
    )
    parser.add_argument(
        "--output-md",
        default="report.md",
        help="Path to write the Markdown summary (default: report.md)",
    )
    args = parser.parse_args()
    # --delete overrides the default --dry-run=True
    if args.delete:
        args.dry_run = False
    return args


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
    """Return number of days between a datetime and now."""
    if dt is None:
        return 0
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).days


def is_protected(tags):
    """Return True if the resource has Protected=true tag."""
    if not tags:
        return False
    for tag in tags:
        if tag.get("Key") == "Protected" and tag.get("Value", "").lower() == "true":
            return True
    return False


def missing_required_tags(tags):
    """Return list of required tag keys that are missing."""
    if not tags:
        return REQUIRED_TAGS[:]
    tag_keys = {t["Key"] for t in tags}
    return [t for t in REQUIRED_TAGS if t not in tag_keys]


def find_orphan_volumes(ec2, dry_run, delete):
    """
    Find EBS volumes in 'available' state (not attached to any instance).
    """
    findings = []
    response = ec2.describe_volumes(Filters=[{"Name": "status", "Values": ["available"]}])

    for vol in response.get("Volumes", []):
        vol_id = vol["VolumeId"]
        tags = vol.get("Tags", [])
        size_gb = vol.get("Size", DEFAULT_EBS_SIZE_GB)
        create_time = vol.get("CreateTime")
        age = days_since(create_time)
        cost = round(size_gb * EBS_GP3_COST_PER_GB_MONTH, 2)

        tag_dict = {t["Key"]: t["Value"] for t in tags} if tags else {}

        finding = {
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
            "safe_to_auto_delete": not is_protected(tags),
        }
        findings.append(finding)

        if delete and not is_protected(tags):
            print(f"[DELETE] Deleting EBS volume {vol_id}")
            ec2.delete_volume(VolumeId=vol_id)
        elif delete and is_protected(tags):
            print(f"[SKIP] Volume {vol_id} has Protected=true, skipping.")

    return findings


def find_stopped_instances(ec2, dry_run, delete, stopped_days_threshold):
    """
    Find EC2 instances in 'stopped' state for more than N days.
    """
    findings = []
    response = ec2.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}]
    )

    for reservation in response.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            inst_id = inst["InstanceId"]
            tags = inst.get("Tags", [])
            launch_time = inst.get("LaunchTime")
            age = days_since(launch_time)

            if age < stopped_days_threshold:
                continue

            tag_dict = {t["Key"]: t["Value"] for t in tags} if tags else {}

            finding = {
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
                "safe_to_auto_delete": False,  # EC2 termination is risky; always needs human review
            }
            findings.append(finding)

            if delete and not is_protected(tags):
                print(f"[DELETE] Terminating EC2 instance {inst_id}")
                ec2.terminate_instances(InstanceIds=[inst_id])
            elif delete and is_protected(tags):
                print(f"[SKIP] Instance {inst_id} has Protected=true, skipping.")

    return findings


def find_unused_eips(ec2, dry_run, delete):
    """
    Find Elastic IPs not associated with any instance or network interface.
    """
    findings = []
    response = ec2.describe_addresses()

    for addr in response.get("Addresses", []):
        # If AssociationId is missing, the EIP is not associated with anything
        if addr.get("AssociationId"):
            continue

        alloc_id = addr.get("AllocationId", addr.get("PublicIp", "unknown"))
        public_ip = addr.get("PublicIp", "")
        tags = addr.get("Tags", [])
        tag_dict = {t["Key"]: t["Value"] for t in tags} if tags else {}

        finding = {
            "resource_id": alloc_id,
            "resource_type": "elastic_ip",
            "reason": "unassociated",
            "age_days": 0,  # EIPs don't expose creation time via standard API
            "estimated_monthly_cost_usd": ELASTIC_IP_IDLE_COST_PER_MONTH,
            "tags": {
                "Project": tag_dict.get("Project"),
                "Environment": tag_dict.get("Environment"),
                "Owner": tag_dict.get("Owner"),
            },
            "suggested_action": "release",
            "safe_to_auto_delete": not is_protected(tags),
        }
        findings.append(finding)

        if delete and not is_protected(tags):
            print(f"[DELETE] Releasing Elastic IP {public_ip} ({alloc_id})")
            ec2.release_address(AllocationId=alloc_id)
        elif delete and is_protected(tags):
            print(f"[SKIP] EIP {alloc_id} has Protected=true, skipping.")

    return findings


def find_missing_tags(ec2, dry_run):
    """
    Find EC2 instances, EBS volumes, and security groups missing required tags.
    """
    findings = []

    # Check EC2 instances
    response = ec2.describe_instances()
    for reservation in response.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            if inst["State"]["Name"] in ["terminated", "shutting-down"]:
                continue
            tags = inst.get("Tags", [])
            missing = missing_required_tags(tags)
            if missing:
                tag_dict = {t["Key"]: t["Value"] for t in tags} if tags else {}
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

    # Check EBS volumes
    vol_response = ec2.describe_volumes()
    for vol in vol_response.get("Volumes", []):
        tags = vol.get("Tags", [])
        missing = missing_required_tags(tags)
        if missing:
            tag_dict = {t["Key"]: t["Value"] for t in tags} if tags else {}
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

    return findings


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
    lines = []
    lines.append("# Cost Janitor Report")
    lines.append(f"")
    lines.append(f"**Scan time:** {report['scan_timestamp']}")
    lines.append(f"**Account:** {report['account_id']}")
    lines.append(f"**Region:** {report['region']}")
    lines.append(f"")
    lines.append("## Summary")
    lines.append(f"")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total orphans found | {report['summary']['total_orphans']} |")
    lines.append(f"| Estimated monthly waste | ${report['summary']['estimated_monthly_waste_usd']:.2f} |")
    lines.append(f"")

    if report["findings"]:
        lines.append("## Findings")
        lines.append(f"")
        lines.append("| Resource ID | Type | Reason | Age (days) | Est. Cost/mo |")
        lines.append("|-------------|------|--------|------------|--------------|")
        for f in report["findings"]:
            lines.append(
                f"| {f['resource_id']} | {f['resource_type']} | {f['reason']} "
                f"| {f['age_days']} | ${f['estimated_monthly_cost_usd']:.2f} |"
            )
    else:
        lines.append("> No orphaned resources found. Your cloud is clean! 🎉")

    with open(output_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    args = parse_args()

    print(f"[INFO] Running Cost Janitor")
    print(f"[INFO] Mode: {'DELETE' if args.delete else 'DRY-RUN'}")
    print(f"[INFO] Endpoint: {args.endpoint_url}")
    print(f"[INFO] Region: {args.region}")
    print(f"[INFO] Stopped EC2 threshold: {args.days} days")

    ec2 = make_client("ec2", args.endpoint_url, args.region)
    account_id = get_account_id(args.endpoint_url, args.region)

    all_findings = []

    print("[INFO] Scanning for unattached EBS volumes...")
    all_findings.extend(find_orphan_volumes(ec2, args.dry_run, args.delete))

    print("[INFO] Scanning for stopped EC2 instances...")
    all_findings.extend(find_stopped_instances(ec2, args.dry_run, args.delete, args.days))

    print("[INFO] Scanning for unused Elastic IPs...")
    all_findings.extend(find_unused_eips(ec2, args.dry_run, args.delete))

    print("[INFO] Scanning for missing required tags...")
    all_findings.extend(find_missing_tags(ec2, args.dry_run))

    # Deduplicate: a resource might appear in both orphan and missing-tag scans
    seen = set()
    unique_findings = []
    for f in all_findings:
        key = (f["resource_id"], f["reason"])
        if key not in seen:
            seen.add(key)
            unique_findings.append(f)

    report = build_report(unique_findings, account_id, args.region)

    with open(args.output, "w") as fh:
        json.dump(report, fh, indent=2)
    print(f"[INFO] Report written to {args.output}")

    write_markdown(report, args.output_md)
    print(f"[INFO] Markdown summary written to {args.output_md}")

    print(f"[INFO] Total orphans found: {report['summary']['total_orphans']}")
    print(f"[INFO] Estimated monthly waste: ${report['summary']['estimated_monthly_waste_usd']:.2f}")

    # Exit non-zero in dry-run mode if orphans found (so CI can fail)
    if args.dry_run and report["summary"]["total_orphans"] > 0:
        print("[WARN] Orphans found in dry-run mode. Exiting with code 1.")
        sys.exit(1)


if __name__ == "__main__":
    main()
