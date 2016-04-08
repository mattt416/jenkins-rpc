#!/usr/bin/env python

# stdlib
import os
import re

# 3rd party
import click
import jinja2

# openstack
from heatclient.client import Client as heatclient
from keystoneauth1.identity import v2
from keystoneauth1 import session
# from keystoneclient.v2_0 import client as ksclient


class TimeoutException(Exception):
    pass


class StackNotFoundException(Exception):
    pass


def keystone_auth():
    """Auth from OS_ env vars """
    auth_plugin = v2.Password(
        auth_url=os.environ['OS_AUTH_URL'],
        username=os.environ['OS_USERNAME'],
        password=os.environ['OS_PASSWORD'],
        tenant_name=os.environ['OS_TENANT_NAME'])
    s = session.Session(auth=auth_plugin)
    return (s, auth_plugin)


def get_heatclient():
    s, auth_plugin = keystone_auth()
    token = auth_plugin.get_token(session=s)
    endpoint = auth_plugin.get_endpoint(
        session=s,
        service_type='orchestration',
        region_name=os.environ['OS_REGION_NAME'])
    return heatclient('1',
                      token=token,
                      endpoint=endpoint)


def wait_for_status_change(stack_name,
                           current_status,
                           interval=15,
                           tries=240*4):
    heat = get_heatclient()
    for _ in range(tries):
        stack = heat.stacks.find(stack_name=stack_name)
        if stack.stack_status != current_status:
            return stack.stack_status
    raise TimeoutException('Timeout waiting for {name} to change status '
                           'from {status}'.format(name=stack_name,
                                                  status=current_status))


def get_stack(stack_name):
    heat = get_heatclient()
    for stack in heat.stacks.list():
        if stack.stack_name == stack_name:
            stack.get()
            return stack
    raise StackNotFoundException(
        "Stack not found: {stack}".format(stack=stack_name))


@click.group()
def cli():
    pass


@cli.command()
def list_stacks():
    heat = get_heatclient()
    print(list(heat.stacks.list()))


@cli.command()
@click.argument('name')
@click.argument('rpc_version')
@click.option('--rpc_repo', default="https://github.com/rcbops/rpc-openstack")
@click.option('--template_repo',
              default="https://github.com/cloud-training/rpc-heat-ansible")
@click.option('--template_version', default="master")
def create_stack(name, rpc_version, rpc_repo, template_repo, template_version):
   pass
   #heat = get_heatclient()


@cli.command()
@click.argument('name')
@click.option('-c', '--connect', help="connect to this host")
def ssh(name, connect):
    stack = get_stack(name)

    # stack.outputs is a list of dicts, convert to key:value
    outputs = {}
    for output in stack.outputs:
        outputs[output['output_key']] = output['output_value']

    all_ips = outputs['all_ips']
    line_match = re.compile("^(?P<name>[^ ]*)\s*-\s*(?P<ip>.*)$")
    host_ips = {}
    for ip_str in all_ips.split('\n'):
        match = line_match.match(ip_str)
        if match:
            gd = match.groupdict()
            host_ips[gd['name']] = gd['ip']

    # files to write
    ssh_config_file = "ssh_config_{stack_name}".format(stack_name=name)
    private_key_file = "priv_key_{stack_name}".format(stack_name=name)

    # write private key to file
    private_key = outputs['private_key']
    with open(private_key_file, 'w') as f:
        f.write(private_key)
    os.chmod(private_key_file, 0o600)

    # Write out ssh config
    ssh_config_file_template = jinja2.Template("""
host *
    IdentityFile {{identityfile}}
    user {{user}}

{% for name, ip in hosts.items() %}
host {{name}}
    hostname {{ip}}
{% endfor %}
""")
    with open(ssh_config_file, 'w') as f:
        f.write(ssh_config_file_template.render(
            hosts=host_ips,
            user="root",
            identityfile=private_key_file
            ))
        print("Private Key: {pk}\nSSH Config: {sshc}\n"
              "Available hosts: {hosts}\n"
              "Example Command: ssh -F {sshc} infra1".format(
                  pk=private_key_file,
                  sshc=ssh_config_file,
                  hosts=host_ips.keys()))
    if connect:
        os.system("ssh -F {sshc} {host}".format(sshc=ssh_config_file,
                                                host=connect))


if __name__ == "__main__":
    cli()
