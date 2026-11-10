variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix for resources."
  type        = string
  default     = "xstore"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "azs" {
  description = "Three availability zones; MSK brokers spread one per AZ."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]

  validation {
    condition     = length(var.azs) == 3
    error_message = "Exactly three AZs are required (3 MSK brokers)."
  }
}

variable "vpc_cidr" {
  description = "CIDR for the VPC."
  type        = string
  default     = "10.40.0.0/16"
}

variable "enable_nat" {
  description = "Create a public subnet and one NAT gateway for private egress. Forced on when EKS is enabled."
  type        = bool
  default     = false
}

variable "kafka_version" {
  description = "MSK Kafka version."
  type        = string
  default     = "3.6.0"
}

variable "msk_broker_volume_gb" {
  description = "EBS volume per MSK broker."
  type        = number
  default     = 20
}

variable "enable_debezium_connectors" {
  description = "Deploy the Debezium Postgres connector from cdc/debezium via MSK Connect."
  type        = bool
  default     = false
}

variable "debezium_plugin_s3_key" {
  description = "Object key of the Debezium Postgres plugin zip uploaded to the lake bucket (required when enable_debezium_connectors)."
  type        = string
  default     = "plugins/debezium-connector-postgres.zip"
}

variable "rds_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "rds_allocated_storage_gb" {
  description = "RDS allocated storage."
  type        = number
  default     = 20
}

variable "deletion_protection" {
  description = "Protect RDS, DynamoDB tables from deletion."
  type        = bool
  default     = true
}

variable "create_ledger_table" {
  description = "Create a DynamoDB table for the append-only ledger (default ledger lives in Postgres)."
  type        = bool
  default     = false
}

variable "lake_bucket_name" {
  description = "Lake bucket name; null lets AWS pick a unique name from the prefix."
  type        = string
  default     = null
}

variable "lambda_layer_arns" {
  description = "Layers providing the CDC Lambda's third-party deps (fastavro, pydantic, pyyaml)."
  type        = list(string)
  default     = []
}

variable "cdc_batch_size" {
  description = "DynamoDB stream records per Lambda invocation."
  type        = number
  default     = 100
}

variable "cdc_max_retry_attempts" {
  description = "Retries for a failing stream batch before it goes to the DLQ."
  type        = number
  default     = 3
}

variable "enable_eks" {
  description = "Create an EKS cluster with a managed node group and IRSA OIDC provider."
  type        = bool
  default     = false
}

variable "eks_version" {
  description = "EKS Kubernetes version."
  type        = string
  default     = "1.31"
}

variable "eks_public_endpoint" {
  description = "Expose the EKS API endpoint publicly."
  type        = bool
  default     = false
}

variable "reconciler_namespace" {
  description = "Kubernetes namespace of the reconciler service account (Helm chart, issue #48)."
  type        = string
  default     = "xstore"
}

variable "reconciler_service_account" {
  description = "Kubernetes service account bound to the reconciler IRSA role."
  type        = string
  default     = "xstore-reconciler"
}
