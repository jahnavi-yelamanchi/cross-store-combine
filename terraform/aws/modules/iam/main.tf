# Least-privilege roles: every statement names concrete ARNs, except
# dynamodb:ListStreams, which has no resource-level permissions.

variable "name" {
  type = string
}

variable "kms_key_arn" {
  type = string
}

variable "stream_arn" {
  type = string
}

variable "dlq_arn" {
  type = string
}

variable "log_group_arn" {
  type = string
}

variable "create_reconciler_role" {
  description = "Known at plan time; true when an OIDC provider is supplied."
  type        = bool
}

variable "oidc_provider_arn" {
  type    = string
  default = null
}

variable "oidc_issuer" {
  description = "OIDC issuer host/path without https://."
  type        = string
  default     = null
}

variable "reconciler_namespace" {
  type = string
}

variable "reconciler_service_account" {
  type = string
}

variable "dynamodb_table_arns" {
  type = list(string)
}

variable "lake_bucket_arn" {
  type = string
}

variable "glue_catalog_id" {
  type = string
}

variable "glue_database_name" {
  type = string
}

variable "athena_workgroup_arn" {
  type = string
}

variable "msk_cluster_arn" {
  type = string
}

variable "rds_secret_arn" {
  type = string
}

data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  glue_prefix = "arn:${data.aws_partition.current.partition}:glue:${data.aws_region.current.name}:${var.glue_catalog_id}"
  msk_topics  = "${replace(var.msk_cluster_arn, ":cluster/", ":topic/")}/*"
  msk_groups  = "${replace(var.msk_cluster_arn, ":cluster/", ":group/")}/*"
}

# ---------------------------------------------------------------- CDC Lambda

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "cdc_lambda" {
  statement {
    sid = "ReadStream"
    actions = [
      "dynamodb:DescribeStream",
      "dynamodb:GetRecords",
      "dynamodb:GetShardIterator",
    ]
    resources = [var.stream_arn]
  }

  statement {
    sid       = "ListStreams"
    actions   = ["dynamodb:ListStreams"]
    resources = ["*"]
  }

  statement {
    sid       = "OnFailureToDlq"
    actions   = ["sqs:SendMessage"]
    resources = [var.dlq_arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${var.log_group_arn}:*"]
  }

  # Stream records and DLQ messages are encrypted with the data key.
  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role" "cdc_lambda" {
  name               = "${var.name}-cdc-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "cdc_lambda" {
  name   = "cdc"
  role   = aws_iam_role.cdc_lambda.id
  policy = data.aws_iam_policy_document.cdc_lambda.json
}

# ---------------------------------------------------- Reconciler (EKS IRSA)

data "aws_iam_policy_document" "reconciler_assume" {
  count = var.create_reconciler_role ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${var.oidc_issuer}:sub"
      values   = ["system:serviceaccount:${var.reconciler_namespace}:${var.reconciler_service_account}"]
    }
    condition {
      test     = "StringEquals"
      variable = "${var.oidc_issuer}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "reconciler" {
  statement {
    sid = "DynamoDBItems"
    actions = [
      "dynamodb:DescribeTable",
      "dynamodb:GetItem",
      "dynamodb:BatchGetItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:ConditionCheckItem",
    ]
    resources = var.dynamodb_table_arns
  }

  statement {
    sid = "DynamoDBStreams"
    actions = [
      "dynamodb:DescribeStream",
      "dynamodb:GetRecords",
      "dynamodb:GetShardIterator",
    ]
    resources = [for arn in var.dynamodb_table_arns : "${arn}/stream/*"]
  }

  statement {
    sid       = "LakeList"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [var.lake_bucket_arn]
  }

  statement {
    sid       = "LakeObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["${var.lake_bucket_arn}/*"]
  }

  statement {
    sid = "GlueCatalog"
    actions = [
      "glue:GetDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:UpdateTable",
      "glue:GetPartitions",
      "glue:BatchGetPartition",
    ]
    resources = [
      "${local.glue_prefix}:catalog",
      "${local.glue_prefix}:database/${var.glue_database_name}",
      "${local.glue_prefix}:table/${var.glue_database_name}/*",
    ]
  }

  statement {
    sid = "Athena"
    actions = [
      "athena:GetWorkGroup",
      "athena:StartQueryExecution",
      "athena:StopQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
    ]
    resources = [var.athena_workgroup_arn]
  }

  statement {
    sid       = "MskCluster"
    actions   = ["kafka-cluster:Connect", "kafka-cluster:DescribeCluster", "kafka:GetBootstrapBrokers"]
    resources = [var.msk_cluster_arn]
  }

  statement {
    sid       = "MskTopics"
    actions   = ["kafka-cluster:DescribeTopic", "kafka-cluster:ReadData", "kafka-cluster:WriteData"]
    resources = [local.msk_topics]
  }

  statement {
    sid       = "MskGroups"
    actions   = ["kafka-cluster:AlterGroup", "kafka-cluster:DescribeGroup"]
    resources = [local.msk_groups]
  }

  statement {
    sid       = "PostgresSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.rds_secret_arn]
  }

  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role" "reconciler" {
  count              = var.create_reconciler_role ? 1 : 0
  name               = "${var.name}-reconciler"
  assume_role_policy = data.aws_iam_policy_document.reconciler_assume[0].json
}

resource "aws_iam_role_policy" "reconciler" {
  count  = var.create_reconciler_role ? 1 : 0
  name   = "reconciler"
  role   = aws_iam_role.reconciler[0].id
  policy = data.aws_iam_policy_document.reconciler.json
}

output "cdc_lambda_role_arn" {
  description = "Returned only after the inline policy exists, so the event source mapping can validate stream access."
  value       = aws_iam_role.cdc_lambda.arn
  depends_on  = [aws_iam_role_policy.cdc_lambda]
}

output "reconciler_role_arn" {
  value = var.create_reconciler_role ? aws_iam_role.reconciler[0].arn : null
}

output "reconciler_policy_json" {
  description = "Policy document, for attaching to a role outside EKS."
  value       = data.aws_iam_policy_document.reconciler.json
}
