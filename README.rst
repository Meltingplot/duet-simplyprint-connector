Duet RepRapFirmware to SimplyPrint.io connector
================================================

This package acts as a bridge between Duet-based 3D printers and the SimplyPrint.io cloud service.

It communicates with the printer using the Duet HTTP API.
For more information, visit https://github.com/Duet3D/RepRapFirmware/wiki/HTTP-requests.

**Minimum supported RepRapFirmware version: 3.5.4**

Communication with SimplyPrint.io is handled via the `simplyprint-ws-client <https://pypi.org/project/simplyprint-ws-client/>`_.

Camera support uses the ``simplyprint-ws-client`` camera system with an ``HttpCameraProtocol``
that handles both single JPEG snapshot endpoints and multipart MJPEG streams.

.. note::

   SimplyPrint maintains an upstream version of this connector at
   https://github.com/SimplyPrint/integration-duet3d.

------------
Status
------------

Supported features:

- Printer registration
- Printer status update
- Webcam snapshots and MJPEG livestreaming via the ``simplyprint-ws-client`` camera pool
- GCode receiving
- File downloading
- Printer control (start, pause, resume, cancel)
- Self upgrading via G-Code M997
- Device health update
- Bed leveling
- Filament sensor
- Duet auto discovery with tracking based on BoardID
- Leave a cookie on the printer to identify the printer in the future (``0:/sys/simplyprint-connector.json``)
- Grab the webcam URL from DWC settings file from the printer
- Webcam URL can be a snapshot endpoint or MJPEG stream

Missing features:

- PSU Control
- GCode Macros / Scripts [not yet implemented by SimplyPrint.io for Duet]
- GCode terminal [not yet implemented by SimplyPrint.io for Duet]
- Receive messages from Printer in SimplyPrint.io [not yet implemented by SimplyPrint.io for Duet]


------------
Installation
------------
Open an SSH session to your Simplyprint-connected device, such as a Raspberry Pi 4B.

.. code-block:: sh

    source <(curl -sSL https://raw.githubusercontent.com/Meltingplot/duet-simplyprint-connector/refs/heads/main/install.sh)


-----------------------------
Content of DuetConnector.json
-----------------------------

The default password for the Duet is `reprap`, even if the web interface does not require a login.

.. code-block:: json

    [
        {
            "id": null,
            "token": null,
            "name": null,
            "in_setup": true,
            "short_id": null,
            "public_ip": null,
            "unique_id": "...",
            "duet_uri": "http://192.168.1.0",
            "duet_password": "reprap",
            "duet_unique_id": "YOUR_DUET_BOARD_ID",
            "duet_name": "YOUR_DUET_NAME",
            "webcam_uri": "http://URI_OF_WEBCAM_SNAPSHOT_ENDPOINT/webcam"
        }
    ]


-----------------------------------------------
Usage of Meltingplot Duet Simplyprint Connector
-----------------------------------------------

- Create a configuration with `simplyprint autodiscover`
- *Optional* Edit the configuration file `~/.config/SimplyPrint/DuetConnector.json`
- Start the duet simplyprint connector with `simplyprint start` or `systemctl start simplyprint-connector.service`
- Add the printer via the Simplyprint.io web interface.
