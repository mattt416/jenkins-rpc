# Functions used for constructing jenkins jobs.

SSH_KEY="/opt/jenkins/creds/id_rsa_cloud10_jenkins"
uev=/opt/rpc-openstack/rpcd/etc/openstack_deploy/user_zzz_gating_variables.yml
KEEP_INSTANCE=${KEEP_INSTANCE:-no}
PS1=${PS1:->}

abort(){
  # This kills children, but not the shell itself
  pkill -P $$ --signal 9
  exit 1
}

set_shopts_env(){
  set -x # verbose
  set -u # fail on ref before assignment
  set -e # terminate script after any non zero exit
  set -o pipefail # fail even if later tasks in a pipeline would succeed

  # Useful for getting real time feedback from ansible playbook runs
  export PYTHONUNBUFFERED=1
  env > buildenv

  # Set ubuntu repo to supplied value. Effects Host bootstrap, and container default repo.
  export BOOTSTRAP_OPTS="${BOOTSTRAP_OPTS:-} bootstrap_host_ubuntu_repo=${UBUNTU_REPO}"
  export BOOTSTRAP_OPTS="${BOOTSTRAP_OPTS} bootstrap_host_ubuntu_security_repo=${UBUNTU_REPO}"
}
set_shopts_env

managed_cleanup(){
  echo "Starting cleanup"
  set +e

  # Retrieve artefacts for archival
  on_remote prep_artefacts ||:
  . $WORKSPACE/instance.env ||:

  if [[ -n "${UPGRADE_PATH:-}" ]]
  then
    destination="${WORKSPACE}/archive_${UPGRADE_PATH}"
  else
    destination="archive"
  fi
  if scp \
    -o "StrictHostKeyChecking=no" \
    -i $SSH_KEY \
    -r \
    root@${accessipv4}:$WORKSPACE/archive "${WORKSPACE}/$destination"
  then
    echo "Artifact Gathering Succeeded"
  else
    echo "Artifact Gathering Failed"
  fi

  if [[ "${KEEP_INSTANCE:-no}" == "no" ]]
  then
    openrc_from_maas_vars
    for var in WORKSPACE OS_REGION_NAME OS_PROJECT_NAME OS_PASSWORD OS_AUTH_URL OS_USERNAME OS_API_KEY
    do
      export $var
    done
    export -f teardown_instance
    timeout 300 bash -c teardown_instance
  fi
  # This kills children, but not the shell itself
  pkill -P $$ --signal 9
  echo "Cleanup complete"
}

managed_aio(){

  image="${1:-}"

  # need pip, novalcient and openstack client to boot the instance that
  # will contain the aio.
  #install_pip
  #pip install python-novaclient python-openstackclient

  # managed_cleanup is now called as a seperate shell step.
  trap abort SIGHUP SIGINT SIGTERM
  openrc_from_maas_vars
  # reduce job name to first character of each word
  # eg jjb-ugprade-matrix --> jum
  # This is to avoid hitting the container name limit
  short_job_name=$(awk -F'[-_]' '{ORS=""
                                 for(i=1; i<=NF; i++) c[i]=substr($i, 0, 1)}
                                 END{for(i=1; i<=length(c); i++)
                                 {print tolower(c[i]) }}' <<<$JOB_NAME)
  get_instance "${short_job_name}-${BUILD_ID}" "${image}"
  on_remote clone_rpc "$sha1"
  on_remote aio
  deploy_result=$?
  # work out working dir on remote, for executing stuff and collecting artefacts
  return $deploy_result
  # teardown called by exit handler
}

aio(){
  prep
  deploy
  deploy_result=$?
  upgrade
  return $deploy_result
}

managed_aio_artifacts(){

  image="${1:-}"

  # need pip, novalcient and openstack client to boot the instance that
  # will contain the aio.
  #install_pip
  #pip install python-novaclient python-openstackclient

  # managed_cleanup is now called as a seperate shell step.
  trap abort SIGHUP SIGINT SIGTERM
  openrc_from_maas_vars
  # reduce job name to first character of each word
  # eg jjb-ugprade-matrix --> jum
  # This is to avoid hitting the container name limit
  short_job_name=$(awk -F'[-_]' '{ORS=""
                                 for(i=1; i<=NF; i++) c[i]=substr($i, 0, 1)}
                                 END{for(i=1; i<=length(c); i++)
                                 {print tolower(c[i]) }}' <<<$JOB_NAME)
  get_instance "${short_job_name}-${BUILD_ID}" "${image}"
  on_remote clone_rpc "$sha1"
  on_remote aio_artifact_build
  deploy_result=$?
  # work out working dir on remote, for executing stuff and collecting artefacts
  return $deploy_result
  # teardown called by exit handler
}

aio_artifact_build(){
  prep
  export TERM=linux
  sudo -E /opt/rpc-openstack/scripts/artifacts-building/python/build-python-artifacts.sh
  deploy_result=$?
  return $deploy_result
}

prep(){
  skip_if_doc_only
  check_for_existing_config
  jcloud_node_fixes
  git_prep
  override_oa
  log_git_status
  set_maas_creds
  integrate_horizon_extensions_changes
  override_cirros_image
  ceph_upgrade_prep

  # Add any additional vars specified in jenkins job params
  echo "${USER_VARS:-}" | tee -a $uev
}

deploy(){
  run_rpc_deploy
  run_tests
}


git_prep(){
  git config --global user.email "rpcgateuser@rackspace.com"
  git config --global user.name "rpcgateuser"
  # Prepare the rpc repo for the deploy, which may be the old version
  # for an upgrade job
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
  # git plugin checks out repo to root of workspace
  # but deploy script expects checkout in /opt/rpc-openstack
  sudo ln -s $PWD /opt/rpc-openstack
}

ceph_upgrade_prep(){
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
}


upgrade(){
  if [ "$UPGRADE" == "yes" ]; then
      git reset --hard
      git submodule foreach git reset --hard
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
}

override_cirros_image(){
  #Supply fixed cirros image while empty key bug is not fixed upstream.
  tee -a $uev >/dev/null <<EOVARS
# (alextricity25) Adding the cirros_*_url vars. This var has been dropped from OSA in
# newton so we are carrying it here so we have support for both newton and earlier releases.
cirros_tgz_url: "http://download.cirros-cloud.net/{{ cirros_version }}/cirros-{{ cirros_version }}-x86_64-uec.tar.gz"
cirros_img_url: "http://rpc-repo.rackspace.com/rpcgating/cirros-0.3.4-x86_64-dropbearmod.img"
# (alextricity25) This variable is used to populate the image directives in tempest.conf in stable/newton.
# We can't rely on the upstream value of this variable to be the same as cirros_img_url, so we also
# override this variable here.
tempest_image_file: "cirros-0.3.4-x86_64-dropbearmod.img"
tempest_images:
  - url: "{{cirros_img_url}}"
    sha256: "ec1120a9310ac3987feee4e3c5108d5d0fd0e594c4283804c17d673ebb2d3769"
  - url: "{{cirros_tgz_url}}"
    sha256: "95e77c7deaf0f515f959ffe329918d5dd23e417503d1d45e926a888853c90710"
tempest_tempest_conf_overrides:
  volume-feature-enabled:
    snapshot: True
EOVARS
}

integrate_horizon_extensions_changes(){
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
}

set_maas_creds(){
  ## Add MAAS credentials
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
}

jcloud_node_fixes(){
  #fix sudoers because jenkins jcloud plugin stamps on it.
  sudo tee -a /etc/sudoers <<ESUDOERS
%admin ALL=(ALL) ALL

# Allow members of group sudo to execute any command
%sudo   ALL=(ALL:ALL) ALL

# See sudoers(5) for more information on "#include" directives:

#includedir /etc/sudoers.d
ESUDOERS
}

log_git_status(){
  git submodule status
  git branch -v
}

run_rpc_deploy(){
  script=${1:-deploy.sh}
  echo "********************** Run RPC $script ***********************"
  sudo -E\
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

run_tests(){
  run_tempest
  run_holland
}

run_tempest(){
  # jenkins user does not have the necessary permissions to run lxc commands
  # serial needed to ensure all tests
  echo "********************** Run Tempest ***********************"
  sudo -E lxc-attach -n $(sudo -E lxc-ls |grep utility) -- /bin/bash -c "RUN_TEMPEST_OPTS='--serial' /opt/openstack_tempest_gate.sh ${TEMPEST_TESTS}"
  echo "********************** Tempest Completed Succesfully ***********************"
}
run_holland(){
  echo "********************** Run Holland  ***********************"
  # Move test_holland.yml playbook to rpc-openstack repo
  cp ${WORKSPACE}/jenkins-rpc/scripts/test_holland.yml /opt/rpc-openstack/rpcd/playbooks
  # Run the playbook
  pushd /opt/rpc-openstack/rpcd/playbooks
    sudo -E openstack-ansible test_holland.yml
  popd
  # Remove the playbook
  rm /opt/rpc-openstack/rpcd/playbooks/test_holland.yml
  echo "********************** Holland Completed Succesfully ***********************"
}

override_oa(){
  if [[ "${OA_REPO:-none}" != "none" ]]; then
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

skip_if_doc_only(){
  # Skip build if only doc changes are detected.
  # Doc changes should only be skipped in PR jobs, not periodics.

  cd $WORKSPACE/rpc-openstack

  # remove origin/ from sha1 if it is alredy prepended.
  sha1="${sha1#origin/}"
  if [[ "${ROOT_BUILD_CAUSE}" == "GHPRBCAUSE" ]]; then
    # long stat widths specified to ensure paths aren't truncated
    (git show --stat=400,400 origin/$sha1 \
        || git show --stat=400,400 $sha1 )\
        |awk '/\|/{print $1}' \
        |egrep -v -e '.*md$' -e '.*rst$' -e '^releasenotes/' \
        || { echo "Skipping AIO build as no non-doc changes were detected"
            #prep_artefacts
            exit 0
           }
  fi
}

prep_artefacts(){
  set +e
  echo "****** Copy Logs To Jenkins Workspace For Archival *******"

  #copy logs into jenkins workspace so they can be archived with the results
  mkdir -p archive
  cp -rL /openstack/log archive/openstack
  cp -rL /var/log archive/local

  # Copy horizon error.log files into jenkins archive
  find /var/lib/lxc/*horizon*/rootfs/var/log/apache2 -name error.log \
    |while read log; do \
    container=$(cut -d/ -f 5 <<<$log); \
    cp $log archive/openstack/$container; done

  # collect openstack etc dirs from containers
  find /var/lib/lxc/*/rootfs/etc/ -name policy.json -o -name swift.conf \
    |while read policy; do \
    src=$(dirname $policy); \
    service=$(basename $src); \
    container=$(cut -d/ -f 5 <<<$src); \
    mkdir -p archive/etc/$container/; cp -r $src/ archive/etc/$container/;  done

  # collect openstack etc dirs from host
  find /etc -name policy.json -o -name swift.conf\
    |while read policy; do \
    src=$(dirname $policy); \
    mkdir -p archive/etc; cp -r $src/ archive/etc/;  done

  # Collect Rabbit, Mysql & Memcached configs
  # Copy from all containers to one dir, don't care that it will get overridden
  # should be the same anyway.
  cp -r /var/lib/lxc/*rabbit*/rootfs/etc/rabbitmq archive/etc/
  cp -r /var/lib/lxc/*galera*/rootfs/etc/mysql archive/etc/
  cp -r /var/lib/lxc/*memcached*/rootfs/etc/memcached.conf archive/etc/

  # Collect MAAS agent config
  cp -r /etc/rackspace-monitoring-agent.conf.d archive/etc/
  cp /etc/rackspace-monitoring-agent.cfg archive/etc/

  # Jenkins user must be able to read all files to archive and transfer to
  # the master. Unreadable files will cause the whole archive operation to fail
  #chown -R jenkins archive

  # remove dangling/circular symlinks
  find archive -type l -exec test ! -e {} \; -delete

  # delete anything that isn't a file or directory - pipes, specials etc
  find archive ! -type d  ! -type f -delete

  # delete anything not readable by the jenkins user
  find archive \
    |while read f; \
    do [ -r $f ] || { echo $f; rm -rf $f; }; done

  # Delete anything over 1GB. This is to prevent suspiciously large
  # logs from filling up the artefact store.
  find . -size +1G -delete

  #don't return non-zero, ever.
  :
}


get_image(){
  nova image-list  |awk -F\| '/'"$1"'.*PVHVM/{print $2; exit}'|tr -d '\n '
}


install_pip(){
  GET_PIP=${GET_PIP:-https://bootstrap.pypa.io/get-pip.py}
  [[ -e get_pip.py ]] || {
    curl $GET_PIP > get_pip.py
    python get_pip.py
  }
}

openrc_from_maas_vars(){
  # Translate env vars set for maas variables into creds that the
  # openstack clients can use.
  set +x
  export OS_REGION_NAME=IAD
  export OS_PROJECT_NAME="$rackspace_cloud_tenant_id"
  export OS_PASSWORD="$rackspace_cloud_password"
  export OS_AUTH_URL="https://identity.api.rackspacecloud.com/v2.0/"
  export OS_USERNAME="$rackspace_cloud_username"
  export OS_API_KEY="$rackspace_cloud_api_key"
  set -x
}

get_instance(){
  . /opt/jenkins/venvs/osclients/bin/activate
  instance_name="${1}-${RANDOM}"
  image_pattern="${2:-Ubuntu 14.04 LTS}"
  echo "Booting Instance: $instance_name" >&2
  flavor="performance2-15"
  image="$(get_image "${image_pattern}")"
  key_name=jenkins

  for i in {1..3}
  do
    nova delete $instance_name &>/dev/null ||:
    nova boot \
      --image "$image"\
      --flavor "$flavor"\
      --key-name "$key_name"\
      --poll\
      --config-drive=true\
      $instance_name \
      >&2 \
      ||:
    for i in {1..15}
    do
      id="$(openstack server list -f value \
        | awk '/'${instance_name}'.*ACTIVE/{print $1}')" && break ||:
      sleep 15
    done
    #environment variables can't contain :, so remove them from keys.
    openstack server show "$id" -fshell \
      |awk -F= '{OFS="="; gsub(/[-:]/, "_", $1); print}' \
      >$WORKSPACE/instance.env \
      && return 0 ||:
    echo "failed to boot instance ${instance_name}, retrying" >&2
    sleep 60
  done
  deactivate||:
  return 1
}

teardown_instance(){
  . $WORKSPACE/instance.env
  instance_name="$name"
  set +x
  . /opt/jenkins/venvs/osclients/bin/activate
  set -x
  echo "Deleting instance: $1"
  for i in {1..5}
  do
    nova delete "$instance_name"
    sleep 60
    openstack server list -f value | grep "$instance_name" \
      || { echo "Succesfully removed $instance_name"; return 0; }
  done
  echo "Failed to teardown $instance_name"
  return 1
}

on_remote(){
  cmd="${@}"
  . $WORKSPACE/instance.env
  remote="$accessipv4"
  env
  ship_env_keys=(
    UPGRADE
    sha1
    ghprbActualCommit
    ghprbTargetBranch
    ghprbAuthorRepoGitUrl
    UPGRADE_FROM_REF
    UPGRADE_FROM_NEAREST_TAG
    UPGRADE_TYPE
    DEPLOY_CEPH
    DEPLOY_MAAS
    UBUNTU_REPO
    ROOT_BUILD_CAUSE
    WORKSPACE
    JOB_NAME
    BUILD_ID
    TEMPEST_TESTS
    JENKINS_RPC_REPO
    JENKINS_RPC_BRANCH
    USER_VARS
    rackspace_cloud_api_key
    rackspace_cloud_auth_url
    rackspace_cloud_password
    rackspace_cloud_tenant_id
    rackspace_cloud_username
    REPO_KEY
    REPO_HOST
    REPO_USER
  )

  #Dump env vars which will be required on the remote host
  : > $WORKSPACE/ship.env
  for env_key in ${ship_env_keys[@]}
  do
    echo "Storing $env_key=${!env_key:-} for shipping to instance"
    echo "export $env_key=\"${!env_key:-}\"" >> $WORKSPACE/ship.env
  done

  user="${ssh_user:-root}"
  key="${ssh_key:-/opt/jenkins/creds/id_rsa_cloud10_jenkins}"
  lib="${WORKSPACE}/jenkins-rpc/scripts/gating_bash_lib.sh"

  scp \
    -i "$key" \
    -o "StrictHostKeyChecking=no" \
    $WORKSPACE/ship.env "${user}@${remote}:~/ship.env"

  ssh "${user}@${remote}" \
    -i "$key" \
    -o "StrictHostKeyChecking=no" \
    "mkdir -p ${WORKSPACE}
     cd ${WORKSPACE}
     . ~/ship.env
     which git || {
       apt-get update
       apt-get install git -fy
     }
     [ -e \"jenkins-rpc\" ] || {
        git clone \"${JENKINS_RPC_REPO}\" jenkins-rpc
        pushd jenkins-rpc
          git fetch -a
          git checkout ${JENKINS_RPC_BRANCH}
        popd
     }
     . $lib
     echo On_remote shell options: \$-
     echo On_remote command: $cmd
     $cmd"
}

clone_rpc(){
  ref="${1:-master}"
  repo="${2:-https://github.com/rcbops/rpc-openstack}"
  dest="${3:-$WORKSPACE/$(basename $repo)}"
  mkdir -p $(dirname $dest)
  git clone --recursive "$repo" "$dest"
  pushd $dest
    git fetch origin "+refs/pull/*:refs/remotes/origin/pr/*"
    git fetch -a
    git checkout "$ref"
    git submodule update --init
  popd
}
