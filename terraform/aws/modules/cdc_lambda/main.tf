# DynamoDB Streams -> Lambda (xstore.cdc.dynamodb_streams.handle_batch).
# Poison records: handle_batch returns batchItemFailures, the mapping bisects
# and retries, then sends failure metadata (shard + sequence range) to the DLQ.

variable "name" {
  type = string
}

variable "role_arn" {
  type = string
}

variable "stream_arn" {
  type = string
}

variable "kms_key_arn" {
  type = string
}

variable "layer_arns" {
  type = list(string)
}

variable "batch_size" {
  type = number
}

variable "max_retry_attempts" {
  type = number
}

variable "repo_root" {
  description = "Repository root; the zip mirrors its layout so xstore path lookups resolve."
  type        = string
}

variable "handler_dir" {
  type = string
}

locals {
  function_name = "${var.name}-cdc-dynamodb"
  package_files = setunion(
    fileset(var.repo_root, "src/xstore/**/*.py"),
    fileset(var.repo_root, "cdc/schema-registry/*.avsc"),
    fileset(var.repo_root, "stores/mappings/*.yaml"),
  )
}

data "archive_file" "package" {
  type        = "zip"
  output_path = "${path.root}/.build/cdc_lambda.zip"

  source {
    filename = "handler.py"
    content  = file("${var.handler_dir}/handler.py")
  }

  dynamic "source" {
    for_each = local.package_files
    content {
      filename = source.value
      content  = file("${var.repo_root}/${source.value}")
    }
  }
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = 14
}

resource "aws_sqs_queue" "dlq" {
  name                              = "${var.name}-cdc-dlq"
  message_retention_seconds         = 1209600
  kms_master_key_id                 = var.kms_key_arn
  kms_data_key_reuse_period_seconds = 300
}

# Only the Lambda on-failure destination writes here; no SQS source queue may
# use this queue as its redrive target.
resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "denyAll"
  })
}

resource "aws_lambda_function" "this" {
  function_name    = local.function_name
  role             = var.role_arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  architectures    = ["x86_64"]
  filename         = data.archive_file.package.output_path
  source_code_hash = data.archive_file.package.output_base64sha256
  layers           = var.layer_arns
  memory_size      = 256
  timeout          = 60

  environment {
    variables = {
      STORE_BACKEND = "real"
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.this.name
  }
}

resource "aws_lambda_event_source_mapping" "stream" {
  event_source_arn                   = var.stream_arn
  function_name                      = aws_lambda_function.this.arn
  starting_position                  = "TRIM_HORIZON"
  batch_size                         = var.batch_size
  maximum_batching_window_in_seconds = 1
  parallelization_factor             = 1
  function_response_types            = ["ReportBatchItemFailures"]
  bisect_batch_on_function_error     = true
  maximum_retry_attempts             = var.max_retry_attempts
  maximum_record_age_in_seconds      = 3600

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.dlq.arn
    }
  }
}

output "function_name" {
  value = aws_lambda_function.this.function_name
}

output "dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.id
}

output "log_group_arn" {
  value = aws_cloudwatch_log_group.this.arn
}
