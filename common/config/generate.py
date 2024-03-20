
from dcim.models import Interface



def generate_interfaces_config(cls, device, interfaces_scope, cli_config_template, device_context_data, cli: bool, nso: bool):
    def generate_cli(interface, context_data, cli_config_template):
        ######################################################################
        # Set default values
        bundle_id = "NOTSET"
        description = "NOTSET"
        mtu = "NOTSET"
        enabled = "shutdown"
        # TODO: needs to be dynamic, lines must be appended on found features...
        ######################################################################
        if interface.lag:
            bundle_id = int(interface.lag.name.split("Bundle-Ether")[1])
        ######################################################################
        if interface.description:
            description = interface.description
        ######################################################################
        if interface.mtu:
            mtu = interface.mtu
        ######################################################################
        if interface.enabled:
            enabled = "no shutdown"
        ######################################################################
        # cls.log_info(context_data)
        ######################################################################
        interface_cli_config = cli_config_template.format(
            interface=interface.name,
            description=description,
            bundle_id=bundle_id,
            enabled=enabled,
            mtu=mtu,
            svp_in=context_data.get("service_policy", {}).get("in", "NOTSET"),
            svp_out=context_data.get("service_policy", {}).get("out", "NOTSET"),
            lacp_period=context_data.get("lacp_period", "NOTSET"),
            load_interval=context_data.get("load_interval", "NOTSET"),
            delay_up=context_data.get("carrier_delay", {}).get("up", "NOTSET"),
            delay_down=context_data.get("carrier_delay", {}).get("down", "NOTSET"),
        )
        return "\n".join([line for line in interface_cli_config.splitlines() if "NOTSET" not in line])

    config_cli = ""
    # Get local_context_data for current device
    interfaces_cd = device_context_data.get('interfaces', {})

    # Get interfaces for current device within the scope
    interfaces = Interface.objects.filter(device_id=device.id)
    # interfaces = Interface.objects.filter(device_id=device.id, name__in=interfaces_scope)
    # self.log_success(f'device: {device.name} has: {len(interfaces)} interfaces')

    if interfaces:
        for interface in interfaces:
            if interfaces_scope:
                if interface.name not in interfaces_scope:
                    continue
            # get context data for current interface:
            if cli:
                interface_cli_config = generate_cli(
                    interface,
                    interfaces_cd.get(interface.name, {}),
                    cli_config_template
                )
                # self.log_info(f'generated configuration for interface: {interface.name}\n{cli_config_template}')
                config_cli += f"{interface_cli_config}\n"
            if nso:
                pass
    return config_cli