#!/opt/netbox/venv/bin/python
import django, os, sys
sys.path.append('/opt/netbox/netbox')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'netbox.settings')
django.setup()
# CLI Run with:
# source /opt/netbox/venv/bin/activate
# python manage.py runscript --loglevel debug  --data '{"limit": 1, "offset": 0, "base_url": "10.0.27.6:8080", "username": "", "password": "", "devices": [], "with_logs": true, "onboard_interfaces": true, "with_multithreading": true}' onboard_from_nso.OnboardFromNso

from extras.reports import Report
from dcim.choices import DeviceStatusChoices
from dcim.models import Device
import openpyxl
from common.utils.device import DeviceManager




"""
requirements.txt:
pip install -i http://glouton.nimda.dolmen.bouyguestelecom.fr/artifactory/api/pypi/pypi/simple --trusted-host glouton.nimda.dolmen.bouyguestelecom.fr openpyxl==3.1.2
"""

class generate_csg_services_inventory(Report):
    name = "Generates the CSG services inventory"
    description = """
        Generates a CSV inventory file from the onboarded CSG devices
    """
    job_timeout = None
    scheduling_enabled = False



    #

    # csg_devices = dm.get_or_create_csg_devices(limit_devices=limit_devices, limit=data.get('limit'), offset=data.get('offset'))
    def test_console_connection(self):
        dm = DeviceManager()

        # Let's define our one line, five column matrix
        headers = ["device", "interface", "mtu", "ip address", "vlan"]

        # Create an excel workbook and select the active sheet
        # wb = openpyxl.Workbook()
        # ws = wb.active

        # # Write matrix to excel file
        # for i in range(len(matrix)):
        #     for j in range(len(matrix[i])):
        #         ws.cell(row=i+1, column=j+1, value=matrix[i][j])

        # # Save the workbook as a .xlsx file
        # wb.save("matrix.xlsx")

        # Write the headers and separator
        md_report = []
        md_report.append(" | ".join(headers))
        md_report.append(" | ".join("---" for _ in headers))

        # # Write the matrix to the markdown list as a table
        # for row in reporting:
        #     md_lines.append("| " + " | ".join(row) + " |")
        #     md_lines.append("| " + " | ".join("---" for _ in row) + " |")

        # self.log_info('\n'.join({md_report}))
        # self.log_info('report', md_report)


        # # Now, let's create a markdown file
        # with open("matrix.md", "w") as markdown_file:
        #     markdown_file.write('\n'.join(md_lines))









    #     # Check that every console port for every active device has a connection defined.
    #     active = DeviceStatusChoices.STATUS_ACTIVE
    #     for console_port in ConsolePort.objects.prefetch_related('device').filter(device__status=active):
    #         if not console_port.connected_endpoints:
    #             self.log_failure(
    #                 console_port.device,
    #                 "No console connection defined for {}".format(console_port.name)
    #             )
    #         elif not console_port.connection_status:
    #             self.log_warning(
    #                 console_port.device,
    #                 "Console connection for {} marked as planned".format(console_port.name)
    #             )
    #         else:
    #             self.log_success(console_port.device)

    # def test_power_connections(self):
    #     # Check that every active device has at least two connected power supplies.
    #     for device in Device.objects.filter(status=DeviceStatusChoices.STATUS_ACTIVE):
    #         connected_ports = 0
    #         for power_port in PowerPort.objects.filter(device=device):
    #             if power_port.connected_endpoints:
    #                 connected_ports += 1
    #                 if not power_port.path.is_active:
    #                     self.log_warning(
    #                         device,
    #                         "Power connection for {} marked as planned".format(power_port.name)
    #                     )
    #         if connected_ports < 2:
    #             self.log_failure(
    #                 device,
    #                 "{} connected power supplies found (2 needed)".format(connected_ports)
    #             )
    #         else:
    #             self.log_success(device)


    # def run(self, data, commit):
    #     ##########################################################################################
    #     from common.utils.nso import Nso
    #     from common.utils.device import DeviceManager, NSODevicesRetrievalError
    #     ##########################################################################################
    #     # instantiate NSO
    #     nso = Nso(
    #         base_url=data.get('base_url'),
    #         username=data.get('username'),
    #         password=data.get('password'),
    #         log=[
    #             self.log_info,
    #             self.log_warning,
    #             self.log_failure
    #         ],
    #     )
    #     # instantiate DeviceManager for data parsing and onboarding
    #     dm = DeviceManager(
    #         nso,
    #         data["with_logs"],
    #         [
    #             self.log_info,
    #             self.log_warning,
    #             self.log_failure,
    #             self.log_debug
    #         ]
    #     )

    #     ###########################################################################################
    #     # Getting devices from netbox
    #     self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Getting devices from NSO and Netbox")
    #     try:
    #         limit_devices = []
    #         if data.get('devices'):
    #             limit_devices = data.get('devices').split(" ")
    #         nb_devices = dm.get_or_create_csg_devices(limit_devices=limit_devices, limit=data.get('limit'), offset=data.get('offset'))
    #     except NSODevicesRetrievalError as e:
    #         raise AbortScript(f"{e}")
    #     if not nb_devices:
    #         raise AbortScript(f"failed to retrieve devices with entered parameteres: limit_devices='{limit_devices}' - limit={data.get('limit')} - offset={data.get('offset')}")

    #     ###########################################################################################
    #     # reduce nb_devices scope to current nso onboarded devices
    #     self.log_info(f"{datetime.now().strftime('%H:%M:%S')} - Started Onboarding device items for: '{len(nb_devices)}' devices on Netbox.")

    #     result_summary = [
    #         "|     device    |  successfuly onboarded |        Error logs     |",
    #         "| :-----------: | :--------------------: | :-------------------: |",
    #     ]

    #     if data["with_multithreading"]:
    #         with ThreadPoolExecutorStackTraced(max_workers=5) as executor:
    #             threads = [
    #                 executor.submit(
    #                     dm.onboard_device,
    #                     local_device,
    #                     data["onboard_interfaces"],
    #                 )
    #                 for local_device in nb_devices
    #             ]
    #             # do something with results (traceback, return status etc.)
    #             results = [future.result() for future in threads]

    #             for res in results:
    #                 success_msg = "yes" if res['successful'] else "No"
    #                 error_logs = '<br>'.join(res['error-messages'])
    #                 result_summary.append(
    #                     f"| {res['device-name']} | {success_msg} | {error_logs} |"
    #                 )
    #             result_summary = "\n".join(result_summary)
    #     else:
    #         for local_device in nb_devices:
    #             dm.onboard_device(local_device, onboard_interfaces=onboard_interfaces)
    #     ############################################################################
    #     if dm.peers_not_onboarded_on_nso:
    #         self.log_warning(f"The following devices are not onboarded on NSO: {dm.peers_not_onboarded_on_nso}")
    #     onbarding_state = "success"
    #     if not all(result['successful'] == True for result in results):
    #         onbarding_state = "failure"
    #     logger = getattr(
    #         self,
    #         f"log_{onbarding_state}",
    #     )
    #     logger(f"{datetime.now().strftime('%H:%M:%S')} - onboarding of: '{len(nb_devices)}' NSO devices to Netbox was a: {onbarding_state}.")
    #     logger(f"\n{result_summary}")

