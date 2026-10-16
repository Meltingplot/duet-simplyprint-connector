"""Installscript of the Duet Simplyprint Connector."""
import click

#: Group that owns the DSF SD directory (/opt/dsf/sd) on SBC setups.
DSF_GROUP = "dsf"


def _dsf_group_exists() -> bool:
    """Return True if DSF is installed, i.e. its group exists."""
    import grp

    try:
        grp.getgrnam(DSF_GROUP)
    except KeyError:
        return False
    return True


@click.command()
def install_as_service():
    """Install the Simplyprint Connector as a systemd service."""
    import getpass
    import grp
    import os
    import subprocess
    import sys
    import tempfile

    # Get the path of the service file
    service_file = os.path.join(sys.prefix, 'simplyprint-connector.service')

    service_content = None

    # Read the content of the service file
    with open(service_file, 'r') as file:
        service_content = file.read()

    # Replace User and Group with the current user and group
    current_user = getpass.getuser()
    current_group = grp.getgrgid(os.getgid()).gr_name
    service_content = service_content.replace('User=ubuntu', f'User={current_user}', 1)
    service_content = service_content.replace('Group=ubuntu', f'Group={current_group}', 1)

    click.echo(f"Installing the service as user/group: {current_user}/{current_group}")

    # In SBC mode the connector writes uploads straight into DSF's SD
    # directory, which is dsf:dsf 0775. Give the service the dsf group, and
    # add the user to it so manual CLI runs get the same access.
    if _dsf_group_exists():
        service_content = service_content.replace(
            f"Group={current_group}\n",
            f"Group={current_group}\nSupplementaryGroups={DSF_GROUP}\n",
            1,
        )
        click.echo(f"DSF detected, adding {current_user} to the {DSF_GROUP} group")
        subprocess.check_output(["sudo", "usermod", "-aG", DSF_GROUP, current_user])

    # Save the modified content to a temp file
    with tempfile.NamedTemporaryFile('wt+') as tmp_file:
        tmp_file.write(service_content)
        tmp_file.flush()

        # Copy the service file to /etc/systemd/system
        subprocess.check_output(['sudo', 'cp', tmp_file.name, '/etc/systemd/system/simplyprint-connector.service'])

    executable_file = os.path.join(sys.prefix, 'bin/simplyprint')

    # Make the simplyprint command available outside the venv
    subprocess.run(['sudo', 'ln', '-s', executable_file, '/usr/local/bin/simplyprint'])

    # Reload the systemd daemon
    subprocess.run(['sudo', 'systemctl', 'daemon-reload'])

    # Enable the service
    subprocess.run(['sudo', 'systemctl', 'enable', 'simplyprint-connector'])

    # Start the service
    subprocess.run(['sudo', 'systemctl', 'start', 'simplyprint-connector'])

    print('The Simplyprint Connector has been installed as a systemd service.')
