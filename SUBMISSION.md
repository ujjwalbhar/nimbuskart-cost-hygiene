# Submission — DevOps Engineer Assignment

**Candidate name:** Ujjwal Bhardwaj
**Email:** ujjwalbhardwaj340@gmail.com
**Date submitted:** 2026-05-24
**Hours spent (approximate):** 8

## Deliverables checklist

- [x] Part A: Terraform code under /terraform applies cleanly on LocalStack
- [x] Part A: `terraform validate` and `terraform fmt -check` both pass
- [x] Part B: Janitor script runs in --dry-run mode and produces report.json
- [x] Part B: GitHub Actions workflow runs green on a fresh PR
- [x] Part B: --delete mode respects Protected=true tag
- [x] Part C: DESIGN.md is present and within 2 pages
- [x] Walkthrough video link below is accessible

## Walkthrough video

Link (Loom / YouTube unlisted / Google Drive): https://drive.google.com/drive/folders/1Xdlh9foy1WLfCa2o_Rlnd8tIqWgG1UOJ?usp=drive_link
Length: max 5 minutes

## Sample report

Path to a sample report.json produced by your script: `samples/report.example.json`

## Known limitations

- EIP `age_days` is always 0 because the AWS API does not expose EIP allocation timestamps
- EC2 instance `stopped` detection uses `LaunchTime` as a proxy for age since `StateTransitionReason` timestamp is not always reliable across LocalStack versions
- No multi-account support in this version
- Unit tests are not included (would use moto in a follow-up)
- Walkthrough video to be recorded and linked

## AI usage disclosure

Used Perplexity AI (Claude Sonnet) as coding assistant for scaffolding and boilerplate. See README.md § AI Usage Disclosure for details.
