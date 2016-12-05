#!/bin/bash
set -x # verbose
set -u # fail on ref before assignment
set -e # terminate script after any non zero exit
set -o pipefail # fail even if later tasks in a pipeline would succeed

# Useful for getting real time feedback from ansible playbook runs
export PYTHONUNBUFFERED=1
env > buildenv

log_git_status(){
  git submodule status
  git branch -v
}

run_rpc_deploy(){
  script=${1:-deploy.sh}
  echo "********************** Run RPC $script ***********************"
  sudo -EH\
    TERM=linux \
    DEPLOY_AIO=yes \
    DEPLOY_HAPROXY=yes \
    DEPLOY_TEMPEST=yes \
    ANSIBLE_GIT_RELEASE=ssh_retry \
    ANSIBLE_GIT_REPO="https://github.com/hughsaunders/ansible" \
    ADD_NEUTRON_AGENT_CHECKSUM_RULE=yes \
    scripts/$script
  echo "********************** RPC $script Completed Succesfully ***********************"
}

run_tempest(){
  # jenkins user does not have the necessary permissions to run lxc commands
  # serial needed to ensure all tests
  echo "********************** Run Tempest ***********************"
  sudo -EH lxc-attach -n $(sudo -E lxc-ls |grep utility) -- /bin/bash -c "RUN_TEMPEST_OPTS='--serial' /opt/openstack_tempest_gate.sh ${TEMPEST_TESTS}"
  echo "********************** Tempest Completed Succesfully ***********************"
}
run_holland(){
  echo "********************** Run Holland  ***********************"
  # Move test_holland.yml playbook to rpc-openstack repo
  cp buildscript_repo/scripts/test_holland.yml /opt/rpc-openstack/rpcd/playbooks
  # Run the playbook
  pushd /opt/rpc-openstack/rpcd/playbooks
    sudo -EH openstack-ansible test_holland.yml
  popd
  # Remove the playbook
  rm /opt/rpc-openstack/rpcd/playbooks/test_holland.yml
  echo "********************** Holland Completed Succesfully ***********************"
}

override_oa(){
  if [[ "$OA_REPO" != "none" ]]; then
    pushd openstack-ansible
      git remote add override $OA_REPO -f
      git fetch -a
      git checkout override/$OA_BRANCH
    popd
  fi
}

integrate_proposed_change(){
  ( git rebase origin/${ghprbTargetBranch} && echo "Rebased ${sha1} on ${ghprbTargetBranch}" ) || \
  ( git rebase --abort
    git merge origin/${ghprbTargetBranch} \
    && echo "Merged ${ghprbTargetBranch} into ${sha1}" ) || {
      echo "Failed to rebase or merge ${sha1} and ${ghprbTargetBranch}, quitting"
      exit 1
    }
}

# Check that this node is clean before running a build.
check_for_existing_config(){
  if [[ -e /opt/rpc-openstack ]]; then
    echo "/opt/rpc-openstack already exists. AIO Gate jobs must be run on clean nodes, quitting."
    exit 1
  fi
}

select_fastest_mirror(){
  script_sha="7f70ca7ff5ccec53fa17cdf4cc64c52a73168c5e"
  script_url="https://raw.githubusercontent.com/openstack/openstack-ansible/\
$script_sha/scripts/fastest-infra-wheel-mirror.py"
  wheel_url=$(curl $script_url | python )
  base_url=${wheel_url%wheel*}
  export UBUNTU_REPO="${base_url}ubuntu"
  # Infra mirrors don't have signatures, so apt config must be updated to
  # allow unsigned packages
  echo 'APT::Get::AllowUnauthenticated "true";' | sudo tee \
    /etc/apt/apt.conf.d/99unauthenticated
  export UNAUTHENTICATED_APT=yes

}


check_for_existing_config

#fix sudoers because jenkins jcloud plugin stamps on it.
sudo tee -a /etc/sudoers <<ESUDOERS
%admin ALL=(ALL) ALL

# Allow members of group sudo to execute any command
%sudo   ALL=(ALL:ALL) ALL

# See sudoers(5) for more information on "#include" directives:

#includedir /etc/sudoers.d
ESUDOERS

# These vars are now job parameters
#UPGRADE=${UPGRADE:-yes}
#UPGRADE_FROM_REF="origin/kilo"
#UPGRADE_FROM_NEAREST_TAG="yes"

if [ "$UPGRADE_FROM_NEAREST_TAG" == "yes" ]
  then
    COMMITISH=$(git describe --tags --abbrev=0 $UPGRADE_FROM_REF)
else
    COMMITISH=$UPGRADE_FROM_REF
fi
if [ "$UPGRADE" == "yes" ]
  then
    git fetch
    git checkout $COMMITISH || {
      echo "Checkout failed, quitting"
      exit 1
    }
else
  integrate_proposed_change
fi

git submodule sync
git submodule update --init

override_oa
log_git_status

# git plugin checks out repo to root of workspace
# but deploy script expects checkout in /opt/rpc-openstack
sudo ln -s $PWD /opt/rpc-openstack

## Add MAAS credentials
uev=/opt/rpc-openstack/rpcd/etc/openstack_deploy/user_zzz_gating_variables.yml
echo "Removing placeholder creds from user_extras_variables"

# Remove placeholder lines
sudo sed -i '/rackspace_cloud_\(auth_url\|tenant_id\|username\|password\|api_key\):/d' /opt/rpc-openstack/rpcd/etc/openstack_deploy/*.yml

echo "Adding MAAS creds to user_extras_variables"
#set +x to avoid leaking creds to the log.
set +x
tee -a $uev >/dev/null <<EOVARS
---
rackspace_cloud_auth_url: ${rackspace_cloud_auth_url}
rackspace_cloud_tenant_id: ${rackspace_cloud_tenant_id}
rackspace_cloud_username: ${rackspace_cloud_username}
rackspace_cloud_password: ${rackspace_cloud_password}
rackspace_cloud_api_key: ${rackspace_cloud_api_key}
EOVARS
set -x

# If horizon extensions are in tree and the appropriate PR trigger vars are
# set, override the horizon extensions git location so that the proposed
# changes are built.
# NOTE(hughsaunders): Any proposed changes to the horizon extensions won't be
# rebased or merged into master before being built. So if the proposed commit's
# parent is old, this test won't guarantee that it works with the current
# master.
if [[ -n "${ghprbAuthorRepoGitUrl:-}" && -n "${ghprbActualCommit:-}" ]]\
      && git ls-tree ${ghprbActualCommit} --name-only \
           | grep -q horizon-extensions; then
  tee -a $uev >/dev/null <<EOVARS
horizon_extensions_git_repo: ${ghprbAuthorRepoGitUrl}
horizon_extensions_git_install_branch: ${ghprbActualCommit}
horizon_extensions_git_install_fragments: "subdirectory=horizon-extensions/"
horizon_extensions_git_package_name: "horizon-extensions"
EOVARS
fi

# Supply fixed cirros image while empty key bug is not fixed upstream.
# Both tempest_img_url and cirros_img_url are overridden since
# cirros_img_url was removed in the newton release, but is still
# needed for our job to run tests againsts previous releases.
# tempest_img_url was introduced in newton and is needed for our tests
# to run the newton tests.
tee -a $uev >/dev/null <<EOVARS
cirros_img_url: "http://rpc-repo.rackspace.com/rpcgating/cirros-0.3.4-x86_64-dropbearmod.img"
tempest_img_url: "http://rpc-repo.rackspace.com/rpcgating/cirros-0.3.4-x86_64-dropbearmod.img"
tempest_images:
  - url: "{{cirros_img_url}}"
    sha256: "ec1120a9310ac3987feee4e3c5108d5d0fd0e594c4283804c17d673ebb2d3769"
tempest_tempest_conf_overrides:
  volume-feature-enabled:
    snapshot: True
EOVARS

# Ensure raw_multi_journal is False for upgrades.
# This is because of the way migrate-yaml.py behaves with the
# '--for-testing-take-new-vars-only'; meaning that the new
# default variables will be set in the user_*_variables_overrides.yml
# file. Since raw_multi_journal is set to False as part of the deploy.sh
# process, but is set to True in Mitaka's
# user_rpco_user_variables_defaults.yml file, this will result in
# migrate-yaml.py adding 'raw_multi_journal: True' in the overrides.
# To avoid this behavior in gate, it is overridden here.
# The same is true for journal_size, and maas_notification_plan.
if [ "$UPGRADE" == "yes" ]; then
    echo "raw_multi_journal: false" | tee -a $uev
    echo "journal_size: 1024" | tee -a $uev
    echo "maas_notification_plan: npTechnicalContactsEmail" | tee -a $uev
    echo "osd_directory: true" | tee -a $uev
fi

if [[ ${UBUNTU_REPO:-auto} == "auto" ]]; then
  select_fastest_mirror
fi

# Set ubuntu repo to supplied value. Effects Host bootstrap, and container default repo.
export BOOTSTRAP_OPTS="${BOOTSTRAP_OPTS:-} bootstrap_host_ubuntu_repo=${UBUNTU_REPO}"
export BOOTSTRAP_OPTS="${BOOTSTRAP_OPTS} bootstrap_host_ubuntu_security_repo=${UBUNTU_REPO}"

# Add any additional vars specified in jenkins job params
echo "${USER_VARS:-}" | tee -a $uev

run_rpc_deploy
run_tempest
run_holland

if [ "$UPGRADE" == "yes" ]; then
    git stash
    git submodule foreach git stash
    git checkout ${sha1}
    if [[ ! -z "${ghprbTargetBranch:-}" ]]; then
      integrate_proposed_change
    fi
    git submodule sync
    git submodule update --init
    override_oa
    log_git_status
    if [[ "$UPGRADE_TYPE" == "major" ]]; then
      # 13.0 renamed upgrade upgrade.sh to test-upgrade.sh
      upgrade_script=$(basename scripts/*upgrade.sh)
      run_rpc_deploy $upgrade_script
    else
      run_rpc_deploy
    fi
    run_tempest
    run_holland
fi

echo
echo "********************** Complete ***********************"
echo "Finished Successfully"
echo
