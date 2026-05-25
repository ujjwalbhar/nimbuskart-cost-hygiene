# Pricing constants for cost estimation
# Source: https://aws.amazon.com/ebs/pricing/ (us-east-1, as of Jan 2026)
# Source: https://aws.amazon.com/ec2/pricing/on-demand/ (t3.micro, us-east-1)
# Source: https://aws.amazon.com/ec2/pricing/on-demand/ (Elastic IP idle charge)
# Source: https://aws.amazon.com/ebs/pricing/ (EBS snapshots)

# EBS gp3 volume: $0.08 per GB-month
EBS_GP3_COST_PER_GB_MONTH = 0.08

# Default EBS volume size assumption if API doesn't return size (GB)
DEFAULT_EBS_SIZE_GB = 20

# Stopped EC2 t3.micro: root EBS volume (8GB gp3) still billed = ~$0.64/mo
# We use $2.00 as a conservative estimate including attached volumes
STOPPED_EC2_COST_PER_MONTH = 2.00

# Elastic IP not associated with a running instance: $0.005/hour = ~$3.60/month
# Source: https://aws.amazon.com/ec2/pricing/ -> "Elastic IP Addresses"
ELASTIC_IP_IDLE_COST_PER_MONTH = 3.60

# Resources missing required tags: no direct AWS charge, but blocks cost
# attribution. We assign a nominal $1/month to surface these in waste totals
# so FinOps teams can see untagged resources alongside real cost orphans.
MISSING_TAG_COST_PER_MONTH = 1.00

# EBS Snapshot: $0.05 per GB-month
# Source: https://aws.amazon.com/ebs/pricing/ -> "Amazon EBS Snapshots"
EBS_SNAPSHOT_COST_PER_GB_MONTH = 0.05

# Snapshots older than this many days with no associated volume are flagged
STALE_SNAPSHOT_DAYS = 30

# Default snapshot size assumption if API doesn't return volume size (GB)
DEFAULT_SNAPSHOT_SIZE_GB = 20

# Required tags every resource must carry
REQUIRED_TAGS = ["Project", "Environment", "Owner"]
