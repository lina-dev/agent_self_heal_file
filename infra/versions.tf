terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }

  # Configure a real backend before using in CI (left local here for clarity):
  # backend "s3" {
  #   bucket         = "<tf-state-bucket>"
  #   key            = "audio-repair/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "<tf-lock-table>"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}
