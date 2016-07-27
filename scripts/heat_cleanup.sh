#!/bin/bash

source /opt/jenkins/creds/mattt_jenkins_dfw
source /opt/jenkins/venvs/rpcheat/bin/activate

set -x

# Delete failed stacks
heat stack-list 2>/dev/null \
    |awk '/(DELETE|CREATE)_FAILED/{print $2}' \
    |while read stack; do heat stack-delete $stack; done

# Find stacks that are currently creating or complete
heat stack-list 2>/dev/null \
    |awk '$2~/-/ && $6~/CREATE_(COMPLETE|IN_PROGRESS)/{print $2}' \
    >stacks_to_keep

echo "*** Stacks to keep ***"
cat stacks_to_keep

# Find stacks that are referrenced by CBS volumes
cinder --os-volume-api-version 1 list 2>/dev/null\
    |awk '$6~/-/{sub(/-.*/,"",$6); t[$6]=1}; END{ for (k in t){ print k }}' \
    >stack_refs

echo "*** Stack refs ***"
cat stack_refs

# delete all volumes that don't reference a stack that should be kept.
while read stack_ref; do
    if ! grep -q $stack_ref stacks_to_keep; then
        cinder --os-volume-api-version 1 list 2>/dev/null\
            |awk '/'$stack_ref'/{print $2}' \
                |while read vol_id; do
                    cinder --os-volume-api-version 1 delete $vol_id 2>/dev/null
                done
    fi
done <stack_refs
