
# Netbox Toolkit

This repository contains a collection of custom scripts for onboarding devices from NSO (Network Services Orchestrator) and generating CSV reports using Netbox.

## Features

- Onboarding devices from NSO: The `onboard_from_nso.py` script allows you to easily onboard devices from NSO into Netbox. It retrieves device information from NSO and creates/updates corresponding device records in Netbox.

- Generating CSV reports: The `generate_reports.py` script generates CSV reports based on data stored in Netbox. You can customize the report parameters and export the data to CSV format for further analysis.

## Prerequisites

These scripts collection are to be run through the netbox/customs interface. the deployment of this framework has already been automated through the Jenkins pipeline for netbox deployment.

This collection can also be run locally through the CLI, but this will need to be deployed on the Netbox worker host.

## Usage

A. through GUI:
1. custom scripts
2. fill input boxes
3. run


B. Through CLI:
1. deploy collection on your netbox-worker at /opt/netbox/netbox
2. install requirements:
```bash
openpyxl==3.1.2
manuf==1.1.5
xmltodict==0.13.0
```
3. run with:

```bash
    source /opt/netbox/venv/bin/activate
    python manage.py runscript --loglevel debug --commit --data '{"limit": 5000, "offset": 1, "base_url": "10.10.10.1:8080", "username": "ifoughal", "password": "Cisco123", "devices": "", "with_logs": true, "nso_timeout": 500, "with_nso": false, "nso_retry": 1, "with_multithreading": true}' <report-file>.<report-class>
```


## Contributing

Contributions to this repository are welcome.
If you find any issues or have suggestions for improvements, create an issue.
