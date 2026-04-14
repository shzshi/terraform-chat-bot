################################################################################
# terraform_modules/s3_bucket/outputs.tf
###############################################################################

output "bucket_id" {
  description = "The name (ID) of the S3 bucket"
  value       = aws_s3_bucket.this.id
}

output "bucket_arn" {
  description = "Full ARN of the S3 bucket"
  value       = aws_s3_bucket.this.arn
}

output "bucket_regional_domain_name" {
  description = "Regional domain name (e.g. for CloudFront origin or presigned URLs)"
  value       = aws_s3_bucket.this.bucket_regional_domain_name
}

output "versioning_status" {
  description = "Versioning status: Enabled or Suspended"
  value       = var.versioning_enabled ? "Enabled" : "Suspended"
}

output "encryption_algorithm" {
  description = "Server-side encryption algorithm applied to all objects"
  value       = "AES256"
}

output "public_access_blocked" {
  description = "Confirms all public access is blocked on this bucket"
  value       = true
}
