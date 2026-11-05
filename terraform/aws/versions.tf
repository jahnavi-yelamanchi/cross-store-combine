terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.6"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Remote state. Disabled so `terraform init -backend=false` / validate work
  # without an AWS account; enable and run `terraform init` for real use.
  # backend "s3" {
  #   bucket         = "xstore-tfstate"
  #   key            = "aws/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "xstore-tf-locks"
  #   encrypt        = true
  # }
}
