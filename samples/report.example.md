# Cost Janitor Report

**Scan time:** 2026-05-24T04:00:00Z
**Account:** 000000000000
**Region:** us-east-1

## Summary

| Metric | Value |
|--------|-------|
| Total orphans found | 3 |
| Estimated monthly waste | $12.40 |

## Findings

| Resource ID | Type | Reason | Age (days) | Est. Cost/mo |
|-------------|------|--------|------------|--------------|
| vol-abc12345 | ebs_volume | unattached | 21 | $0.80 |
| i-0abc12345678 | ec2_instance | stopped_over_14_days | 30 | $2.00 |
| eipalloc-0abc123 | elastic_ip | unassociated | 0 | $3.60 |
