# MSK replaces local Redpanda: 3 x kafka.t3.small, IAM (SASL) auth over TLS,
# KMS encryption at rest. Clients connect on 9098 (bootstrap_brokers_sasl_iam).

variable "name" {
  type = string
}

variable "kafka_version" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_id" {
  type = string
}

variable "kms_key_arn" {
  type = string
}

variable "volume_size_gb" {
  type = number
}

variable "enable_debezium_connectors" {
  type = bool
}

variable "client_security_group_id" {
  type = string
}

variable "plugin_bucket_arn" {
  type = string
}

variable "plugin_s3_key" {
  type = string
}

variable "postgres_host" {
  type = string
}

variable "postgres_secret_arn" {
  type = string
}

variable "postgres_connector_json" {
  description = "Path to cdc/debezium/postgres-connector.json."
  type        = string
}

data "aws_region" "current" {}

resource "aws_cloudwatch_log_group" "msk" {
  name              = "/aws/msk/${var.name}"
  retention_in_days = 14
}

resource "aws_msk_configuration" "this" {
  name           = "${var.name}-kafka"
  kafka_versions = [var.kafka_version]

  server_properties = <<-EOT
    auto.create.topics.enable=true
    default.replication.factor=3
    min.insync.replicas=2
    num.partitions=6
  EOT
}

resource "aws_msk_cluster" "this" {
  cluster_name           = var.name
  kafka_version          = var.kafka_version
  number_of_broker_nodes = 3

  broker_node_group_info {
    instance_type   = "kafka.t3.small"
    client_subnets  = var.subnet_ids
    security_groups = [var.security_group_id]

    storage_info {
      ebs_storage_info {
        volume_size = var.volume_size_gb
      }
    }
  }

  client_authentication {
    unauthenticated = false
    sasl {
      iam = true
    }
  }

  encryption_info {
    encryption_at_rest_kms_key_arn = var.kms_key_arn
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  configuration_info {
    arn      = aws_msk_configuration.this.arn
    revision = aws_msk_configuration.this.latest_revision
  }

  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = aws_cloudwatch_log_group.msk.name
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Optional: Debezium connectors from cdc/debezium/*.json via MSK Connect.
# Only the Postgres connector maps to this stack (MongoDB has no AWS target
# here). The JSON is reused as-is with AWS overrides: RDS host, credentials
# from Secrets Manager via the MSK config provider, TLS to RDS, and JSON
# converters (no Confluent schema registry on AWS). The plugin zip (Debezium
# Postgres + msk-config-providers jars) must be uploaded to the lake bucket.
# ---------------------------------------------------------------------------

locals {
  debezium_base = jsondecode(file(var.postgres_connector_json)).config

  # Kafka's provider:path:key placeholder splits on ":", so pass the secret
  # name (ARN minus the 6-char suffix), not the ARN.
  postgres_secret_name = regex("secret:(.+)-[A-Za-z0-9]{6}$", var.postgres_secret_arn)[0]

  debezium_config = merge(
    {
      for k, v in local.debezium_base : k => v
      if !strcontains(k, "schema.registry.url") && !startswith(k, "errors.deadletterqueue")
    },
    {
      "database.hostname"              = var.postgres_host
      "database.user"                  = "$${secretsmanager:${local.postgres_secret_name}:username}"
      "database.password"              = "$${secretsmanager:${local.postgres_secret_name}:password}"
      "database.sslmode"               = "require"
      "key.converter"                  = "org.apache.kafka.connect.json.JsonConverter"
      "value.converter"                = "org.apache.kafka.connect.json.JsonConverter"
      "key.converter.schemas.enable"   = "false"
      "value.converter.schemas.enable" = "false"
      "tasks.max"                      = "1"
    },
  )
}

data "aws_iam_policy_document" "connect_assume" {
  count = var.enable_debezium_connectors ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["kafkaconnect.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "connect" {
  count = var.enable_debezium_connectors ? 1 : 0

  statement {
    sid       = "Cluster"
    actions   = ["kafka-cluster:Connect", "kafka-cluster:DescribeCluster"]
    resources = [aws_msk_cluster.this.arn]
  }

  statement {
    sid = "Topics"
    actions = [
      "kafka-cluster:CreateTopic",
      "kafka-cluster:DescribeTopic",
      "kafka-cluster:WriteData",
      "kafka-cluster:ReadData",
    ]
    resources = ["${replace(aws_msk_cluster.this.arn, ":cluster/", ":topic/")}/*"]
  }

  statement {
    sid       = "Groups"
    actions   = ["kafka-cluster:AlterGroup", "kafka-cluster:DescribeGroup"]
    resources = ["${replace(aws_msk_cluster.this.arn, ":cluster/", ":group/")}/*"]
  }

  statement {
    sid       = "PostgresSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.postgres_secret_arn]
  }

  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.msk.arn}:*"]
  }
}

resource "aws_iam_role" "connect" {
  count              = var.enable_debezium_connectors ? 1 : 0
  name               = "${var.name}-msk-connect"
  assume_role_policy = data.aws_iam_policy_document.connect_assume[0].json
}

resource "aws_iam_role_policy" "connect" {
  count  = var.enable_debezium_connectors ? 1 : 0
  name   = "debezium"
  role   = aws_iam_role.connect[0].id
  policy = data.aws_iam_policy_document.connect[0].json
}

resource "aws_mskconnect_custom_plugin" "debezium_postgres" {
  count        = var.enable_debezium_connectors ? 1 : 0
  name         = "${var.name}-debezium-postgres"
  content_type = "ZIP"

  location {
    s3 {
      bucket_arn = var.plugin_bucket_arn
      file_key   = var.plugin_s3_key
    }
  }
}

resource "aws_mskconnect_worker_configuration" "this" {
  count = var.enable_debezium_connectors ? 1 : 0
  name  = "${var.name}-worker"

  properties_file_content = <<-EOT
    key.converter=org.apache.kafka.connect.json.JsonConverter
    value.converter=org.apache.kafka.connect.json.JsonConverter
    config.providers=secretsmanager
    config.providers.secretsmanager.class=com.amazonaws.kafka.config.providers.SecretsManagerConfigProvider
    config.providers.secretsmanager.param.region=${data.aws_region.current.name}
  EOT
}

resource "aws_mskconnect_connector" "postgres" {
  count = var.enable_debezium_connectors ? 1 : 0

  name                       = "xstore-postgres"
  kafkaconnect_version       = "2.7.1"
  connector_configuration    = local.debezium_config
  service_execution_role_arn = aws_iam_role.connect[0].arn

  capacity {
    provisioned_capacity {
      mcu_count    = 1
      worker_count = 1
    }
  }

  kafka_cluster {
    apache_kafka_cluster {
      bootstrap_servers = aws_msk_cluster.this.bootstrap_brokers_sasl_iam
      vpc {
        security_groups = [var.client_security_group_id]
        subnets         = var.subnet_ids
      }
    }
  }

  kafka_cluster_client_authentication {
    authentication_type = "IAM"
  }

  kafka_cluster_encryption_in_transit {
    encryption_type = "TLS"
  }

  plugin {
    custom_plugin {
      arn      = aws_mskconnect_custom_plugin.debezium_postgres[0].arn
      revision = aws_mskconnect_custom_plugin.debezium_postgres[0].latest_revision
    }
  }

  worker_configuration {
    arn      = aws_mskconnect_worker_configuration.this[0].arn
    revision = aws_mskconnect_worker_configuration.this[0].latest_revision
  }

  log_delivery {
    worker_log_delivery {
      cloudwatch_logs {
        enabled   = true
        log_group = aws_cloudwatch_log_group.msk.name
      }
    }
  }

  depends_on = [aws_iam_role_policy.connect]
}

output "cluster_arn" {
  value = aws_msk_cluster.this.arn
}

output "bootstrap_brokers_sasl_iam" {
  value = aws_msk_cluster.this.bootstrap_brokers_sasl_iam
}
