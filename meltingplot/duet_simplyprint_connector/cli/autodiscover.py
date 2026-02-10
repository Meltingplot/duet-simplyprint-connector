"""
This module provides functionality to autodiscover devices within specified IP ranges.

The module connects to them using the RepRapFirmware API.
It uses the RepRapFirmware API to connect to devices and retrieve their unique IDs.

Functions:
    convert_cidr_to_list(ip_range) -> list:
        Convert a CIDR notation IP range to a list of individual IP addresses.

    connect_to_duet(ip_address, password):
        Connect to a printer using the specified IP address and password.

    connect_to_range(password, ipv4_range, ipv6_range):
        Connect to all devices within the specified IP ranges.

    autodiscover(password, ipv4_range, ipv6_range):
        Autodiscover devices in the specified IP range using the provided password.
"""
import asyncio
import io
import ipaddress
import json
import urllib.parse

import aiohttp
import click
from simplyprint_ws_client.core.app import ClientApp

from ..duet.api import RepRapFirmware
from ..network import get_local_ip_and_mac


def convert_cidr_to_list(ip_range) -> list:
    """Convert a CIDR notation IP range to a list of individual IP addresses."""
    try:
        ip_network = ipaddress.ip_network(ip_range, strict=False)
        return list(ip_network.hosts())
    except ValueError:
        return None


async def download_dwc_file(duet: RepRapFirmware) -> dict:
    """Download the DWC settings file from the Duet."""
    content = io.BytesIO()
    async for chunk in duet.rr_download(filepath='0:/sys/dwc-settings.json'):
        content.write(chunk)
    content.seek(0)
    response = json.load(content)
    return response


def _format_hostname_for_url(hostname: str) -> str:
    """Format the hostname for URL reassembling."""
    try:
        if ipaddress.ip_address(hostname).version == 6:
            hostname = f"[{hostname}]"
    except ValueError:
        # Not an IP address, keep it as is
        pass
    return hostname


async def get_webcam_url(duet: RepRapFirmware) -> str:
    """Sanitize the webcam URL."""
    dwc_settings = await download_dwc_file(duet)

    if dwc_settings is None:
        return None

    try:
        webcam_url = dwc_settings['main']['webcam']['url']
    except KeyError:
        return None

    if "[HOSTNAME]" in webcam_url:
        # urlparse is not able to parse the URL if it contains [HOSTNAME]
        # as it will handle it like an IPv6 address
        webcam_url = webcam_url.replace("[HOSTNAME]", "HOSTNAME")

    webcam_url_parse = urllib.parse.urlparse(webcam_url)
    schema = webcam_url_parse.scheme
    hostname = webcam_url_parse.netloc

    if hostname == '' or (webcam_url_parse.hostname is not None and webcam_url_parse.hostname.lower() == 'hostname'):
        duet_url_parse = urllib.parse.urlparse(duet.address)

        if duet_url_parse.hostname != '':
            hostname = _format_hostname_for_url(duet_url_parse.hostname)
        if webcam_url_parse.port is not None:
            hostname = f"{hostname}:{webcam_url_parse.port}"
        if hostname == '':
            # fallback
            hostname = duet_url_parse.geturl()

        schema = duet_url_parse.scheme

    if schema == '':
        schema = 'http'

    webcam_url = urllib.parse.urlunparse(
        (
            schema,
            hostname,
            webcam_url_parse.path,
            webcam_url_parse.params,
            webcam_url_parse.query,
            webcam_url_parse.fragment,
        ),
    )

    return webcam_url


async def get_cookie(duet: RepRapFirmware) -> str:
    """Get the connector cookie."""
    content = io.BytesIO()
    async for chunk in duet.rr_download(filepath='0:/sys/simplyprint-connector.json'):
        content.write(chunk)
    content.seek(0)
    return json.load(content)


def normalize_url(url: str) -> str:
    """Sanitize the URL."""
    if url.startswith('http://') or url.startswith('https://'):
        return url
    return f'http://{url}'


async def connect_to_duet(address: str, password: str) -> dict:
    """Connect to a printer using the specified IP address and password."""
    duet = RepRapFirmware(
        address=normalize_url(address),
        password=password,
    )

    try:
        await duet.connect()
        board = await duet.rr_model(key='boards[0]')
        board = board['result']
        duet_name = await duet.rr_model(key='network.name')
        duet_name = duet_name['result']
        try:
            webcam_uri = await get_webcam_url(duet)
        except aiohttp.client_exceptions.ClientResponseError:
            webcam_uri = None
        try:
            cookie = await get_cookie(duet)
        except aiohttp.client_exceptions.ClientResponseError:
            cookie = None
    except (
        aiohttp.client_exceptions.ClientConnectorError,
        aiohttp.ClientError,
        asyncio.exceptions.CancelledError,
        asyncio.exceptions.TimeoutError,
        OSError,
        KeyError,
    ):
        return None
    finally:
        await duet.close()

    return {
        'duet_name': f"{duet_name}",
        'duet_uri': normalize_url(f'{address}'),
        'duet_password': password,
        'duet_unique_id': f"{board['uniqueId']}",
        'webcam_uri': normalize_url(webcam_uri) if webcam_uri is not None else None,
        'cookie': cookie,
    }


async def connect_to_range(
    password: str,
    ipv4_range: ipaddress.IPv4Network,
    ipv6_range: ipaddress.IPv6Network,
) -> list:
    """Connect to all devices within the specified IP ranges."""
    tasks = []
    for ipv4 in ipv4_range:
        tasks.append(connect_to_duet(f"{ipv4}", password))
    for ipv6 in ipv6_range:
        tasks.append(connect_to_duet(f"[{ipv6}]", password))

    return await asyncio.gather(*tasks)


class AutoDiscover():
    """
    A class to handle the autodiscovery of devices within specified IP ranges.

    Attributes:
        app (ClientApp): The application instance.
        autodiscover (click.Command): The Click command for autodiscovery.

    Methods:
        __init__(app: ClientApp) -> None:
            Initializes the AutoDiscover class with the given application instance.
    """

    def __init__(self, app: ClientApp) -> None:
        """Initialize the AutoDiscover class with the given application instance."""
        self.app = app

        netinfo = get_local_ip_and_mac()
        ipv4_range = ipaddress.ip_network(netinfo.ip).supernet(new_prefix=24)
        default_ipv4_range = f"{ipv4_range}"

        self._autodiscover = self.autodiscover
        self.autodiscover = click.Command(
            name='autodiscover',
            callback=self.autodiscover,
            params=[
                click.Option(
                    ['--password'],
                    prompt=True,
                    default='reprap',
                    hide_input=False,
                    confirmation_prompt=False,
                    help='Password for authentication',
                ),
                click.Option(
                    ['--ipv4-range'],
                    prompt=True,
                    default=default_ipv4_range,
                    help='IPv4 range to scan for devices',
                ),
                click.Option(
                    ['--ipv6-range'],
                    prompt=True,
                    default='::1/128',
                    help='IPv6 range to scan for devices',
                ),
            ],
        )

    def autodiscover(self, password, ipv4_range, ipv6_range):
        """Autodiscover devices in the specified IP range."""
        ipv4_addresses = convert_cidr_to_list(ipv4_range)
        ipv6_addresses = convert_cidr_to_list(ipv6_range)

        click.echo(
            f'Starting autodiscovery with password: {password}, '
            f'IPv4 range: {ipv4_range}, and IPv6 range: {ipv6_range}',
        )

        responses = asyncio.run(connect_to_range(password, ipv4_addresses, ipv6_addresses))

        clients = {f"{client['duet_unique_id']}": client for client in responses if client is not None}

        self.app.logger.info(f'Found {len(clients)} devices.')

        for client in clients.values():
            self.app.logger.info(f'Found device: {client["duet_name"]}')

        configs = self.app.config_manager.get_all()
        for config in configs:
            if config.duet_unique_id in clients:
                self.app.logger.info(f'Found existing config for {config.duet_unique_id}. Updating.')

                config.duet_uri = normalize_url(clients[config.duet_unique_id]['duet_uri'])
                config.webcam_uri = clients[config.duet_unique_id]['webcam_uri']
                clients.pop(config.duet_unique_id, None)

        for client in clients.values():
            if client['cookie'] is not None:
                self.app.logger.info(
                    f"Skip adding new config for {client['duet_name']} - {client['duet_unique_id']}"
                    f" as it already has a cookie set under 0:/sys/simplyprint-connector.json",
                )
                continue
            self.app.logger.info(f"Adding new config for {client['duet_name']} - {client['duet_unique_id']}")
            config = self.app.config_manager.config_t.get_new()
            config.duet_name = client['duet_name']
            config.duet_uri = client['duet_uri']
            config.duet_password = client['duet_password']
            config.duet_unique_id = client['duet_unique_id']
            config.webcam_uri = client['webcam_uri']
            config.in_setup = True
            self.app.config_manager.persist(config)
            self.app.add(config)

        self.app.config_manager.flush()
