output "vpc_id" {
  value = module.network.vpc_id
}

output "msk_cluster_arn" {
  value = module.msk.cluster_arn
}

output "msk_bootstrap_brokers_sasl_iam" {
  description = "Bootstrap servers for IAM-authenticated TLS clients (port 9098)."
  value       = module.msk.bootstrap_brokers_sasl_iam
}

output "rds_endpoint" {
  value = module.rds.endpoint
}

output "rds_master_user_secret_arn" {
  description = "Secrets Manager secret holding the RDS master credentials."
  value       = module.rds.master_user_secret_arn
}

output "dynamodb_table_name" {
  value = module.dynamodb.table_name
}

output "dynamodb_stream_arn" {
  value = module.dynamodb.stream_arn
}

output "lake_bucket" {
  value = module.lake.bucket_name
}

output "glue_database" {
  value = module.lake.glue_database_name
}

output "athena_workgroup" {
  value = module.lake.athena_workgroup_name
}

output "cdc_lambda_name" {
  value = module.cdc_lambda.function_name
}

output "cdc_dlq_url" {
  value = module.cdc_lambda.dlq_url
}

output "reconciler_role_arn" {
  description = "IRSA role for the reconciler service account; annotate the SA with it."
  value       = module.iam.reconciler_role_arn
}

output "eks_cluster_name" {
  value = var.enable_eks ? module.eks[0].cluster_name : null
}
