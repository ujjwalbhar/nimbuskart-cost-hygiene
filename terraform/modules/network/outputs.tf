output "vpc_id" {
  description = "ID of the created VPC"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "List of public subnet IDs (one per AZ)"
  value       = [aws_subnet.public_a.id, aws_subnet.public_b.id]
}

output "web_security_group_id" {
  description = "ID of the web-tier security group"
  value       = aws_security_group.web_sg.id
}
