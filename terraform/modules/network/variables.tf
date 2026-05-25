variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.20.0.0/16"
}

variable "project" {
  description = "Project name used in tags and resource names"
  type        = string
}

variable "environment" {
  description = "Deployment environment (e.g. staging, production)"
  type        = string
}

variable "owner" {
  description = "Team or individual responsible for these resources"
  type        = string
}

variable "allowed_ssh_cidr" {
  description = "CIDR block allowed inbound SSH on port 22. WARNING: default 0.0.0.0/0 is intentionally unsafe — restrict in production."
  type        = string
  default     = "0.0.0.0/0"
}
