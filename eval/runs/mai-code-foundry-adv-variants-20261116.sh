#!/bin/bash
# MAI-Code 1.1 Flash on Azure AI Foundry (pay-per-token, user-approved 2026-11-16, ~$3-5, cap $8): the adversarial
# battery at baseline, misleading_hint and terse, all through Foundry so the variant deltas compare like with like
# (its existing baseline is through Copilot). The deployment is created here once the subscription has access
# (2026-11-16: refused, SpecialFeatureOrQuotaIdRequired; the user applied for access). Exit 4 = still no access.
# The resource is looked up from the signed-in Azure CLI: no resource names in the repo.
cd /home/aharmon/BytecodeAgent
RD=eval/runs/mai-code-foundry-adv-variants-20261116
export BCA_FOUNDRY_ACCOUNT=$(az cognitiveservices account list --query "[?kind=='AIServices'] | [0].name" -o tsv)
export BCA_FOUNDRY_RG=$(az cognitiveservices account list --query "[?kind=='AIServices'] | [0].resourceGroup" -o tsv)
export BCA_FOUNDRY_CAP_USD=8
if ! az cognitiveservices account deployment show --name "$BCA_FOUNDRY_ACCOUNT" --resource-group "$BCA_FOUNDRY_RG" \
     --deployment-name mai-code-1.1-flash >/dev/null 2>&1; then
  if ! az cognitiveservices account deployment create --name "$BCA_FOUNDRY_ACCOUNT" --resource-group "$BCA_FOUNDRY_RG" \
       --deployment-name mai-code-1.1-flash --model-name MAI-Code-1.1-Flash --model-version 2026-09-15 \
       --model-format Microsoft --sku-name GlobalStandard --sku-capacity 100 > ~/.local/state/bca/mai-code-deploy.log 2>&1; then  # outside the repo: az output names the resource
    echo "$(date '+%m-%d %T') no access yet" >> $RD.out; exit 4
  fi
  echo "$(date '+%m-%d %T') deployment created" >> $RD.out
fi
.venv/bin/python -m bca.liveeval run --run-dir $RD --tasks adversarial --variant baseline,misleading_hint,terse \
  --model foundry:mai-code-1.1-flash --jobs 2 --max-calls 600 >> $RD.out 2>&1
echo "$(date +%T) exit $?" >> $RD.out
