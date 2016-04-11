#!/bin/bash

#Assume in rpc-openstack directory and checked out on commit to test rebased on appropriate branch

function check_tag {
  # Tag sorting makes rc versions newer than the released version
  # This function returns the release if it exists else the rc
  tag_to_test=$1
  t=''
  if [[ $(echo $tag_to_test | egrep '^r[0-9]+\.[0-9]+\.[0-9]+rc[0-9]+$') ]]; then
    t=$(git tag --list $(echo $1 | egrep -o '^r[0-9]+\.[0-9]+\.[0-9]+'))
  fi
  if [[ $t != '' ]]; then
    echo $t
  else
    echo $tag_to_test
  fi
}
current_branch=$1

closest_tag=$(git describe --tags --abbrev=0 origin/$current_branch)

original_ref=$(git rev-parse 'HEAD^{commit}')

git checkout --quiet master
git submodule --quiet update --init
pushd openstack-ansible > /dev/null
  osa_t=$(check_tag $(git describe --abbrev=0))
popd > /dev/null
git checkout --quiet $original_ref
git submodule --quiet update --init

osa_major_number=$(echo "$osa_t" | cut -d. -f1)
# Assume rpc-openstack major version is the same as OpenStack-Ansible tag major version.
master_major_number=$osa_major_number

if [[ $current_branch == 'master' ]]; then
  # If the branch/rc tag process was not followed correctly closest_tag might not show what was expected
  #c_tag=($(echo $closest_tag | egrep -o '[0-9]+\.[0-9]+\.[0-9]+' | cut -d. --output-delimiter=' ' -f1-))
  c_tag=($(git tag --list r[0-9]*| sort -V | tail -n1 | egrep -o '[0-9]+\.[0-9]+\.[0-9]+' | tr '.' ' '))
  branch_major_number=${c_tag[0]}
  if [[ ${c_tag[0]} == $master_major_number ]]; then
    # master should be for the next minor after the one pointed to be closest_tag
    branch_minor_number=$(expr ${c_tag[1]} + 1)
  else
    # master should be for a new major
    branch_major_number=$master_major_number
    branch_minor_number=0
  fi
  current_patch_version=''
else
  branch_major_number=$(echo $current_branch | egrep '^[a-z]+-[0-9]+\.[0-9]+$' | cut -d- -f2 | cut -d. -f1)
  branch_minor_number=$(echo $current_branch | egrep '^[a-z]+-[0-9]+\.[0-9]+$' | cut -d- -f2 | cut -d. -f2)
  current_patch_version=$(echo $closest_tag | egrep "^r${branch_major_number}\.${branch_minor_number}\.[0-9]+[rc0-9]*$")
fi

next_minor_version=$(git tag --list r${branch_major_number}.$(expr ${branch_minor_number} + 1).*  | sort -V | tail -n1)
if [[ $current_branch != 'master' ]] && [[ $next_minor_version == '' ]] && [[ $master_major_number -eq $branch_major_number ]]; then
  next_minor_version=master
fi
next_major_version=$(git tag --list r$(expr ${branch_major_number} + 1).* | sort -V | tail -n1)
if [[ $current_branch != 'master' ]] && [[ $next_major_version == '' ]] && [[ $master_major_number -gt $branch_major_number ]]; then
  next_major_version=master
fi

previous_minor_version=$(check_tag $(git tag --list r${branch_major_number}.$(expr ${branch_minor_number} - 1).* | sort -V | tail -n1))
previous_major_version=$(git tag --list r$(expr ${branch_major_number} - 1).* | sort -V | tail -n1)

>&2 echo "Branch: $current_branch"

>&2 echo "Closest tag: $closest_tag"

>&2 echo "Current patch version: $current_patch_version"

>&2 echo "Next major version: $next_major_version"
>&2 echo "Next minor version: $next_minor_version"

>&2 echo "Previous major version: $previous_major_version"
>&2 echo "Previous minor version: $previous_minor_version"


if [[ $current_patch_version != '' ]]; then
  test_branches="from-$current_patch_version"
else
  test_branches=''
fi

# Assume cannot upgrade to 0 minor release
if [[ $previous_major_version != '' ]] && [[ $branch_minor_number != 0 ]]; then
  test_branches="$test_branches from-$previous_major_version"
fi
if [[ $previous_minor_version != '' ]]; then
  test_branches="$test_branches from-$previous_minor_version"
fi

# Assume upgrading to first minor not possible
if [[ $next_major_version != '' ]] && [[ $next_minor_version != '' ]] && [[ $(echo $next_major_version | egrep -o '\.[0-9]+\.' | cut -d. -f2) != 0 ]]; then
  test_branches="$test_branches to-$next_major_version"
elif [[ $next_minor_version != '' ]]; then
  test_branches="$test_branches to-$next_minor_version"
fi

echo $test_branches
