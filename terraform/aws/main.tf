# AWS mapping of the local stack (ADR-023):
#   Redpanda       -> MSK (IAM auth, TLS)
#   Postgres       -> RDS Postgres 16 with logical replication for Debezium
#   DynamoDB/moto  -> DynamoDB table `xstore` with streams
#   Parquet/Iceberg-> S3 + Glue Data Catalog (Iceberg) + Athena
#   CDC pump       -> Lambda on the DynamoDB stream, SQS DLQ for poison records
#   docker compose -> EKS (optional) for the reconciler Helm chart (issue #48)

locals {
  name = "${var.project}-${var.environment}"
}

# One customer-managed key for data at rest. The default key policy delegates
# to IAM, so each role below gets only the kms actions it needs.
resource "aws_kms_key" "data" {
  description             = "${local.name} data at rest"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "data" {
  name          = "alias/${local.name}-data"
  target_key_id = aws_kms_key.data.key_id
}

module "network" {
  source = "./modules/network"

  name       = local.name
  cidr       = var.vpc_cidr
  azs        = var.azs
  enable_nat = var.enable_nat || var.enable_eks
}

module "dynamodb" {
  source = "./modules/dynamodb"

  kms_key_arn         = aws_kms_key.data.arn
  deletion_protection = var.deletion_protection
  create_ledger_table = var.create_ledger_table
  ledger_table_name   = "${local.name}-ledger"
}

module "rds" {
  source = "./modules/rds"

  name                = local.name
  subnet_ids          = module.network.private_subnet_ids
  security_group_id   = module.network.rds_security_group_id
  kms_key_arn         = aws_kms_key.data.arn
  instance_class      = var.rds_instance_class
  allocated_storage   = var.rds_allocated_storage_gb
  deletion_protection = var.deletion_protection
}

module "lake" {
  source = "./modules/lake"

  name        = local.name
  bucket_name = var.lake_bucket_name
  kms_key_arn = aws_kms_key.data.arn
}

module "msk" {
  source = "./modules/msk"

  name              = local.name
  kafka_version     = var.kafka_version
  subnet_ids        = module.network.private_subnet_ids
  security_group_id = module.network.msk_security_group_id
  kms_key_arn       = aws_kms_key.data.arn
  volume_size_gb    = var.msk_broker_volume_gb

  enable_debezium_connectors = var.enable_debezium_connectors
  client_security_group_id   = module.network.client_security_group_id
  plugin_bucket_arn          = module.lake.bucket_arn
  plugin_s3_key              = var.debezium_plugin_s3_key
  postgres_host              = module.rds.address
  postgres_secret_arn        = module.rds.master_user_secret_arn
  postgres_connector_json    = "${path.root}/../../cdc/debezium/postgres-connector.json"
}

module "eks" {
  source = "./modules/eks"
  count  = var.enable_eks ? 1 : 0

  name                     = local.name
  kubernetes_version       = var.eks_version
  subnet_ids               = module.network.private_subnet_ids
  node_security_group_ids  = [module.network.client_security_group_id]
  kms_key_arn              = aws_kms_key.data.arn
  endpoint_public_access   = var.eks_public_endpoint
  client_security_group_id = module.network.client_security_group_id
}

module "iam" {
  source = "./modules/iam"

  name        = local.name
  kms_key_arn = aws_kms_key.data.arn

  # CDC Lambda
  stream_arn    = module.dynamodb.stream_arn
  dlq_arn       = module.cdc_lambda.dlq_arn
  log_group_arn = module.cdc_lambda.log_group_arn

  # Reconciler (IRSA)
  create_reconciler_role     = var.enable_eks
  oidc_provider_arn          = var.enable_eks ? module.eks[0].oidc_provider_arn : null
  oidc_issuer                = var.enable_eks ? module.eks[0].oidc_issuer : null
  reconciler_namespace       = var.reconciler_namespace
  reconciler_service_account = var.reconciler_service_account
  dynamodb_table_arns        = module.dynamodb.table_arns
  lake_bucket_arn            = module.lake.bucket_arn
  glue_catalog_id            = module.lake.glue_catalog_id
  glue_database_name         = module.lake.glue_database_name
  athena_workgroup_arn       = module.lake.athena_workgroup_arn
  msk_cluster_arn            = module.msk.cluster_arn
  rds_secret_arn             = module.rds.master_user_secret_arn
}

module "cdc_lambda" {
  source = "./modules/cdc_lambda"

  name               = local.name
  role_arn           = module.iam.cdc_lambda_role_arn
  stream_arn         = module.dynamodb.stream_arn
  kms_key_arn        = aws_kms_key.data.arn
  layer_arns         = var.lambda_layer_arns
  batch_size         = var.cdc_batch_size
  max_retry_attempts = var.cdc_max_retry_attempts
  repo_root          = "${path.root}/../.."
  handler_dir        = "${path.root}/lambda"
}
