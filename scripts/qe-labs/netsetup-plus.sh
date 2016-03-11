create_cc_security_group() {
    if [[ `neutron security-group-list | grep "qe_default"` ]]; then
        echo "Security group rule 'qe_default' already exists! Skipping..."
    else
        neutron security-group-create --tenant-id $QE_DEMO_TENANT_UUID --description "default security group for cloudcafe" qe_default
    fi
}

create_glance_images() {
    if [[ `glance image-list | grep "cirros-0.3.2"` ]]; then
        echo "Image for cirros-0.3.2 already exists! Skipping..."
    else
        glance image-create --disk-format qcow2 --container-format bare --owner $QE_DEMO_TENANT_UUID --is-public True --file cirros-0.3.2-x86_64-disk.img  --name cirros-0.3.2
    fi
    if [[ `glance image-list | grep "cirros-0.3.3"` ]]; then
        echo "Image for cirros-0.3.3 already exists! Skipping..."
    else
        glance image-create --disk-format qcow2 --container-format bare --owner $QE_DEMO_TENANT_UUID --is-public True --file cirros-0.3.3-x86_64-disk.img  --name cirros-0.3.3
    fi
}

create_keystone_tenants() {
    if [[ `keystone tenant-list | grep "qe_demo"` ]]; then
        echo "Keystone tenant already exists! Skipping..."
    else
        keystone tenant-create --name qe_demo --description "tenant for cloudcafe testing"
        keystone tenant-create --name qe_demo_alt --description "tenant for cloudcafe testing"
    fi
}

create_keystone_users() {
    if [[ `keystone user-list | grep "qe_demo"` ]]; then
        echo "Keystone user already exists! Skipping..."
    else
        keystone user-create --name qe_demo --tenant qe_demo --pass secrete --email "qe_demo@example.com"
        keystone user-create --name qe_demo_alt --tenant qe_demo_alt --pass secrete --email "qe_demo_alt@example.com"
    fi
}

create_private_net_subnet() {
    if [[ `neutron net-list | grep private` ]]; then
        echo "Network 'private' exists! Skipping..."
    else
        neutron net-create private --provider:network_type=vxlan --provider:segmentation_id=1 --router:external=False --shared
        neutron subnet-create private 172.31.0.0/24 --name private-subnet --gateway 172.31.0.1 --dns-nameservers list=true 8.8.8.8 8.8.4.4
    fi
}

create_public_net_subnet() {
    if [[ `neutron net-list | grep public` ]]; then
        echo "Network 'public' exists! Skipping..."
    else
        neutron net-create public  --provider:network_type=vlan --provider:physical_network=vlan --provider:segmentation_id=$SEGID --router:external=True
        neutron subnet-create public $GATEWAY_CIDR --name public-subnet  --gateway $GATEWAY_IP
    fi
}

get_cirros_images() {
    if [[ `ls cirros-0.3.2-x86_64-disk.img` ]]; then
        echo "Image cirros-0.3.2-x86_64-disk.img already exists! Skipping..."
    else
        wget http://cdn.download.cirros-cloud.net/0.3.2/cirros-0.3.2-x86_64-disk.img
    fi
    if [[ `ls cirros-0.3.3-x86_64-disk.img` ]]; then
        echo "Image cirros-0.3.3-x86_64-disk.img already exists! Skipping..."
    else
        wget http://cdn.download.cirros-cloud.net/0.3.3/cirros-0.3.3-x86_64-disk.img
    fi
}

print_expected_format() {
    echo "Expected format:"
    echo "$0 sat6 lab1"
}

print_parameters() {
    echo $SEGID
    echo $GATEWAY_CIDR
    echo $GATEWAY_IP
    echo "DONE"
}

select_iad_lab1() {
    #IAD LAB 01
    SEGID=280
    GATEWAY_CIDR=10.17.255.192/26
    GATEWAY_IP=10.17.255.193
}

select_iad_lab2() {
    #IAD LAB 02
    SEGID=840
    GATEWAY_CIDR=10.4.213.192/26
    GATEWAY_IP=10.4.213.193
}

select_sat6_lab1() {
    #SAT6 LAB 01
    SEGID=2010
    GATEWAY_CIDR=10.127.101.128/25
    GATEWAY_IP=10.127.101.129
}

select_sat6_lab2() {
    #SAT6 LAB 02
    SEGID=2030
    GATEWAY_CIDR=10.127.102.128/25
    GATEWAY_IP=10.127.102.129
}

set_security_icmp() {
    if [[ `neutron security-group-show default | grep icmp | grep ingress` ]]; then
        echo "Ping ingress rule already exists! Skipping..."
    else
        echo "Creating ping ingression rule"
        neutron security-group-rule-create --direction ingress --protocol icmp default
    fi
}

set_security_inbound_tcp() {
    if [[ `neutron security-group-show default | grep $1 | grep ingress` ]]; then
        echo "Ingress rule for port ($1) already exists! Skipping..."
    else
        echo "Creating ingression rule for port ($1)"
        neutron security-group-rule-create --direction ingress --protocol tcp --port-range-min $1 --port-range-max $1 default
    fi
}

set_security_outbound_tcp() {
    if [[ `neutron security-group-show default | grep $1 | grep egress` ]]; then
        echo "Egress rule for port ($1) already exists! Skipping..."
    else
        echo "Creating egression rule for port ($1)"
        neutron security-group-rule-create --direction egress --protocol tcp --port-range-min $1 --port-range-max $1 default
    fi
}

setup_router() {
    if [[ `neutron router-list | grep "public-private"` ]]; then
        echo "Router already exists! Skipping..."
    else
        neutron router-create public-private
        neutron router-gateway-set public-private public
        neutron router-interface-add public-private private-subnet
    fi
}

# SAT6 Lab 01
if [[ $1 == "sat6" && $2 == "lab01" ]]; then
    select_sat6_lab1
# SAT6 Lab 02
elif [[ $1 == "sat6" && $2 == "lab02" ]]; then
    select_sat6_lab2
# IAD Lab 01
elif [[ $1 == "iad" && $2 == "lab01" ]]; then
    select_iad_lab1
# IAD Lab 02
elif [[ $1 == "iad" && $2 == "lab02" ]]; then
    select_iad_lab2
# Invalid Parameters
else
    print_expected_format
    exit
fi

print_parameters

# Base Network Setup
create_public_net_subnet
create_private_net_subnet
setup_router
set_security_inbound_tcp 22
set_security_outbound_tcp 22
set_security_icmp

# Cloud Cafe Environment Prep
create_keystone_tenants
create_keystone_users
mkdir qe_images
cd qe_images
get_cirros_images
export QE_DEMO_TENANT_UUID=7007261b8eed426a9fbee41766aade6c
create_glance_images
create_cc_security_group
