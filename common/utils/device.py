from dcim.choices import InterfaceDuplexChoices, InterfaceModeChoices
from dcim.models import Manufacturer, Device, Interface, Platform, DeviceType, Site, Cable, DeviceRole
# from dcim.models.device_components import Interface
from extras.models import Tag
from ipam.models import IPAddress, VRF, VLAN
from ipaddress import IPv4Network
from django.utils.text import slugify
from re import match as re_match
from re import search as re_search
from copy import deepcopy
from manuf import manuf
from datetime import datetime
from common.utils.nso import Nso, UnsupportedInterfacefType, SkipInterfaceType, UnsupportedNedError
from django.core.exceptions import ValidationError
from json import dumps as json_dumps_
from sys import exc_info
from traceback import format_exc
from requests.exceptions import Timeout as TimeoutException


class UnsupportedDeviceTypeOnboardingError(Exception):
    pass


class NsoObjectNotFoundError(Exception):
    pass


class InterfaceNotMatchedError(Exception):
    pass

class InterfaceNotFoundOnNSOError(Exception):
    pass

class LLDPNeighborsListEmpty(Exception):
    pass


class NSODevicesRetrievalError(Exception):
    pass


class BannerNotCompliantError(Exception):
    pass


def split_interface_name(interface_name):
    match = re_match(r'([a-zA-Z\-]+)(\d.*)', interface_name)
    if match:
        return match.groups()

def deep_merge(dict1:dict, dict2:dict):
    for key, value in dict2.items():
        if isinstance(value, dict):
            dict1[key] = deep_merge(dict1.get(key, {}), value)
        else:
            dict1[key] = value
    return dict1

def ipmask_to_cidr(ip, mask):
    network = IPv4Network((ip, mask), strict=False)
    return f"{ip}/{network.prefixlen}"

def match_speed(speed:str):
    "matches nso speed values with netbox"
    speed_mapping = {
        '10 Mbps': 10000,
        '100 Mbps': 100000,
        '1 Gbps': 1000000,
        'ten-gbps': 10000000,
        '25 Gbps': 25000000,
        '40 Gbps': 40000000,
        '100 Gbps': 100000000,
        '200 Gbps': 200000000,
        '400 Gbps': 400000000,
    }
    return speed_mapping.get(speed)

def get_manufacturer_by_mac(mac_address):
    p = manuf.MacParser()
    return p.get_manuf(mac_address)


class DeviceManager:
    def __init__(self, nso:object=None, with_logs:bool=True, log=[]):
        self.nso = None
        if nso:
            ###########################################################################################
            self.nso = nso
            self.peers_not_onboarded_on_nso = []
            ###########################################################################################
            # placeholder device attributes if the device doesn't exist:
            self.default_nb_manuf = Manufacturer.objects.get(name="unknown")
            self.default_nb_device_type = DeviceType.objects.get(model="unknown", manufacturer=self.default_nb_manuf)
            self.default_nso_device_role = DeviceRole.objects.get(name="unknown")
            self.default_nb_site = Site.objects.get(name="unknown")


        self.with_logs = with_logs
        if log:
            self.log_info = log[0]
            self.log_warning = log[1]
            self.log_failure = log[2]
            self.log_debug = log[3]
        self.duplex_mapping = {value: label.lower() for value, label in InterfaceDuplexChoices.CHOICES}


        ###########################################################################################

    def get_or_create_csg_devices(self, limit_devices:list=[], limit:int=0, offset:int=0):
        if self.nso:
            # getting CSG devices from netbox
            nso_csg_devices, resp = self.nso.query(
                payload={
                    "tailf-rest-query:immediate-query": {
                        "foreach": "/nso-cfs/csg-provisionning:csg-provisionning/csg-two-ctr-schema:csg-two-ctr-schema",
                        "select": [
                            {
                                "label": "name",
                                "expression": "csg-device-id",
                                "result-type": "string"
                            }
                        ]
                    }
                }
            )
            if not nso_csg_devices:
                self.log_failure(
                    " |     status Code    |  query url |        payload        |  Full error log  |\n"
                    " | :----------------: | :--------: | :-------------------: | :--------------: |\n"
                    f"| {resp.status_code} | {resp.url} |  {resp.request.body}  |  '{resp.json()}' |\n"
                )
                raise NSODevicesRetrievalError(f"Failed to retrieve CSG devices from NSO")

            devices_list = []
            current_index = 0
            for device_entry in nso_csg_devices:
                if current_index < offset:  # we're within the offset, skip this iteration
                    current_index += 1      # increment the index for the next loop
                    continue
                device_attributes = list(device_entry.values())[0]
                for attribute in device_attributes:
                    if attribute["label"] == "name":
                        if limit_devices:
                            if attribute["value"] in limit_devices:
                                devices_list.append(attribute["value"])
                            continue
                        else:
                            devices_list.append(attribute["value"])
                if len(devices_list) >= limit:
                    if limit:
                        break

            self.log_info(f"onboarding: '{len(devices_list)}' CSG devices on Netbox from NSO.") if self.with_logs else None

            existing_devices = set(Device.objects.filter(name__in=devices_list))
            missing_devices = set(devices_list) - set(Device.objects.filter(name__in=devices_list).values_list('name', flat=True))

            missing_device_objects = []
            if missing_devices:
                self.log_warning(f"The following devices were not found on Netbox: '{' '.join(missing_devices)}'")
                for device_name in missing_devices:
                    self.log_info(f"Creating missing device: '{device_name}' on Netbox with placeholder attributes") if self.with_logs else None
                    missing_device_objects.append(
                        Device(
                            name=device_name,
                            device_type=self.default_nb_device_type,
                            device_role=self.default_nso_device_role,
                            site_id=self.default_nb_site.id
                        )
                    )
                missing_device_objects = list(Device.objects.bulk_create(missing_device_objects))
                nb_devices = list(existing_devices) + missing_device_objects
            else:
                self.log_info("All devices were found on Netbox.") if self.with_logs else None
                nb_devices = existing_devices
        else:
            nb_devices = list(Device.objects.filter(name__in=limit_devices)) if limit_devices else list(Device.objects.filter(name__startswith="CSG")[offset: offset+limit])
        return nb_devices

    def update_device_tags(self, device, tag_name):
        tag, created = Tag.objects.get_or_create(name=tag_name)
        if created:
            self.log_info(f"created tag: '{tag.name}' on Netbox") if self.with_logs else None
        self.log_info(f"Updating device: '{device.name}' with tags: '{tag_name}'") if self.with_logs else None
        device.tags.add(tag)

    def get_device_banner(self, device):
        self.log_info(f"Getting device: '{device.name}' banner from NSO.'") if self.with_logs else None
        banner, resp = self.nso.get_device_config(device=device.name, attribute="banner")
        return banner

    def update_device_site(self, device):
        banner = self.get_device_banner(device)
        nso_parsed_site_name = banner["exec"]["message"]
        if nso_parsed_site_name:
            site_name_match = re_search(r'site\s(\S+)', nso_parsed_site_name)

            if site_name_match:
                site_name = site_name_match.group(1)
                if site_name.casefold() != "none":
                    self.log_info(f"site parser matched: '{site_name}' from banner message") if self.with_logs else None
                    nb_site, created = Site.objects.get_or_create(name=site_name, slug=slugify(site_name))
                    if created:
                        self.log_info(f"created site: '{nb_site.name}' on Netbox from NSO banner parsing.") if self.with_logs else None
                    device.site = nb_site
            else:
                raise BannerNotCompliantError(f"exec banner is not compliant with pattern - found: '{nso_parsed_site_name}'")
        else:
            raise BannerNotCompliantError(f"has no exec banner message")

    def get_device_type(self, device):
        self.log_info(f"getting device type from NSO for device: '{device.name}'.'") if self.with_logs else None
        nso_device_type, resp = self.nso.get_device(device=device.name, attribute="device-type")
        try:
            return nso_device_type["cli"]["ned-id"]["#text"]
        except KeyError:
            raise UnsupportedDeviceTypeOnboardingError(f"device onboarding for: '{device.name}' from NSO to netbox is not supported for ned: '{nso_device_type}'")

    def update_device_manufacturer(self, device):
        nso_device_type = self.get_device_type(device)
        if "cisco" in nso_device_type.casefold():
            nb_manufacturer = Manufacturer.objects.get(name="Cisco")
        else:
            raise UnsupportedDeviceTypeOnboardingError(f"device onboarding from NSO to netbox is not supported for ned: '{nso_device_type}'")
        self.log_info(f"getting/updating device model from NSO.'") if self.with_logs else None
        nso_device_platform, resp = self.nso.get_device(device=device.name, attribute="platform")
        if nso_device_platform:
            if device.device_type.model == nso_device_platform["model"]:
                self.log_info(f"device model is compliant with NSO: '{nso_device_platform['model']}'") if self.with_logs else None
            else:
                self.log_info(f"updating device: '{device.name}' model: '{device.device_type.model}' to corresponding NSO: '{nso_device_platform['model']}' model") if self.with_logs else None
                device.device_type, created = DeviceType.objects.get_or_create(
                    model=nso_device_platform['model'],
                    manufacturer=nb_manufacturer
                )
                if created:
                    self.log_info(f"created device model: '{nso_device_platform['model']}' for manufacturer: '{nb_manufacturer}' on Netbox") if self.with_logs else None
        else:
            raise NsoObjectNotFoundError(f"device: '{device.name}' platform couldn't be retrieved from NSO: {resp.text}")
        return nso_device_platform

    def update_device_platform(self, device, device_platform):
        self.log_info(f"updating device: '{device.name}' platform  on Netbox") if self.with_logs else None
        platform, created = Platform.objects.get_or_create(
            slug=slugify(device_platform["name"].lower())
        )
        if created:
            self.log_info(f"Created new platform: '{device_platform['name']}' on netbox from NSO") if self.with_logs else None
        else:
            platform.snapshot()
        platform.name = device_platform["name"]
        platform.slug = slugify(device_platform["name"])
        platform.manufacturer = device.device_type.manufacturer

        platform.full_clean()

        platform.save()
        device.platform = platform

    def update_device_os_version(self, device, device_platform):
        self.log_info(f"updating device: '{device.name}' OS version: '{device_platform['version']}' on netbox from NSO") if self.with_logs else None
        if device.local_context_data:
            device.local_context_data.update({"os_version": device_platform["version"]})
        else:
            device.local_context_data = {"os_version": device_platform["version"]}

    def update_device_serial_number(self, device, device_platform):
        self.log_info(f"updating device: '{device.name}' serial-number: '{device_platform['serial-number']}' on netbox from NSO") if self.with_logs else None
        device.serial = device_platform["serial-number"]

    def get_device_interface_data(self, device, retry:int, timeout:int):
        #######################################################################################
        self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started retrieving device: '{device.name}' interfaces configuration from NSO.") if self.with_logs else None
        nso_local_device_interf_config, resp = self.nso.get_device_config(
            device=device.name,
            attribute="interface",
        )
        self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished retrieving device: '{device.name}' interfaces configuration from NSO.") if self.with_logs else None
        if not nso_local_device_interf_config:
            self.log_warning(f"nso_local_device_interf_config is empty for device: '{device.name}' url: '{resp.url}'") if self.with_logs else None
        #######################################################################################
        self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started retrieving device: '{device.name}' interfaces oper-status from NSO.") if self.with_logs else None
        nso_local_device_interf_properties, resp = self.nso.get_device_live_status(
            device=device.name,
            path="Cisco-IOS-XR-ifmgr-oper:interface-properties/data-nodes",
            retry=retry,
            timeout=timeout,
        )
        self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished retrieving device: '{device.name}' interfaces oper-status from NSO.") if self.with_logs else None
        nso_interface_properties = {}

        if nso_local_device_interf_properties:
            nso_local_device_interf_properties = nso_local_device_interf_properties.get('data-node', [])
            if nso_local_device_interf_properties:
                nso_local_device_interf_properties = nso_local_device_interf_properties[0].get("system-view", {}).get('interfaces', {}).get('interface', {})
            for interface in nso_local_device_interf_properties:
                # nso_interface_properties.update({interface.pop('interface-name'): interface})
                nso_interface_properties.setdefault(interface.pop('interface-name'), {'properties': {}, 'state': {}})['properties'] = interface

        else:
            self.log_warning(f"nso_local_device_interf_properties for device: '{device.name}' was empty.")
            # TODO:
            # nso.match_interface_type must match interface by name pattern...
            # must rely on state instead of properties when properties is not empty...
            # check if possible to replace with query, as interface-state takes too long to process.
            #
            # raise InterfaceNotFoundOnNSOError("nso_local_device_interf_properties was empty for device.")
            #######################################################################################
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started retrieving device: '{device.name}' interfaces-state from NSO.") if self.with_logs else None
            nso_local_device_interfaces_state, resp = self.nso.get_device_live_status(
                device=device.name,
                path="ietf-interfaces:interfaces-state",
                retry=retry,
                timeout=timeout,
            )
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished retrieving device: '{device.name}' interfaces state from NSO.") if self.with_logs else None
            if not nso_local_device_interfaces_state:
                self.log_warning(f"nso_local_device_interfaces_state is empty for device: '{device.name}' url: '{resp.url}'") if self.with_logs else None
            else:
                nso_local_device_interfaces_state = nso_local_device_interfaces_state['interface']
                for interface in nso_local_device_interfaces_state:
                    nso_interface_properties.setdefault(interface.pop('name'), {'properties': {}, 'state': {}})['state'] = interface
            #######################################################################################
        return nso_local_device_interf_config, nso_interface_properties

    def get_or_create_device_interfaces(self, device, nso_interface_properties, nso_interf_config):
        # Query existing interfaces for the provided device
        interface_names = list(nso_interface_properties.keys())
        self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Retrieved '{len(interface_names)}' interfaces for device: '{device.name}' from NSO") if self.with_logs else None

        existing_interface = list(Interface.objects.filter(device=device, name__in=interface_names))
        self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Retrieved '{len(existing_interface)}' interfaces for device: '{device.name}' from Netbox") if self.with_logs else None

        # Find missing interfaces using set difference
        missing_interface_names = set(interface_names) - set(Interface.objects.filter(device=device, name__in=interface_names).values_list('name', flat=True))

        matched_interfaces = {}
        for interface in existing_interface:
            #####################################################################################
            try:
                nb_interface_type, nso_interface_type = self.nso.match_interface_type(nso_interface_properties[interface.name]['properties']['type'])
            except SkipInterfaceType as e:
                self.log_debug(f"{datetime.now().strftime('%H:%M:%S')} - can't create interface: {interface.name} on device: '{device.name}' {e}") if self.with_logs else None
                continue
            except UnsupportedInterfacefType as e:
                self.log_debug(f"{datetime.now().strftime('%H:%M:%S')} - can't create interface: {interface.name} on device: '{device.name}' {e}") if self.with_logs else None
                continue
            #####################################################################################
            try:
                matched_interfaces[interface.name] = self.match_interface(device, interface.name, nso_interf_config, nso_interface_type)
            except InterfaceNotMatchedError as e:
                self.log_debug(f"{e}") if self.with_logs else None
                continue

        missing_interfaces_list = []
        for interface_name in missing_interface_names:
            interface_properties = nso_interface_properties[interface_name]['properties']
            #####################################################################################
            try:
                nb_interface_type, nso_interface_type = self.nso.match_interface_type(interface_properties['type'])
            except SkipInterfaceType as e:
                self.log_debug(f"{datetime.now().strftime('%H:%M:%S')} - can't create interface: {interface_name} on device: '{device.name}' {e}") if self.with_logs else None
                continue
            except UnsupportedInterfacefType as e:
                self.log_debug(f"{datetime.now().strftime('%H:%M:%S')} - can't create interface: {interface_name} on device: '{device.name}' {e}") if self.with_logs else None
                continue
            #####################################################################################
            try:
                matched_interfaces[interface_name] = self.match_interface(device, interface_name, nso_interf_config, nso_interface_type)
            except InterfaceNotMatchedError as e:
                self.log_debug(f"{e}") if self.with_logs else None
                continue
            #####################################################################################
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Creating missing interface: '{interface_name}' for device: '{device.name}' on Netbox") if self.with_logs else None

            missing_interfaces_list.append(
                Interface(
                    device=device,
                    name=interface_name,
                    type=nb_interface_type,
                    description=matched_interfaces[interface_name].pop("description", ""),
                    enabled=True if "up" in interface_properties['state'].casefold() else False,
                    mtu=interface_properties['mtu'],
                )
            )
        if missing_interfaces_list:
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started creating missing: '{len(missing_interfaces_list)}' interfaces for device: '{device.name}' on Netbox") if self.with_logs else None
            missing_interfaces = list(Interface.objects.bulk_create(missing_interfaces_list))
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished creating missing: '{len(missing_interfaces_list)}' interfaces for device: '{device.name}' on Netbox") if self.with_logs else None


        all_interfaces = list(existing_interface) + missing_interfaces

        self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - total is: '{len(all_interfaces)}' interfaces for device: '{device.name}' on Netbox") if self.with_logs else None
        return all_interfaces, matched_interfaces

    def update_interface_macaddress(self, device, interface, retry:int, timeout:int):
        formatted_inter_name = interface.name.replace('/', '%2F')
        self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started Getting mac address for device: '{device.name}' interface: '{interface.name}' from NSO.") if self.with_logs else None
        mac_address, resp = self.nso.get_device_live_status(
            device=device.name,
            path=f"Cisco-IOS-XR-drivers-media-eth-oper:ethernet-interface/interfaces/interface={formatted_inter_name}/mac-info/operational-mac-address",   # alt: burned-in-mac-address"
            retry=retry,
            timeout=timeout,
        )
        self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished Getting mac address for device: '{device.name}' interface: '{interface.name}' from NSO.") if self.with_logs else None
        if mac_address:
            self.log_info(f"updating device: '{device.name}' interface: '{interface.name}' with mac address: '{mac_address}' on on Netbox.") if self.with_logs else None
            interface.mac_address = mac_address

    def match_interface(self, device, interface_name, nso_interface_config, nso_interface_type):
        re_interface_type_, interface_id = split_interface_name(interface_name)
        matched_interface = {}
        found = False
        if isinstance(nso_interface_type, str):
            nso_interface_configs_per_type = nso_interface_config.get(nso_interface_type)
            if not nso_interface_configs_per_type:
                raise InterfaceNotMatchedError(f"Failed to match interface: '{interface_name}' of type: '{nso_interface_type}' for device: '{device.name}' - possible values:\n {json_dumps_(nso_interface_config, indent=4)}")

            for interface in nso_interface_config[nso_interface_type]:
                if str(interface["id"]) == str(interface_id):
                    found = True
                    matched_interface = deepcopy(interface)
                    matched_interface.pop("id")
                    matched_interface.pop("mtu", None)
                    matched_interface.pop("shutdown", None)
                    break
        elif isinstance(nso_interface_type, list):
            for current_nso_interface_type in nso_interface_type:
                nso_interface_configs_per_type = nso_interface_config.get(current_nso_interface_type)
                if not nso_interface_configs_per_type:
                    raise InterfaceNotMatchedError(f"Failed to match interface: '{interface_name}' of type: '{current_nso_interface_type}' for device: '{device.name}' - possible values:\n {json_dumps_(nso_interface_config, indent=4)}")

                for interface_type_name, interfaces in nso_interface_configs_per_type.items():
                    for interface in interfaces:
                        if str(interface["id"]) == str(interface_id):
                            found = True
                            matched_interface = deepcopy(interface)
                            matched_interface.pop("id")
                            matched_interface.pop("mtu", None)
                            matched_interface.pop("shutdown", None)
                            break
                    if found:
                        break
                if found:
                    break
        if not found:
            raise InterfaceNotMatchedError(f"SKIPPING - device: '{device.name}' interface: '{interface_name}' was not matched! interface_type: '{re_interface_type_}' id: '{interface_id}'")
        return matched_interface

    def update_interface_vrf(self, device, nb_interface, matched_interface):
        inter_vrf = matched_interface.pop("vrf", None)
        if inter_vrf:
            self.log_debug(f"vrf: {inter_vrf}") if self.with_logs else None
            nb_vrf, created = VRF.objects.get_or_create(name=inter_vrf)
            if created:
                self.log_info(f"Created vrf: {inter_vrf} on Netbox") if self.with_logs else None
            self.log_info(f"updating vrf: {inter_vrf} to device: '{device.name}' current interface: '{nb_interface.name}'") if self.with_logs else None
            nb_interface.vrf = nb_vrf

    def update_interface_bundle(self, device, nb_interface, matched_interface):
        bundle_id = matched_interface['bundle']['id'].pop('id-value')
        matched_interface['bundle'] = matched_interface['bundle'].pop('id')
        bundle_inter_name = f"Bundle-Ether{bundle_id}"
        bundle_inter, created = Interface.objects.get_or_create(name=bundle_inter_name, device=device)
        if created:
            self.log_info(f"created bundle interface: '{bundle_inter_name}'") if self.with_logs else None
        self.log_info(f"updating device: '{device.name}' interface: '{nb_interface.name}' with lag-bundle: '{bundle_inter_name}'") if self.with_logs else None
        nb_interface.lag = bundle_inter

    def update_interface_address(self, afi, device, nb_interface, matched_interface):
        address = matched_interface.get(afi, {}).get("address", {}).get('ip')
        mask = matched_interface.get(afi, {}).get("address", {}).get('mask')
        # sometimes, address and mask are not set and instead we get eg: 'ipv6: {'enable': None}
        if address and mask:
            address_cidr = ipmask_to_cidr(
                address,
                mask
            )
            ip_address, created = IPAddress.objects.get_or_create(
                address=address_cidr,
                vrf=nb_interface.vrf
            )
            if created:
                self.log_info(f"created '{afi}' ip_address: '{address_cidr}'") if self.with_logs else None
            else:
                ip_address.snapshot()
            self.log_info(f"Assigning '{afi}' ip_address: '{address_cidr}' to device: '{device.name}' interface: '{nb_interface.name}'") if self.with_logs else None
            ip_address.assigned_object = nb_interface
            ip_address.full_clean()
            ip_address.save()
        matched_interface.pop(afi)

    def create_device_connections(self, device, retry:int, timeout:int):
        def get_nso_peer_device(peer_device_name):
            nso_peer_device_exists, resp = self.nso.get_device(device=peer_device_name, attribute="name")
            if not nso_peer_device_exists:
                if peer_device_name not in self.peers_not_onboarded_on_nso:
                    self.peers_not_onboarded_on_nso.append(peer_device_name)
                self.log_warning(f"peer device: '{peer_device_name}' is not onboarded on NSO") if self.with_logs else None
            return nso_peer_device_exists

        def get_peer_device(peer_device_name, peer_mac_address):
            try:
                nb_peer_manuf_name = get_manufacturer_by_mac(peer_mac_address)
            except ValueError as e:
                self.log_warning(f"failed to match peer device: '{peer_device_name}' manufacturer: '{e}'")
                nb_peer_manuf_name = "unknown"

            if not nb_peer_manuf_name:
                self.log_warning(f"peer device: '{peer_device_name} manufacturer was empty: '{nb_peer_manuf_name}'")
                nb_peer_manuf_name = "unknown"

            nb_peer_manuf, created = Manufacturer.objects.get_or_create(
                name=nb_peer_manuf_name,
                slug=slugify(nb_peer_manuf_name)
            )

            if created:
                self.log_warning(f"created a new manufacturer: '{nb_peer_manuf_name}' for peer device: '{peer_device_name}' on Netbox.") if self.with_logs else None

            nb_peer_device_type, created = DeviceType.objects.get_or_create(
                model='unknown',
                manufacturer=nb_peer_manuf,
                slug="unknown" if nb_peer_manuf.name == "unknown" else f"unknown-{nb_peer_manuf.name}"
            )

            peer_device, created = Device.objects.get_or_create(
                name=peer_device_name,
                device_type=nb_peer_device_type,
                role_id=self.default_nso_device_role.id,
                site_id=self.default_nb_site.id,
            )
            if created:
                self.log_info(f"created peer-device: '{peer_device.name}' on netbox") if self.with_logs else None
            else:
                peer_device.snapshot()

            peer_device.device_type.manufacturer = nb_peer_manuf
            peer_device.full_clean()
            peer_device.save()
            return peer_device

        def get_peer_device_interface(peer_device, peer_interface_name):
            peer_interface, created = Interface.objects.get_or_create(
                name=peer_interface_name,
                device=peer_device,
            )
            if created:
                self.log_info(f"created interface: '{peer_interface.name}' for peer-device: '{peer_device.name}' on netbox") if self.with_logs else None
            else:
                peer_interface.snapshot()
            return peer_interface

        def update_speed_duplex(peer_device, peer_interface, retry:int, timeout:int):
            formatted_inter_name = peer_interface.name.replace('/', '%2F')
            peer_inter_speed_duplex, resp = self.nso.get_device_live_status(
                device=peer_device.name,
                path=f"Cisco-IOS-XR-drivers-media-eth-oper:ethernet-interface/interfaces/interface={formatted_inter_name}/layer1-info?fields=speed;duplex",
                retry=retry,
                timeout=timeout,
            )
            if peer_inter_speed_duplex:
                self.log_info(f"Setting speed: '{peer_inter_speed_duplex['speed']}' for peer interface: '{peer_interface.name}' on peer device: '{peer_device.name}'") if self.with_logs else None
                peer_interface.speed = match_speed(peer_inter_speed_duplex['speed'])
                duplex_key = peer_inter_speed_duplex['duplex'].split("-")[0]
                if duplex_key in self.duplex_mapping:
                    self.log_info(f"Setting duplex: '{self.duplex_mapping[duplex_key]}' for peer interface: '{peer_interface.name}' on peer device: '{peer_device.name}'") if self.with_logs else None
                    peer_interface.duplex = self.duplex_mapping[duplex_key]
                else:
                    self.log_failure(f"duplex key: '{duplex_key}' is currently not supported.")
            else:
                self.log_warning(
                    f"couldn't get peer interface: '{peer_interface.name}' for peer device: '{peer_device.name}' - "
                    f"resp status code:  '{resp.status_code}' - "
                    f"resp text: '{resp.text}'"
                    f"resp text: '{resp.url}'"
                )

        def create_cable_connection(local_interface, peer_interface, peer_device):
            create_cable = True
            delete_cable = False
            # TODO: sometimes cables stays connected on peer, lust be deleted_
            if local_interface.cable or peer_interface.cable:
                if local_interface.cable:
                    self.log_info(f"Current local interface is already connected") if self.with_logs else None
                if peer_interface.cable:
                    self.log_info(f"Current peer interface is already connected") if self.with_logs else None
                self.log_info(f"ensuring local interface connection is connected to the current peer...") if self.with_logs else None
                if len(local_interface.link_peers) > 1:
                    self.log_warning(f"Found: '{len(local_interface.link_peers)}' connections for local interface: '{local_interface.name}', expected 1.")
                if peer_interface in local_interface.link_peers:
                    self.log_info(f"connection cable: '{local_interface.cable.id}' is compliant.")if self.with_logs else None
                    create_cable = False
                else:
                    self.log_warning(f"connection from local device: '{local_interface.device.name}' between interface: '{local_interface.name}' and peer device: '{peer_device.name}' interface: '{peer_interface.name}' is not compliant.")
                    delete_cable = True

            if delete_cable:
                try:
                    peer_interface.cable.delete()
                    self.log_info(f"deleted cable connection for peer interface") if self.with_logs else None
                except AttributeError:
                    pass
                try:
                    local_interface.cable.delete()
                    self.log_info(f"deleted cable connection for local interface") if self.with_logs else None
                except AttributeError:
                    pass
                self.log_info(f"setting to None cable connection for peer interface") if self.with_logs else None
                peer_interface.cable = None
                self.log_info(f"setting to None cable connection for local interface") if self.with_logs else None
                local_interface.cable = None

            if create_cable:
                self.log_info(f"Creating connection between local interface: '{local_interface.name}' and peer device: '{peer_device.name}' interface: '{peer_interface.name}'.") if self.with_logs else None
                cable = Cable.objects.create(
                    a_terminations=[local_interface],
                    b_terminations=[peer_interface],
                )
                cable.full_clean()
                cable.save()

        def update_peer_device_interface(device, lldp_peering_data, peer_device, peer_interface, nso_peer_device_exists, retry:int, timeout:int):
            if peer_interface.name.startswith("Bundle"):
                local_interface_name = lldp_peering_data['parent-interface']
                # peer_interfaces['bundle'] = peer_interface
                try:
                    local_interface = Interface.objects.get(
                        name=local_interface_name,
                        device=device
                    )
                except Interface.DoesNotExist:
                    self.log_warning(f"local device: '{device.name}' interface: '{local_interface_name}' was not found, skipping interconnection.")
                    return
                peer_interface.enabled = local_interface.enabled
                peer_interface.mtu = local_interface.mtu
                peer_interface.type = local_interface.type
            else:
                local_interface_name = lldp_peering_data['local-interface']
                # peer_interfaces['interfaces'].append(peer_interface)
                if nso_peer_device_exists:
                    update_speed_duplex(peer_device, peer_interface, retry=retry, timeout=timeout)

                try:
                    local_interface = Interface.objects.get(
                        name=local_interface_name,
                        device=device
                    )
                except Interface.DoesNotExist:
                    self.log_warning(f"local device: '{device.name}' interface: '{local_interface_name}' was not found, skipping interconnection.")
                    return
                self.log_info(f"Updating peer interface with local interface state (enabled, mtu, type)") if self.with_logs else None
                peer_interface.enabled = local_interface.enabled
                peer_interface.mtu = local_interface.mtu
                peer_interface.type = local_interface.type
                create_cable_connection(local_interface, peer_interface, peer_device)
            peer_interface.full_clean()
            peer_interface.save()

        def create_peer_lags(peer_interfaces, retry:int, timeout:int):
            for lag_member_inter in peer_interfaces['interfaces']:
                lag_member_inter.snapshot()
                self.log_info(f"creating lag relation for peer device between peer device: '{lag_member_inter.device.name}' interface: '{lag_member_inter.name}' and lag: '{peer_interfaces['bundle'].name}'") if self.with_logs else None
                lag_member_inter.lag = peer_interfaces['bundle']
                lag_member_inter.full_clean()
                lag_member_inter.save()
        self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started getting lldp neibhors for device: '{device.name}' from NSO.") if self.with_logs else None
        device_lldp_neighbors, resp = self.nso.get_device_live_status(
            device=device.name,
            path="tailf-ned-cisco-ios-xr-stats:lldp",
            retry=retry,
            timeout=timeout
        )
        self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished getting lldp neibhors for device: '{device.name}' from NSO.") if self.with_logs else None

        # self.log_debug(f"device: '{device.name}' resp.url: '{resp.url}' lldp_neighbors:\n{json_dumps_(device_lldp_neighbors, indent=4)}")
        device_lldp_neighbors = device_lldp_neighbors.get('neighbors', [])
        if not device_lldp_neighbors:
            raise LLDPNeighborsListEmpty(f"LLDP is either not enabled/configured or NSO internal error for device: '{device.name}'...")

        # peer_interfaces = {"bundle": None, "interfaces": []}
        for lldp_peering_data in device_lldp_neighbors:
            peer_device_name = lldp_peering_data['device-id']
            peer_device_interface_name = lldp_peering_data['port-id']

            nso_peer_device_exists = get_nso_peer_device(peer_device_name=peer_device_name)

            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started getting/creating lldp peer_device: '{peer_device_name}' on Netbox.") if self.with_logs else None
            peer_device = get_peer_device(
                peer_device_name,
                lldp_peering_data['chassis-id']
            )
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished getting/creating lldp peer_device: '{peer_device_name}' on Netbox.") if self.with_logs else None

            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started getting/creating lldp peer_device: '{peer_device_name}' interface: '{peer_device_interface_name}' on Netbox.") if self.with_logs else None
            peer_interface = get_peer_device_interface(
                peer_device,
                peer_device_interface_name
            )
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished getting/creating lldp peer_device: '{peer_device_name}' interface: '{peer_device_interface_name}' on Netbox.") if self.with_logs else None

            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started updating lldp peer_device: '{peer_device_name}' interface: '{peer_device_interface_name}' on Netbox.") if self.with_logs else None

            update_peer_device_interface(
                device=device,
                lldp_peering_data=lldp_peering_data,
                peer_device=peer_device,
                peer_interface=peer_interface,
                nso_peer_device_exists=nso_peer_device_exists,
                retry=retry,
                timeout=timeout
            )
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished updating lldp peer_device: '{peer_device_name}' interface: '{peer_device_interface_name}' on Netbox.") if self.with_logs else None
        # if peer_interfaces['bundle']:
        #     self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started creating bundles for peer_device: '{peer_device_name}' on Netbox.") if self.with_logs else None
        #     create_peer_lags(peer_interfaces)
        #     self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished creating bundles for peer_device: '{peer_device_name}' on Netbox.") if self.with_logs else None

    def update_device_interfaces(self, device, nb_interfaces, matched_interfaces, retry:int, timeout:int):
        #####################################################################################
        for nb_interface in nb_interfaces:
            nb_interface.snapshot()
            matched_interface = matched_interfaces[nb_interface.name]
            #####################################################################################
            self.update_interface_vrf(device, nb_interface, matched_interface)
            if "bundle" in matched_interface.keys():
                self.update_interface_bundle(device, nb_interface, matched_interface)

            # update vlans:
            dot1q_vid = matched_interface.get("encapsulation", {}).get("dot1q", {}).pop("vlan-id", [])
            if dot1q_vid:
                # TODO:
                # vlans must be onboarded from ECO or L2 devices with their respective name
                # once the vlans have been onboarded, the getter must be changed accordingly with the correct vlan group and vlan name
                #

                # pop fields if empty
                if not matched_interface.get("encapsulation", {}).get("dot1q"):
                    matched_interface.get("encapsulation", {}).pop("dot1q", {})

                if not matched_interface.get("encapsulation", {}):
                    matched_interface.pop("encapsulation", {})

                dot1q_vid = dot1q_vid[0]
                nb_vlan, created = VLAN.objects.get_or_create(vid=dot1q_vid, name=str(dot1q_vid))
                if created:
                    self.log_info(f"created vlan vid: '{dot1q_vid}' name: '{dot1q_vid}'") if self.with_logs else None
                nb_interface.mode = InterfaceModeChoices.MODE_TAGGED
                nb_interface.tagged_vlans.set([nb_vlan.id])

            # if L3:
            for i in [4, 6]:
                afi = f"ipv{i}"
                if afi in matched_interface.keys():
                    self.update_interface_address(afi, device, nb_interface, matched_interface)
            # # if L2
            # nb_interface.enabled =  True if "up" in nso_interface['state'].casefold() else False
            # nb_interface.mtu = nso_interface['mtu']
            # after changing interface status, we need to save.

            if matched_interface:
                interface_context_entry = device.local_context_data.setdefault('interfaces', {}).setdefault(nb_interface.name, {})
                deep_merge(interface_context_entry, matched_interface)
            #####################################################################################
            # takes too long due to NSO calls been slow
            # self.update_interface_macaddress(device, nb_interface, retry=retry, timeout=timeout,)
            #####################################################################################
            nb_interface.full_clean()
            nb_interface.save()
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished Updating interface: '{nb_interface.name}' for device: '{device.name}' on Netbox.") if self.with_logs else None
            #####################################################################################

    def onboard_device_interfaces(self, device, onboard_interfaces, retry:int, timeout:int):
        #################################################################################
        nso_local_device_interf_config, nso_interface_properties = self.get_device_interface_data(device, retry=retry, timeout=timeout)
        #################################################################################
        nb_interfaces, matched_interfaces = self.get_or_create_device_interfaces(device=device, nso_interface_properties=nso_interface_properties, nso_interf_config=nso_local_device_interf_config)
        #################################################################################
        self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started updating '{len(nb_interfaces)}' interfaces for device: '{device.name}' On Netbox") if self.with_logs else None
        self.update_device_interfaces(
            device,
            nb_interfaces,
            matched_interfaces,
            retry=retry,
            timeout=timeout,
        )
        self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished updating '{len(nb_interfaces)}' interfaces for device: '{device.name}' On Netbox") if self.with_logs else None
        #################################################################################
        self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started creating/updating interface connections for device: '{device.name}' On Netbox") if self.with_logs else None
        self.create_device_connections(device, retry=retry, timeout=timeout)
        self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished creating/updating interface connections for device: '{device.name}' On Netbox") if self.with_logs else None

    def onboard_device(self, device, onboard_interfaces:bool, retry:int, timeout:int):
        onboarding_state = {
            "device-name": device.name,
            "successful": True,
            "error-messages": []
        }
        try:
            device.snapshot()


            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started onboarding device: '{device.name}'") if self.with_logs else None
            self.update_device_tags(device=device, tag_name="nso-onboarded")

            try:
                self.update_device_site(device=device)
            except (BannerNotCompliantError, UnsupportedNedError) as e:
                error_msg = f"{e}"
                self.log_failure(error_msg) if self.with_logs else None
                onboarding_state['error-messages'].append(error_msg)
                onboarding_state['successful'] = False
                return onboarding_state
            try:
                nso_device_platform = self.update_device_manufacturer(device=device)
            except UnsupportedDeviceTypeOnboardingError as e:
                self.log_failure(error_msg) if self.with_logs else None
                onboarding_state['error-messages'].append(error_msg)
                onboarding_state['successful'] = False
                return onboarding_state
            try:
                self.update_device_platform(device, nso_device_platform)
            except ValidationError as e:
                error_msg = f"{e}"
                self.log_failure(error_msg) if self.with_logs else None

                onboarding_state['error-messages'].append(error_msg)
                onboarding_state['successful'] = False
                return onboarding_state

            self.update_device_os_version(device, nso_device_platform)
            self.update_device_serial_number(device, nso_device_platform)

            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started saving device: '{device.name}'") if self.with_logs else None
            device.full_clean()
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished saving of device: '{device.name}'") if self.with_logs else None

            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started full_clean of device: '{device.name}'") if self.with_logs else None
            device.save()
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished full_clean device: '{device.name}'") if self.with_logs else None

            if onboard_interfaces:
                try:
                    self.onboard_device_interfaces(device, onboard_interfaces, retry=retry, timeout=timeout)
                except (LLDPNeighborsListEmpty, InterfaceNotFoundOnNSOError, UnsupportedNedError, TimeoutException) as e:
                    error_msg = f"{e}"
                    self.log_failure(error_msg) if self.with_logs else None

                    onboarding_state['error-messages'].append(error_msg)
                    onboarding_state['successful'] = False
                    self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started full_clean of device: '{device.name}'") if self.with_logs else None
                    device.full_clean()
                    self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished full_clean of device: '{device.name}'") if self.with_logs else None

                    self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started saving of device: '{device.name}'") if self.with_logs else None
                    device.save()
                    self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished saving of device: '{device.name}'") if self.with_logs else None
                    return onboarding_state
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started full_clean of device: '{device.name}'") if self.with_logs else None
            device.full_clean()
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished full_clean of device: '{device.name}'") if self.with_logs else None

            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started saving of device: '{device.name}'") if self.with_logs else None
            device.save()
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished saving of device: '{device.name}'") if self.with_logs else None

            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished onboarding device: '{device.name}'") if self.with_logs else None

        except Exception as e_1:

            error_msg = f"caught unhandled exception on device: '{device.name}'"

            error_msg = str(exc_info()[0](format_exc())).split(',')
            error_msg = f"```{error_msg}\n" + ''.join(error_msg) + "\n```"
            self.log_failure(error_msg)
            onboarding_state['error-messages'].append(error_msg)
            onboarding_state['successful'] = False
        return onboarding_state
