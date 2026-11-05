.PHONY: tf-aws-validate plan-aws

TF_AWS_DIR := terraform/aws

# Offline: no backend, no credentials. Needs registry.terraform.io for providers.
tf-aws-validate:
	cd $(TF_AWS_DIR) && terraform init -backend=false -input=false
	cd $(TF_AWS_DIR) && terraform validate
	terraform fmt -check -recursive $(TF_AWS_DIR)

# Needs real AWS credentials (or a mocked endpoint); never applies.
plan-aws:
	cd $(TF_AWS_DIR) && terraform init -backend=false -input=false
	cd $(TF_AWS_DIR) && terraform plan -refresh=false -input=false
