# Lake: S3 warehouse + Glue Data Catalog (Iceberg) + Athena workgroup.
# Replaces the local Parquet/Iceberg store (ADR-005).

variable "name" {
  type = string
}

variable "bucket_name" {
  type    = string
  default = null
}

variable "kms_key_arn" {
  type = string
}

locals {
  warehouse_prefix = "warehouse"
  results_prefix   = "athena-results"
}

resource "aws_s3_bucket" "lake" {
  bucket        = var.bucket_name
  bucket_prefix = var.bucket_name == null ? "${var.name}-lake-" : null
}

resource "aws_s3_bucket_ownership_controls" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Iceberg snapshots are the history; old S3 versions are only a safety net.
  rule {
    id     = "expire-noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  rule {
    id     = "expire-athena-results"
    status = "Enabled"
    filter {
      prefix = "${local.results_prefix}/"
    }
    expiration {
      days = 30
    }
  }

  depends_on = [aws_s3_bucket_versioning.lake]
}

data "aws_iam_policy_document" "tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.lake.arn,
      "${aws_s3_bucket.lake.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "lake" {
  bucket = aws_s3_bucket.lake.id
  policy = data.aws_iam_policy_document.tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.lake]
}

resource "aws_glue_catalog_database" "xstore" {
  name         = "xstore"
  description  = "xstore lake tables (Iceberg)"
  location_uri = "s3://${aws_s3_bucket.lake.bucket}/${local.warehouse_prefix}/xstore.db"
}

# Columns match stores/iceberg/customer.json. Glue writes the Iceberg metadata
# and stamps parameters.table_type = ICEBERG; the day(updated_at) partition
# spec and sort order are applied by the writer (pyiceberg/Athena ALTER), since
# the Glue CreateTable Iceberg input does not take a partition spec.
resource "aws_glue_catalog_table" "customer" {
  name          = "customer"
  database_name = aws_glue_catalog_database.xstore.name
  table_type    = "EXTERNAL_TABLE"

  open_table_format_input {
    iceberg_input {
      metadata_operation = "CREATE"
      version            = "2"
    }
  }

  storage_descriptor {
    location = "s3://${aws_s3_bucket.lake.bucket}/${local.warehouse_prefix}/xstore.db/customer"

    columns {
      name = "customer_id"
      type = "string"
    }
    columns {
      name = "name"
      type = "string"
    }
    columns {
      name = "email"
      type = "string"
    }
    columns {
      name = "phone"
      type = "string"
    }
    columns {
      name = "country"
      type = "string"
    }
    columns {
      name = "updated_at"
      type = "timestamp"
    }
  }

  # Every Iceberg commit moves metadata_location; Terraform owns only the shape.
  lifecycle {
    ignore_changes = [parameters, storage_descriptor[0].parameters]
  }
}

resource "aws_athena_workgroup" "xstore" {
  name          = var.name
  force_destroy = false

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = 10737418240

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }

    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake.bucket}/${local.results_prefix}/"

      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = var.kms_key_arn
      }
    }
  }
}

output "bucket_name" {
  value = aws_s3_bucket.lake.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.lake.arn
}

output "glue_catalog_id" {
  description = "Account id owning the Glue catalog."
  value       = aws_glue_catalog_database.xstore.catalog_id
}

output "glue_database_name" {
  value = aws_glue_catalog_database.xstore.name
}

output "athena_workgroup_name" {
  value = aws_athena_workgroup.xstore.name
}

output "athena_workgroup_arn" {
  value = aws_athena_workgroup.xstore.arn
}
