"""Tests for the install-as-service command."""

import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from meltingplot.duet_simplyprint_connector.cli.install import install_as_service

SERVICE_FILE = Path(__file__).resolve().parent.parent / "simplyprint-connector.service"


def _run_install(dsf_installed, prefix):
    # install_as_service reads the unit from sys.prefix, where only an
    # installed package puts it; use the one from the repository instead.
    shutil.copy(SERVICE_FILE, prefix / SERVICE_FILE.name)
    installed = {}
    commands = []

    def check_output(cmd, *args, **kwargs):
        commands.append(cmd)
        if cmd[:2] == ["sudo", "cp"]:
            with open(cmd[2]) as f:
                installed["unit"] = f.read()
        return b""

    def getgrnam(name):
        if dsf_installed and name == "dsf":
            return SimpleNamespace(gr_name="dsf")
        raise KeyError(name)

    with patch.object(sys, "prefix", str(prefix)), \
            patch("subprocess.check_output", side_effect=check_output), \
            patch("subprocess.run") as run, \
            patch("getpass.getuser", return_value="pi"), \
            patch("grp.getgrgid", return_value=SimpleNamespace(gr_name="pi")), \
            patch("grp.getgrnam", side_effect=getgrnam):
        result = CliRunner().invoke(install_as_service)

    assert result.exit_code == 0, result.output
    commands += [call.args[0] for call in run.call_args_list]
    return installed["unit"], commands


def test_install_as_service_on_dsf_adds_dsf_group(tmp_path):
    unit, commands = _run_install(dsf_installed=True, prefix=tmp_path)

    assert "User=pi\nGroup=pi\nSupplementaryGroups=dsf\n" in unit
    assert ["sudo", "usermod", "-aG", "dsf", "pi"] in commands


def test_install_as_service_without_dsf_leaves_groups_alone(tmp_path):
    unit, commands = _run_install(dsf_installed=False, prefix=tmp_path)

    assert "User=pi\nGroup=pi\n" in unit
    assert "SupplementaryGroups" not in unit
    assert not any("usermod" in cmd for cmd in commands)
