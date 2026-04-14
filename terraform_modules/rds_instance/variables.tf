variable "db_identifier" {
  type        = string
  description = "Unique identifier for the RDS instance (lowercase, hyphens allowed)"
}

variable "engine" {
  type        = string
  description = "Database engine: mysql, postgres, or mariadb"

  validation {
    condition     = contains(["mysql", "postgres", "mariadb"], var.engine)
    error_message = "Engine must be one of: mysql, postgres, mariadb."
  }
}

variable "engine_version" {
  type        = string
  description = "Database engine version (e.g. 8.0 for MySQL, 15.3 for PostgreSQL)"
}

variable "instance_class" {
  type        = string
  description = "RDS instance class (e.g. db.t3.micro, db.t3.small)"
  default     = "db.t3.micro"
}

variable "allocated_storage_gb" {
  type        = number
  description = "Allocated storage in GB (minimum 20)"
  default     = 20
}

variable "db_name" {
  type        = string
  description = "Name of the initial database to create"
}

variable "db_username" {
  type        = string
  description = "Master username for the database"
}

variable "db_password" {
  type        = string
  description = "Master password for the database (sensitive — will be stored in state)"
  sensitive   = true
}

variable "subnet_group_name" {
  type        = string
  description = "Name of the DB subnet group for placement"
}

variable "aws_region" {
  type        = string
  description = "AWS region for the RDS instance"
  default     = "us-east-1"
}
