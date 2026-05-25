terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  s3_use_path_style           = true
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    ec2 = "http://localhost:4566"
    s3  = "http://localhost:4566"
    iam = "http://localhost:4566"
    sts = "http://localhost:4566"
  }
}

module "network" {
  source           = "./modules/network"
  vpc_cidr         = "10.20.0.0/16"
  project          = var.project
  environment      = var.environment
  owner            = var.owner
  allowed_ssh_cidr = var.allowed_ssh_cidr
}

locals {
  common_tags = {
    Project     = var.project
    Environment = var.environment
    Owner       = var.owner
    ManagedBy   = "terraform"
  }
}

# Two web-tier EC2 instances
resource "aws_instance" "web" {
  count         = 2
  ami           = "ami-12345678" # Dummy AMI - LocalStack does not validate this
  instance_type = "t3.micro"

  subnet_id              = element(module.network.public_subnet_ids, count.index)
  vpc_security_group_ids = [module.network.web_security_group_id]

  tags = merge(local.common_tags, {
    Name = "${var.project}-${var.environment}-web-${count.index + 1}"
    Tier = "web"
  })
}

# Intentionally unattached EBS volume - used in Part B as a known orphan
resource "aws_ebs_volume" "orphan" {
  availability_zone = "us-east-1a"
  size              = 10

  tags = merge(local.common_tags, {
    Name = "${var.project}-${var.environment}-orphan-vol"
  })
}

# S3 log bucket
resource "aws_s3_bucket" "logs" {
  bucket = var.log_bucket_name

  tags = merge(local.common_tags, {
    Name = "${var.project}-${var.environment}-logs"
  })
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    id     = "expire-noncurrent-30-days"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}
