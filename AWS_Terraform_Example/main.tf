resource "aws_s3_bucket" "bucket" {
  bucket = var.bucket_name

  tags = {
    Name        = "My S3 Bucket"
    Environment = "Dev"
  }
}