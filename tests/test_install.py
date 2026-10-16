"""Tests for the install-as-service command."""

from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from meltingplot.duet_simplyprint_connector.cli.install import install_as_service


def _run_install(dsf_installed):
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

    with patch("subprocess.check_output", side_effect=check_output), \
            patch("subprocess.run") as run, \
            patch("getpass.getuser", return_value="pi"), \
            patch("grp.getgrgid", return_value=SimpleNamespace(gr_name="pi")), \
            patch("grp.getgrnam", side_effect=getgrnam):
        result = CliRunner().invoke(install_as_service)

    assert result.exit_code == 0, result.output
    commands += [call.args[0] for call in run.call_args_list]
    return installed["unit"], commands


def test_install_as_service_on_dsf_adds_dsf_group():
    unit, commands = _run_install(dsf_installed=True)

    assert "User=pi\nGroup=pi\nSupplementaryGroups=dsf\n" in unit
    assert ["sudo", "usermod", "-aG", "dsf", "pi"] in commands


def test_install_as_service_without_dsf_leaves_groups_alone():
    unit, commands = _run_install(dsf_installed=False)

    assert "User=pi\nGroup=pi\n" in unit
    assert "SupplementaryGroups" not in unit
    assert not any("usermod" in cmd for cmd in commands)
