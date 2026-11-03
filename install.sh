#!/bin/bash
# It is used to install the necessary components for the Meltingplot Duet SimplyPrint Connector.
# Supported systems: Debian Bookworm or newer (including Raspberry Pi OS) and Ubuntu 24.04 or newer.
# Ensure you have the required permissions to execute this script.

# The README runs this script with `source`, so run it in a subshell to keep
# `set -e` and `exit` from closing the caller's shell.
(
set -e
trap 'echo "An error occurred. Exiting..."' ERR

case "$(uname -m)" in
    aarch64 | x86_64) ;;
    *) echo "It is recommended to use a 64-bit system." ;;
esac

. /etc/os-release
if [[ " $ID $ID_LIKE " != *" debian "* ]]; then
    echo "This script is only for Debian based systems like Debian, Raspberry Pi OS or Ubuntu."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
if [[ $(echo -e "3.11\n$PYTHON_VERSION" | sort -V | head -n1) != "3.11" ]]; then
    echo "Python 3.11 or higher is required."
    exit 1
fi

sudo mkdir -p /opt/duet-simplyprint-connector
sudo chown "$(id -un)":"$(id -gn)" /opt/duet-simplyprint-connector
sudo chmod 755 /opt/duet-simplyprint-connector

sudo apt-get update
PACKAGES=(git ffmpeg python3-venv gcc g++ make python3-dev libopenblas0 libopenblas-dev liblapack-dev)
# libatlas-base-dev is no longer available since Debian Trixie and Ubuntu 26.04
if apt-cache show libatlas-base-dev > /dev/null 2>&1; then
    PACKAGES+=(libatlas-base-dev)
fi
sudo apt-get install -y "${PACKAGES[@]}"

cd /opt/duet-simplyprint-connector
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install meltingplot.duet_simplyprint_connector
simplyprint autodiscover
simplyprint install-as-service
)
