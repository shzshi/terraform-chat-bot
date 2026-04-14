###############################################################################
# terraform_modules/ec2_instance/variables.tf
###############################################################################

variable "ami" {
  description = "AMI ID for the EC2 instance (e.g. ami-0c02fb55956c7d316)"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string

  validation {
    condition     = contains(["t3.micro", "t3.small", "t3.medium", "t3.large", "m5.large"], var.instance_type)
    error_message = "Allowed values: t3.micro, t3.small, t3.medium, t3.large, m5.large."
  }
}

variable "name" {
  description = "Tag name for the EC2 instance"
  type        = string
}