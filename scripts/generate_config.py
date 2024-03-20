from extras.scripts import Script, ObjectVar, MultiObjectVar, BooleanVar
from dcim.models import Manufacturer, Device, Interface
import os
from common.config.generate import  generate_interfaces_config

from common.utils.functions import load_file, update_file


class Generate_config(Script):
    class Meta:
        name = "Device Config Generator"
        description = """
            Parses device context data and
            related objects in order to generate
            a full configuration.
        """

    manufacturer = ObjectVar(
        model=Manufacturer,
        required=False,
    )

    device = MultiObjectVar(
        model=Device,
        required=True,
        query_params={
            'status': 'active',
            'manufacturer_id': '$manufacturer'
        },
    )

    interfaces = MultiObjectVar(
        model=Interface,
        required=False,
        default=[],
        query_params={
            'device_id': '$device'
        },
    )


    dry_run = BooleanVar(
        default=True,
        description="Generated configuration will not be pushed/applied"
    )

    nso = BooleanVar(
        default=True,
        description="Generate configuration for NSO service packages"
    )

    cli = BooleanVar(
        default=True,
        description="Generate configuration for CLI"
    )

    def init_templates(self):
        self.config_templates = {
            "cli": {},
            "nso": {}
        }
        self.config_template_cli = self.config_templates["cli"]
        self.config_template_nso = self.config_templates["nso"]
        ##################################################################
        # TODO get all templates using dir on ./templates, then load them dynamically:
        self.config_template_cli["banner"] = load_file("txt", f"{self.WORK_DIR}/templates/ios-xr/banner.txt")
        self.config_template_cli["interfaces"] = load_file("txt", f"{self.WORK_DIR}/templates/ios-xr/interfaces.txt")

    def run(self, data, commit):
        self.WORK_DIR = os.path.dirname(os.path.realpath(__file__))
        self.ROOT_DIR = f"{os.path.dirname(os.getcwd())}/netbox"

        config_cli = ""
        ##################################################################
        interfaces =  data.get("interfaces")

        interfaces_scope = []
        for interface in interfaces:
            interfaces_scope.append(interface.name)
        ##################################################################
        self.init_templates()
        for device_name in data['device']:
            ##################################################################
            device = Device.objects.get(name=device_name)

            config_context = device.get_config_context()

            ##################################################################
            config_cli += f"""\n{self.config_template_cli["banner"].format(
                banner_login=config_context.get('banner_login', 'NOTSET')
            )}\n"""
            ##################################################################
            interfaces_config_cli = generate_interfaces_config(
                self,
                device=device,
                interfaces_scope=interfaces_scope,
                cli_config_template=self.config_template_cli["interfaces"],
                device_context_data=config_context,
                cli=data['cli'],
                nso=data['nso']
            )

            config_cli += interfaces_config_cli
            ##################################################################
            update_file(
                config_cli,
                f"{self.ROOT_DIR}/generated-configs/config-{device_name}.txt"
            )
            self.log_success(f'Finished generating cli configuration for device: {device_name}')
