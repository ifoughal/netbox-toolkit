#!/opt/netbox/venv/bin/python
from os import environ
from django import setup
from sys import exc_info, path
path.append('/opt/netbox/netbox')
environ.setdefault('DJANGO_SETTINGS_MODULE', 'netbox.settings')
setup()
# CLI Run with:
# source /opt/netbox/venv/bin/activate
# python manage.py runscript --loglevel debug  --data '{"limit": 1, "offset": 0, "base_url": "10.0.1.1:8080", "username": "", "password": "", "devices": [], "with_logs": true, "onboard_interfaces": true, "with_multithreading": true}' onboard_from_nso.OnboardFromNso

from extras.scripts import Script, StringVar, TextVar, IntegerVar, BooleanVar
from django.forms import PasswordInput
from common.utils.functions import ThreadPoolExecutorStackTraced
from datetime import datetime
from utilities.exceptions import AbortScript
from traceback import format_exc
"""
requirements.txt:
pip install -i http://local-registry.local/artifactory/api/pypi/pypi/simple --trusted-host local-registry.local manuf==1.1.5
pip install -i http://local-registry.local/artifactory/api/pypi/pypi/simple --trusted-host local-registry.local xmltodict==0.13.0
"""

class OnboardFromNso(Script):
    class Meta:
        name = "Onboard devices from NSO"
        description = """
            Onboards devices from NSO CDB to Netbox
        """
        job_timeout = 5000
        commit_default = True
        scheduling_enabled = False

    limit = IntegerVar(
        required=True,
        default=1
    )

    offset = IntegerVar(
        required=True,
        default="0"
    )

    base_url = StringVar(
        required=True,
        default="10.10.10.1:8080",
    )

    username = StringVar(
        required=True,
        default="ifoughal",
    )

    password = StringVar(
        required=True,
        widget=PasswordInput,
    )

    nso_timeout = IntegerVar(
        required=True,
        default=500
    )

    nso_retry = IntegerVar(
        required=True,
        default=1
    )

    devices = TextVar(
        required=False,
    )

    with_logs = BooleanVar(
        default=True,
    )

    onboard_interfaces = BooleanVar(
        default=True,
    )

    with_multithreading = BooleanVar(
        default=True,
    )

    def run(self, data, commit):
        try:
            ##########################################################################################
            from common.utils.nso import Nso
            from common.utils.device import DeviceManager, NSODevicesRetrievalError
            ##########################################################################################
            # instantiate NSO
            nso = Nso(
                base_url=data.get('base_url'),
                username=data.get('username'),
                password=data.get('password'),
                log=[
                    self.log_info,
                    self.log_warning,
                    self.log_failure,
                    self.log_debug

                ],
            )
            # instantiate DeviceManager for data parsing and onboarding
            dm = DeviceManager(
                nso,
                data["with_logs"],
                [
                    self.log_info,
                    self.log_warning,
                    self.log_failure,
                    self.log_debug
                ]
            )

            ###########################################################################################
            result = nso.test_credentials()
            if not result:
                raise AbortScript("failed to authenticate to NSO with current credentials...")
            self.log_success("NSO given crendentials successfuly authenticated!")
            ###########################################################################################
            # Getting devices from netbox
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started retrieving devices from NSO and Netbox")
            try:
                limit_devices = []
                if data.get('devices'):
                    limit_devices = data.get('devices').split(" ")
                nb_devices = dm.get_or_create_csg_devices(limit_devices=limit_devices, limit=data.get('limit'), offset=data.get('offset'))
            except NSODevicesRetrievalError as e:
                raise AbortScript(f"{e}")
            if not nb_devices:
                raise AbortScript(f"failed to retrieve devices with entered parameteres: limit_devices='{limit_devices}' - limit={data.get('limit')} - offset={data.get('offset')}")
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Finished retrieving: '{len(nb_devices)}' devices from Netbox.")
            ###########################################################################################
            # reduce nb_devices scope to current nso onboarded devices
            self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started Onboarding device items for: '{len(nb_devices)}' devices on Netbox.")

            result_summary = [
                "|     device    |  successfuly onboarded |        Error logs     |",
                "| :-----------: | :--------------------: | :-------------------: |",
            ]


            if data["with_multithreading"]:
                with ThreadPoolExecutorStackTraced(max_workers=5) as executor:
                    threads = [
                        executor.submit(
                            dm.onboard_device,
                            device=device,
                            onboard_interfaces=data["onboard_interfaces"],
                            retry=data["nso_retry"],
                            timeout=data["nso_timeout"],
                        )
                        for device in nb_devices
                    ]
                    # do something with results (traceback, return status etc.)
                    results = [future.result() for future in threads]

                    for res in results:
                        success_msg = "yes" if res['successful'] else "No"
                        error_logs = '<br>'.join(res['error-messages'])
                        result_summary.append(
                            f"| {res['device-name']} | {success_msg} | {error_logs} |"
                        )
                    result_summary = "\n".join(result_summary)
            else:
                for local_device in nb_devices:
                    dm.onboard_device(local_device, onboard_interfaces=onboard_interfaces)
            ############################################################################
            if dm.peers_not_onboarded_on_nso:
                self.log_warning(f"The following devices are not onboarded on NSO: {dm.peers_not_onboarded_on_nso}")
            onbarding_state = "success"
            if not all(result['successful'] == True for result in results):
                onbarding_state = "failure"
            logger = getattr(
                self,
                f"log_{onbarding_state}",
            )
            logger(f"{datetime.now().strftime('%H:%M:%S')} - onboarding of: '{len(nb_devices)}' NSO devices to Netbox was a: {onbarding_state}.")
            logger(f"\n{result_summary}")

        except AbortScript as e:
            raise AbortScript(f"{e}")
        except ModuleNotFoundError as e:
            raise AbortScript(f"Missing module(s): {e}")
        except Exception as e_1:
            error_msg = str(exc_info()[0](format_exc())).split(',')
            error_msg = "```\n" + ''.join(error_msg) + "\n```"
            self.log_failure(error_msg)
            raise AbortScript(f"failed due to caughting unhandled exception")

