###############################################################################
# terraform_modules/ec2_instance/main.tf
###############################################################################

resource "aws_instance" "this" {
  ami           = var.ami
  instance_type = var.instance_type

  tags = {
    Name      = var.name
    ManagedBy = "terraform-chatbot"
  }
}