# Azure infra (terraform/azure). Never applies; plan needs ARM_SUBSCRIPTION_ID
# and an Entra login (az login or ARM_USE_OIDC=true).
.PHONY: tf-azure-validate plan-azure func-azure-package

TF_AZURE_DIR := terraform/azure
FUNC_AZURE_SRC := $(TF_AZURE_DIR)/functions/cosmos_change_feed
FUNC_AZURE_BUILD := build/functions/cosmos_change_feed

tf-azure-validate:
	cd $(TF_AZURE_DIR) && terraform init -backend=false -input=false
	cd $(TF_AZURE_DIR) && terraform validate
	terraform fmt -check -recursive $(TF_AZURE_DIR)

plan-azure:
	@test -n "$$ARM_SUBSCRIPTION_ID" || (echo "ARM_SUBSCRIPTION_ID is not set" && exit 1)
	cd $(TF_AZURE_DIR) && terraform init -input=false
	mkdir -p build
	cd $(TF_AZURE_DIR) && terraform plan -input=false -out=$(CURDIR)/build/azure.tfplan

# Function package: app files + vendored xstore with the data files it reads.
func-azure-package:
	rm -rf $(FUNC_AZURE_BUILD)
	mkdir -p $(FUNC_AZURE_BUILD)/src $(FUNC_AZURE_BUILD)/stores $(FUNC_AZURE_BUILD)/cdc
	cp $(FUNC_AZURE_SRC)/function_app.py $(FUNC_AZURE_SRC)/host.json $(FUNC_AZURE_SRC)/requirements.txt $(FUNC_AZURE_BUILD)/
	cp -r src/xstore $(FUNC_AZURE_BUILD)/src/
	cp -r stores/mappings $(FUNC_AZURE_BUILD)/stores/
	cp -r cdc/schema-registry $(FUNC_AZURE_BUILD)/cdc/
