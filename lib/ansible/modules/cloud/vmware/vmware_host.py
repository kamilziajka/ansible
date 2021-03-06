#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2015, Joseph Callen <jcallen () csc.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type


ANSIBLE_METADATA = {'metadata_version': '1.0',
                    'status': ['preview'],
                    'supported_by': 'community'}

DOCUMENTATION = r'''
---
module: vmware_host
short_description: Add/remove ESXi host to/from vCenter
description:
- This module can be used to add/remove an ESXi host to/from vCenter.
version_added: '2.0'
author:
- Joseph Callen (@jcpowermac)
- Russell Teague (@mtnbikenc)
notes:
- Tested on vSphere 5.5
requirements:
- python >= 2.6
- PyVmomi
options:
  datacenter_name:
    description:
    - Name of the datacenter to add the host.
    required: yes
  cluster_name:
    description:
    - Name of the cluster to add the host.
    required: yes
  esxi_hostname:
    description:
    - ESXi hostname to manage.
    required: yes
  esxi_username:
    description:
    - ESXi username.
    required: yes
  esxi_password:
    description:
    - ESXi password.
    required: yes
  state:
    description:
    - Add or remove the host.
    choices: [absent, present]
    default: present
extends_documentation_fragment: vmware.documentation
'''

EXAMPLES = r'''
- name: Add ESXi Host to vCenter
  vmware_host:
    hostname: '{{ vcenter_hostname }}'
    username: '{{ vcenter_username }}'
    password: '{{ vcenter_password }}'
    datacenter_name: datacenter_name
    cluster_name: cluster_name
    esxi_hostname: '{{ esxi_hostname }}'
    esxi_username: '{{ esxi_username }}'
    esxi_password: '{{ esxi_password }}'
    state: present
  delegate_to: localhost
'''

RETURN = r'''
'''

try:
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.vmware import (
    TaskError,
    connect_to_api,
    find_cluster_by_name,
    find_datacenter_by_name,
    vmware_argument_spec,
    wait_for_task,
)


class VMwareHost(object):
    def __init__(self, module):
        self.module = module
        self.datacenter_name = module.params['datacenter_name']
        self.cluster_name = module.params['cluster_name']
        self.esxi_hostname = module.params['esxi_hostname']
        self.esxi_username = module.params['esxi_username']
        self.esxi_password = module.params['esxi_password']
        self.state = module.params['state']
        self.dc = None
        self.cluster = None
        self.host = None
        self.content = connect_to_api(module)

    def process_state(self):
        try:
            # Currently state_update_dvs is not implemented.
            host_states = {
                'absent': {
                    'present': self.state_remove_host,
                    'absent': self.state_exit_unchanged,
                },
                'present': {
                    'present': self.state_exit_unchanged,
                    'absent': self.state_add_host,
                }
            }

            host_states[self.state][self.check_host_state()]()

        except vmodl.RuntimeFault as runtime_fault:
            self.module.fail_json(msg=runtime_fault.msg)
        except vmodl.MethodFault as method_fault:
            self.module.fail_json(msg=method_fault.msg)
        except Exception as e:
            self.module.fail_json(msg=str(e))

    def find_host_by_cluster_datacenter(self):
        self.dc = find_datacenter_by_name(self.content, self.datacenter_name)
        self.cluster = find_cluster_by_name(self.content, self.cluster_name, self.dc)

        if self.cluster is None:
            self.module.fail_json(msg="Unable to find cluster %(cluster_name)s" % self.module.params)

        for host in self.cluster.host:
            if host.name == self.esxi_hostname:
                return host, self.cluster

        return None, self.cluster

    def add_host_to_vcenter(self):
        host_connect_spec = vim.host.ConnectSpec()
        host_connect_spec.hostName = self.esxi_hostname
        host_connect_spec.userName = self.esxi_username
        host_connect_spec.password = self.esxi_password
        host_connect_spec.force = True
        host_connect_spec.sslThumbprint = ""
        as_connected = True
        esxi_license = None
        resource_pool = None

        try:
            task = self.cluster.AddHost_Task(host_connect_spec, as_connected, resource_pool, esxi_license)
            success, result = wait_for_task(task)
            return success, result
        except TaskError as add_task_error:
            # This is almost certain to fail the first time.
            # In order to get the sslThumbprint we first connect
            # get the vim.fault.SSLVerifyFault then grab the sslThumbprint
            # from that object.
            #
            # args is a tuple, selecting the first tuple
            ssl_verify_fault = add_task_error.args[0]
            host_connect_spec.sslThumbprint = ssl_verify_fault.thumbprint

        task = self.cluster.AddHost_Task(host_connect_spec, as_connected, resource_pool, esxi_license)
        success, result = wait_for_task(task)
        return success, result

    def state_exit_unchanged(self):
        self.module.exit_json(changed=False)

    def state_remove_host(self):
        changed = True
        result = None
        if not self.module.check_mode:
            if not self.host.runtime.inMaintenanceMode:
                maintenance_mode_task = self.host.EnterMaintenanceMode_Task(300, True, None)
                changed, result = wait_for_task(maintenance_mode_task)

            if changed:
                task = self.host.Destroy_Task()
                changed, result = wait_for_task(task)
            else:
                raise Exception(result)
        self.module.exit_json(changed=changed, result=str(result))

    def state_update_host(self):
        self.module.exit_json(changed=False, msg="Currently not implemented.")

    def state_add_host(self):
        changed = True
        result = None

        if not self.module.check_mode:
            changed, result = self.add_host_to_vcenter()
        self.module.exit_json(changed=changed, result=str(result))

    def check_host_state(self):
        self.host, self.cluster = self.find_host_by_cluster_datacenter()

        if self.host is None:
            return 'absent'
        else:
            return 'present'


def main():
    argument_spec = vmware_argument_spec()
    argument_spec.update(
        datacenter_name=dict(type='str', required=True),
        cluster_name=dict(type='str'),
        esxi_hostname=dict(type='str', required=True),
        esxi_username=dict(type='str', required=True),
        esxi_password=dict(type='str', required=True, no_log=True),
        state=dict(type='str', default='present', choices=['absent', 'present'])
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    if not HAS_PYVMOMI:
        module.fail_json(msg='pyvmomi is required for this module')

    vmware_host = VMwareHost(module)
    vmware_host.process_state()


if __name__ == '__main__':
    main()
