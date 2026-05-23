# Pricing constants for cost estimation
# Source: https://aws.amazon.com/ebs/pricing/ (us-east-1, as of Jan 2026)
# Source: https://aws.amazon.com/ec2/pricing/on-demand/ (t3.micro, us-east-1)
# Source: https://aws.amazon.com/ec2/pricing/on-demand/ (Elastic IP idle charge)

# EBS gp3 volume: $0.08 per GB-month
EBS_GP3_COST_PER_GB_MONTH = 0.08

# Default EBS volume size if we can't determine it (GB)
DEFAULT_EBS_SIZE_GB = 20

# EC2 t3.micro stopped instance still incurs EBS cost for root volume
# We estimate a modest $2/month for a stopped t3.micro (root EBS only)
STOPPED_EC2_COST_PER_MONTH = 2.00

# Elastic IP not associated with a running instance: $0.005/hour = ~$3.60/month
ELASTIC_IP_IDLE_COST_PER_MONTH = 3.60

# Resources missing required tags - no direct cost but blocks cost attribution
# We assign a nominal $0 here; the real cost is operational overhead
MISSING_TAG_COST_PER_MONTH = 0.00

# Required tags every resource must carry
REQUIRED_TAGS = ["Project", "Environment", "Owner"]
