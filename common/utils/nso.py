from requests import request
from requests.exceptions import Timeout as TimeoutException
import xmltodict
import argparse
from json import loads as json_loads
from time import sleep



class UnsupportedNedError(Exception):
    pass


class UnsupportedInterfacefType(Exception):
    pass


class SkipInterfaceType(Exception):
    pass



# import asyncio
# from aiohttp import ClientSession, ClientTimeout
#     async def request(self, method:str, url:str, headers:dict, ssl_verify:bool=False, timeout:int=5, n_retries:int=3, data:dict={}):
#         timeout = ClientTimeout(total=timeout)
#         auth = aiohttp.BasicAuth(self.username, self.password)
#         retries = 0
#         delay = 10

#         while retries < n_retries:

#             async with ClientSession(timeout=timeout, headers=headers) as session:
#                 try:
#                     if data:
#                         response = await session.request(method, url, verify_ssl=ssl_verify, data=data, auth=auth)
#                     else:
#                         response = await session.request(method, url, verify_ssl=ssl_verify, auth=auth)

#                     # Here, you may want to add some response status code checking logic
#                     response.raise_for_status()

#                     break

#                 except asyncio.TimeoutError:
#                     print(f"Timeout exception caught, waiting for {delay} seconds before retrying...")
#                     retries += 1
#                     await asyncio.sleep(delay)
#                     delay *= 2

#                 except Exception as e:
#                     raise e

#         return response

#     async def main(self):
#         response = await self.request('GET', 'url', 'headers')
# This allows other tasks to run during a sleep


class Nso(object):
    """
        NSO request class

        possible headers:
            application/yang-data+xml
            application/yang-data+json

            application/vnd.yang.collection+xml
            application/vnd.yang.collection+json

            application/yang-patch+xml
            application/yang-patch+json
    """
    def __init__(self, *args, **kwargs):
        self.username = kwargs.get("username")
        self.password = kwargs.get("password")

        self.base_url = f"http://{kwargs.get('base_url')}"
        self.log_info = kwargs.get("log")[0]
        self.log_warning = kwargs.get("log")[1]
        self.log_failure = kwargs.get("log")[2]
        self.log_debug = kwargs.get("log")[3]

    def request(self, method:str, url:str, headers:dict, ssl_verify:bool=False, timeout:int=5, retry:int=3, data:dict={}):
        kwargs = {
            "method": method,
            "url": url,
            "headers": headers,
            "auth": (self.username, self.password),
            "verify": ssl_verify,
            "timeout": timeout,
        }

        if data:
            kwargs.update({"json": data})
        delay = 5  # start retry after 3 seconds
        for i in range(retry):
            try:
                resp = request(**kwargs)
                break
            except TimeoutException:
                if i == retry - 1:  # if it is the last retry
                    raise  # re-raise the last exception
                else:
                    self.log_warning(f"Timeout exception caught, timedout after: '{timeout}' waiting for {delay} seconds before retrying for: {i+2}/{retry} times...")
                    sleep(delay)
                    delay *= 2  # double the delay
        return resp

    def query(self, payload:dict={}):
        url = f"{self.base_url}/restconf/tailf/query"
        headers = {
            "Content-Type": "application/yang-data+json",
        }
        resp = self.request("POST", url, headers, data=payload)
        if resp.status_code == 200:
            parsed_resp = json_loads(resp.text)
            parsed_resp = parsed_resp.get(list(parsed_resp.keys())[0], {}).get("result", [])
        else:
            parsed_resp = {}
        return parsed_resp, resp

    def test_credentials(self):
        url = f"{self.base_url}/restconf"

        headers = {
            "Accept": "application/yang-data+xml"
        }
        resp = self.request("GET", url, headers)

        if resp.status_code == 200:
            return True
        else:
            self.log_failure(f"status code: '{resp.status_code}' resp text: '{resp.text}'")
            return False

    def get_device(self, device:str, attribute:str=""):
        """
        """
        url = f"{self.base_url}/restconf/data/tailf-ncs:devices/device={device}"
        if attribute:
            url = f"{url}/{attribute}"
        headers = {
            "Accept": "application/yang-data+xml"
        }
        resp = self.request("GET", url, headers)

        if resp.status_code == 200:
            parsed_resp = xmltodict.parse(resp.text)
        else:
            parsed_resp = {}
        if attribute:
            parsed_resp = parsed_resp.get(attribute, parsed_resp)
            parsed_resp = parsed_resp.get("#text", parsed_resp)
        return parsed_resp, resp

    def get_device_config(self, device:str, ned_id:str="", attribute:str=""):
        headers={
            "Accept": f"application/yang-data+json"
        }

        url = f"{self.base_url}/restconf/data/tailf-ncs:devices/device={device}/config"
        if attribute:
            # dynamic ned-id matching
            ned_id = ""
            nso_device_type, resp = self.get_device(device=device, attribute="device-type")
            try:
                nso_device_type = nso_device_type["cli"]["ned-id"]["#text"]
            except KeyError:
                raise UnsupportedNedError(f"couldn't retrieve device-type for device: {device} - resp.status_code: '{resp.status_code}' nso_device_type: '{nso_device_type}' ")

            if "cisco" in nso_device_type:
                ned_id = "tailf-ned-cisco"
                pass
            else:
                raise UnsupportedNedError(f"nso ned-id: '{nso_device_type}' is currently not supported")

            platform, resp = self.get_device(device=device, attribute="platform")
            platform_name = platform.get('name')
            if not platform_name:
                raise UnsupportedNedError(f"couldn't retrieve platform for device: '{device}' resp.status_code: '{resp.status_code}' platform: '{platform}'  ")

            ned_id = f"{ned_id}-{platform['name']}"
            url = f"{url}/{ned_id}:{attribute}"

        resp = self.request("GET", url, headers)

        parsed_resp = {}
        if resp.status_code == 200:
            parsed_resp = resp.json()
            parsed_resp = parsed_resp.get(list(parsed_resp.keys())[0])

        if attribute:
            parsed_resp = parsed_resp.get(f"{ned_id}:{attribute}", parsed_resp)

        return parsed_resp, resp

    def get_device_live_status(self, device:str, path:str="", timeout:int=30, retry:int=3):
        """
            eg: get lldp:
                    path="tailf-ned-cisco-ios-xr-stats:lldp"
                    > live-status/tailf-ned-cisco-ios-xr-stats:lldp"

                get interfaces-state:
                    path="ietf-interfaces:interfaces-state/interface={interface}"
                    > live-status/ietf-interfaces:interfaces-state/interface=Bundle-Ether0

        """
        url =  f"{self.base_url}/restconf/data/tailf-ncs:devices/device={device}/live-status"
        if path:
            url = f"{url}/{path}"
        headers = {
            "Accept": "application/yang-data+json",
        }
        resp = self.request("GET", url, headers, timeout=timeout, retry=retry)

        parsed_response = {}
        if resp.status_code == 200:
            parsed_response = resp.json()
            parsed_response = parsed_response.get(list(parsed_response.keys())[0])
        return parsed_response, resp

    # utility method to match netbox type with nso type
    def match_interface_type(self, nso_interf_type):
        """
            Matches nso interface type with:
                > Netbox interface type
                > config/tailf-ned-cisco-ios-xr:interface interface type

            returns:
                Matched interface type: (tupple)
        """
        collection_types = {
            "IFT_LOOPBACK": ("virtual", "Loopback"),
            "IFT_FINT_INTF": (None, None),
            "IFT_ETHERBUNDLE": ("lag", "Bundle-Ether"),
            "IFT_ETHERNET": ("1000base-t", "GigabitEthernet"),
            "IFT_GETHERNET":  ("1000base-t", "GigabitEthernet"),
            "IFT_TENGETHERNET": ("10gbase-x-sfpp", "TenGigE"),
            "IFT_TWENTYFIVEGETHERNET": ("25gbase-x-sfp28", "TwentyFiveGigE"),
            "IFT_HUNDREDGE": ("100gbase-x-qsfp28", "HundredGigE"),
            "IFT_OPTICS": (None, None),
            "IFT_NULL": (None, None),
            "IFT_VLAN_SUBIF": ("virtual", ["Bundle-Ether-subinterface"])
        }
        if nso_interf_type not in collection_types:
            raise UnsupportedInterfacefType(f"NSO interface type: '{nso_interf_type}' is not supported")
        if not collection_types[nso_interf_type][1]:
            raise SkipInterfaceType(f"NSO interface type: '{nso_interf_type}' is not supported")

        return collection_types[nso_interf_type]


if __name__ == "__main__":
    """
        python nso.py --base-url sdn-nsosl01:8080 --username user --password
    """

    parser = argparse.ArgumentParser(
        description='A script to sync Netshot to Netbox device data.'
    )
    parser.add_argument(
        "--base-url",
        help="NSO restconf base url",
        type=str,
    )

    parser.add_argument(
        "--username",
        help="NSO restconf username",
        action="store",
        dest="username",
        type=str,
    )

    parser.add_argument(
        "--password",
        help="NSO restconf password",
        action="store",
        dest="password",
        type=str,
    )

    parser.add_argument(
        "--device",
        help="NSO device name",
        action="store",
        dest="device",
        type=str,
        default="csg002000"
    )

    kwargs = parser.parse_args()

    nso = Nso(
        base_url=kwargs.base_url,
        username=kwargs.username,
        password=kwargs.password,
    )

    response, resp = nso.get_device(device=kwargs.device, platform="name")
    parsed_response = xmltodict.parse(response.text)
    print(response.text)
    print(parsed_response)

    response = nso.get_lldp_neighbors(device=kwargs.device)
    parsed_response = xmltodict.parse(response.text)
    print(response.text)
    print(parsed_response)
