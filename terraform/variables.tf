variable "project" {
  description = "Project name for tagging"
  type        = string
  default     = "nimbuskart"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "staging"
}

variable "owner" {
  description = "Owner team for tagging"
  type        = string
  default     = "devops"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "allowed_ssh_cidr" {
  description = "CIDR block allowed for SSH access. Default is open — restrict this in production."
  type        = string
  default     = "10.0.0.0/8"
}

variable "log_bucket_name" {
  description = "Name of the S3 bucket for application logs"
  type        = string
  default     = "nimbuskart-staging-logs"
}
