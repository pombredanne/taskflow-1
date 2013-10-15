# -*- coding: utf-8 -*-

# vim: tabstop=4 shiftwidth=4 softtabstop=4

#    Copyright (C) 2013 Yahoo! Inc. All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import contextlib
import hashlib
import logging
import os
import random
import sys
import time

logging.basicConfig(level=logging.ERROR)

top_dir = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                       os.pardir,
                                       os.pardir))
sys.path.insert(0, top_dir)

from taskflow.patterns import graph_flow as gf
from taskflow.patterns import linear_flow as lf

from taskflow import engines
from taskflow import exceptions as exc
from taskflow import task

from taskflow.persistence import backends
from taskflow.utils import eventlet_utils as e_utils
from taskflow.utils import persistence_utils as p_utils


@contextlib.contextmanager
def slow_down(how_long=0.5):
    try:
        yield how_long
    finally:
        if len(sys.argv) > 1:
            # Only both to do this if user input provided.
            print("** Ctrl-c me please!!! **")
            time.sleep(how_long)


def print_wrapped(text):
    print("-" * (len(text)))
    print(text)
    print("-" * (len(text)))


def get_backend():
    try:
        backend_uri = sys.argv[1]
    except Exception:
        backend_uri = 'sqlite://'

    backend = backends.fetch({'connection': backend_uri})
    backend.get_connection().upgrade()
    return backend


class PrintText(task.Task):
    def __init__(self, print_what, no_slow=False):
        content_hash = hashlib.md5(print_what).hexdigest()[0:8]
        super(PrintText, self).__init__(name="Print: %s" % (content_hash))
        self._text = print_what
        self._no_slow = no_slow

    def execute(self):
        if self._no_slow:
            print_wrapped(self._text)
        else:
            with slow_down():
                print_wrapped(self._text)


class DefineVMSpec(task.Task):
    def __init__(self, name):
        super(DefineVMSpec, self).__init__(provides='vm_spec',
                                           name=name)

    def execute(self):
        return {
            'type': 'kvm',
            'disks': 2,
            'vcpu': 1,
            'ips': 1,
            'volumes': 3,
        }


class LocateImages(task.Task):
    def __init__(self, name):
        super(LocateImages, self).__init__(provides='image_locations',
                                           name=name)

    def execute(self, vm_spec):
        image_locations = {}
        for i in range(0, vm_spec['disks']):
            url = "http://www.yahoo.com/images/%s" % (i)
            image_locations[url] = "/tmp/%s.img" % (i)
        return image_locations


class DownloadImages(task.Task):
    def __init__(self, name):
        super(DownloadImages, self).__init__(provides='download_paths',
                                             name=name)

    def execute(self, image_locations):
        for src, loc in image_locations.items():
            with slow_down(1):
                print("Downloading from %s => %s" % (src, loc))
        return sorted(image_locations.values())


class FetchNetworkSettings(task.Task):
    SYSCONFIG_CONTENTS = """DEVICE=eth%s
BOOTPROTO=static
IPADDR=%s
ONBOOT=yes"""

    def __init__(self, name):
        super(FetchNetworkSettings, self).__init__(provides='network_settings',
                                                   name=name)

    def execute(self, ips):
        settings = []
        for i, ip in enumerate(ips):
            settings.append(self.SYSCONFIG_CONTENTS % (i, ip))
        return settings


class AllocateIP(task.Task):
    def __init__(self, name):
        super(AllocateIP, self).__init__(provides='ips', name=name)

    def execute(self, vm_spec):
        ips = []
        for i in range(0, vm_spec.get('ips', 0)):
            ips.append("192.168.0.%s" % (random.randint(1, 254)))
        return ips


class WriteNetworkSettings(task.Task):
    def execute(self, download_paths, network_settings):
        for j, path in enumerate(download_paths):
            with slow_down(1):
                print("Mounting %s to /tmp/%s" % (path, j))
            for i, setting in enumerate(network_settings):
                filename = ("/tmp/etc/sysconfig/network-scripts/"
                            "ifcfg-eth%s" % (i))
                with slow_down(1):
                    print("Writing to %s" % (filename))
                    print(setting)


class BootVM(task.Task):
    def execute(self, vm_spec):
        print("Starting vm!")
        with slow_down(1):
            print("Created: %s" % (vm_spec))


class AllocateVolumes(task.Task):
    def execute(self, vm_spec):
        volumes = []
        for i in range(0, vm_spec['volumes']):
            with slow_down(1):
                volumes.append("/dev/vda%s" % (i + 1))
                print("Allocated volume %s" % volumes[-1])
        return volumes


class FormatVolumes(task.Task):
    def execute(self, volumes):
        for v in volumes:
            print("Formatting volume %s" % v)
            with slow_down(1):
                pass
            print("Formatted volume %s" % v)


def create_flow():
    # Setup the set of things to do (mini-nova).
    flow = lf.Flow("root").add(
        PrintText("Starting vm creation.", no_slow=True),
        lf.Flow('vm-maker').add(
            # First create a specification for the final vm to-be.
            DefineVMSpec("define_spec"),
            # This does all the image stuff.
            gf.Flow("img-maker").add(
                LocateImages("locate_images"),
                DownloadImages("download_images"),
            ),
            # This does all the network stuff.
            gf.Flow("net-maker").add(
                AllocateIP("get_my_ips"),
                FetchNetworkSettings("fetch_net_settings"),
                WriteNetworkSettings("write_net_settings"),
            ),
            # This does all the volume stuff.
            gf.Flow("volume-maker").add(
                AllocateVolumes("allocate_my_volumes", provides='volumes'),
                FormatVolumes("volume_formatter"),
            ),
            # Finally boot it all.
            BootVM("boot-it"),
        ),
        PrintText("Finished vm create.", no_slow=True),
        PrintText("Instance is running!", no_slow=True))
    return flow

print_wrapped("Initializing")

# Setup the persistence & resumption layer.
backend = get_backend()
try:
    book_id, flow_id = sys.argv[2].split("+", 1)
except (IndexError, ValueError):
    book_id = None
    flow_id = None

# Set up how we want our engine to run, serial, parallel...
engine_conf = {
    'engine': 'parallel',
    'executor': e_utils.GreenExecutor(5),
}

# Create/fetch a logbook that will track the workflows work.
book = None
flow_detail = None
if all([book_id, flow_id]):
    with contextlib.closing(backend.get_connection()) as conn:
        try:
            book = conn.get_logbook(book_id)
            flow_detail = book.find(flow_id)
        except exc.NotFound:
            pass
if book is None and flow_detail is None:
    book = p_utils.temporary_log_book(backend)
    engine = engines.load_from_factory(create_flow,
                                       backend=backend, book=book,
                                       engine_conf=engine_conf)
    print("!! Your tracking id is: '%s+%s'" % (book.uuid,
                                               engine.storage.flow_uuid))
    print("!! Please submit this on later runs for tracking purposes")
else:
    engine = engines.load_from_detail(flow_detail,
                                      backend=backend, engine_conf=engine_conf)

print_wrapped('Running')
engine.run()

# How to use.
#
# 1. $ python me.py "sqlite:////tmp/nova.db"
# 2. ctrl-c before this finishes
# 3. Find the tracking id (search for 'Your tracking id is')
# 4. $ python me.py "sqlite:////tmp/cinder.db" "$tracking_id"
# 5. Watch it pick up where it left off.
# 6. Profit!
