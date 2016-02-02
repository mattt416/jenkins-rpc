__author__ = 'weez8031'

# how to use:
# 1st, we need a cloud-init image, then get the glance image id, eg xxx-aaa-bbb.
# after you get the image id, git clone this file and user_data.file to a compute node.
# run
# python lvmTest.py xxx-aaa-bbb(the glance image id)
# the script will create a bootable volume first, spawn a nova instance on this volume at this compute node, inject a
# bash script(user_data.file) into the cloud-init instance, user_data.file will keep creating files on the instance,
# script will catch any mis-match md5, and will copy the log file to /var/log/lvmtest.log

from keystoneclient import session as ksc_session
from keystoneclient.auth.identity import v3
from keystoneclient.v3 import client as keystone_v3
from novaclient.v2 import client as nova_v2
from cinderclient.v1 import client as cinder_v1
from neutronclient.v2_0 import client as neutron_v2
import os, socket, time, shutil, sys


def get_keystone_creds():
    d = {}
    d['username'] = os.environ['OS_USERNAME']
    d['password'] = os.environ['OS_PASSWORD']
    d['auth_url'] = os.environ['OS_AUTH_URL']
    d['project_name'] = os.environ['OS_PROJECT_NAME']
    d['user_domain_name'] = os.environ['OS_USER_DOMAIN_NAME']
    d['project_domain_name'] = os.environ['OS_PROJECT_DOMAIN_NAME']
    return d


def get_keystone_session():
    cred = get_keystone_creds()
    auth = v3.Password(**cred)
    return ksc_session.Session(auth=auth)


def create_volume_from_image(img_uuid):
    session = get_keystone_session()
    img_uuid = img_uuid
    vol_name = "test"
    cinder = cinder_v1.Client(session=session)
    myvol = cinder.volumes.create(size=10, display_name=vol_name, imageRef=img_uuid)
    while myvol.status != u'available':
        myvol = cinder.volumes.get(myvol.id)
        time.sleep(5)
    return myvol


def get_network_id():
    session = get_keystone_session()
    neutron = neutron_v2.Client(session=session)
    if neutron.list_networks()['networks']:
        networkd_id = neutron.list_networks()['networks'][0]['id']
        return networkd_id
    else:
        return ''


def boot_instance_from_volume(img_uuid):
    hostname = "nova:" + socket.gethostname()
    session = get_keystone_session()
    print "get keystone session"
    nova = nova_v2.Client(session=session)
    print "get the bootable volume, may need some time."
    block_dev_mapping = {'vda': create_volume_from_image(img_uuid).id}
    print "get network"
    network_id = get_network_id()
    with open('user_data.file') as f:
        print "read user_data.file"
        if network_id:
            nics = [{'net-id': network_id}]
            instance = nova.servers.create(name="br-storage-test", image='', block_device_mapping=block_dev_mapping,
                                           flavor=4, nics=nics, userdata=f, availability_zone=hostname)
        else:
            instance = nova.servers.create(name="br-storage-test", image='', block_device_mapping=block_dev_mapping,
                                           flavor=4, userdata=f, availability_zone=hostname)
        print "create instance"
        return instance


def logfile_analyzer(filename):
    filename = filename
    f = open(filename)
    md5code = ''
    md5check = ''
    print "analysing logs now, will quit if there are an md5 mis-match or just click CTRL+C to quit"
    while 1:
        where = f.tell()
        line = f.readline()
        try:
            if not line:
                time.sleep(1)
                f.seek(where)
            else:
                if 'text1.log' in line:
                    md5code = line[:32]
                    md5check = True
                    print md5code
                    continue
                if md5check and 'text' in line:
                    if md5code == line[:32]:
                        continue
                    else:
                        print "found error : " + str(where) + " : " + line
                        break
        except KeyboardInterrupt:
            print "You Pressed the Ctrl+C"
            break
    print "Copy the log file to /var/log/lvmtest.log"
    shutil.copy(filename, '/var/log/lvmtest.log')


def main(image_uuid):
    instance = boot_instance_from_volume(image_uuid)
    time.sleep(10)
    logfile_analyzer('/var/lib/nova/instances/' + str(instance.id) + '/console.log')


if __name__ == "__main__":
    main(str(sys.argv[1]))

